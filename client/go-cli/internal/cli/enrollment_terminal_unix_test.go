//go:build !windows

package cli

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

// A separate reporting pipe proves cleanup occurred while terminal output is
// still stopped. XON is sent only afterwards to let the Go test runner exit.
const enrollmentPTYHarness = `
import errno, os, pty, select, signal, sys, termios, time
gate_r, gate_w = os.pipe()
report_r, report_w = os.pipe()
os.set_inheritable(gate_r, True)
os.set_inheritable(report_w, True)
pid, master = pty.fork()
if pid == 0:
    os.close(gate_w)
    os.close(report_r)
    os.environ['ARBITER_TERMINAL_GATE'] = str(gate_r)
    os.environ['ARBITER_TERMINAL_REPORT'] = str(report_w)
    os.environ['ARBITER_TERMINAL_MODE'] = sys.argv[2]
    os.execv(sys.argv[1], [sys.argv[1], '-test.run=^TestEnrollmentTerminalPTYChild$'])
os.close(gate_r)
os.close(report_w)
reaped = False
try:
    attrs = termios.tcgetattr(master)
    attrs[0] |= termios.IXON
    termios.tcsetattr(master, termios.TCSANOW, attrs)
    if sys.argv[2] == 'output':
        os.write(master, b'\x13')
    os.write(gate_w, b'g')
    deadline, result, terminal = time.monotonic() + 5, b'', b''
    confirmed = False
    while b'SETTLED' not in result and time.monotonic() < deadline:
        readable, _, _ = select.select([master, report_r], [], [], 0.05)
        for fd in readable:
            try:
                data = os.read(fd, 4096)
            except OSError as e:
                if fd == master and e.errno == errno.EIO:
                    continue
                raise
            if fd == report_r:
                if not data:
                    raise AssertionError('terminal child exited before cleanup')
                result += data
            else:
                terminal += data
                if sys.argv[2] == 'confirm' and not confirmed and b'press Enter to continue: ' in terminal:
                    os.write(master, b'\n')
                    confirmed = True
                if len(terminal) > 8192:
                    raise AssertionError('unexpected terminal output volume')
                if sys.argv[2] == 'output' and b'synthetic-not-secret' in terminal:
                    raise AssertionError('terminal output was not stopped')
    if b'SETTLED' not in result:
        raise AssertionError('terminal owner did not expire and clean custody')
    if sys.argv[2] == 'output':
        os.write(master, b'\x11')
    while time.monotonic() < deadline:
        ended, status = os.waitpid(pid, os.WNOHANG)
        if ended:
            reaped = True
            if status != 0:
                raise AssertionError('terminal child failed after cleanup')
            print(sys.argv[2] + ': terminal outcome, cleanup and reacquisition passed')
            break
        select.select([master], [], [], 0.02)
        try:
            if select.select([master], [], [], 0)[0]:
                os.read(master, 4096)
        except OSError:
            pass
    if not reaped:
        raise AssertionError('terminal child did not finish')
finally:
    if not reaped:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    for fd in (master, gate_w, report_r):
        os.close(fd)
`

func TestEnrollmentTerminalPTY(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		if os.Getenv("GITHUB_ACTIONS") == "true" {
			t.Fatal("native PTY coverage requires Python 3")
		}
		t.Skip("PTY regression requires Python 3")
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"input", "output", "confirm"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, python, "-c", enrollmentPTYHarness, exe, mode)
			cmd.Env = append(os.Environ(), "ARBITER_TERMINAL_CUSTODY="+terminalCustodyPath(t))
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatal("bounded controlling-PTY regression failed", err, string(output))
			}
		})
	}
}

func TestEnrollmentTerminalPTYChild(t *testing.T) {
	value := os.Getenv("ARBITER_TERMINAL_GATE")
	if value == "" {
		t.Skip("controlling-PTY child only")
	}
	openFD := func(value string) *os.File {
		n, err := strconv.Atoi(value)
		if err != nil || n < 3 {
			t.Fatal("invalid test control descriptor")
		}
		f := os.NewFile(uintptr(n), "test control")
		t.Cleanup(func() { f.Close() })
		return f
	}
	gate, report := openFD(value), openFD(os.Getenv("ARBITER_TERMINAL_REPORT"))
	var ready [1]byte
	if _, err := gate.Read(ready[:]); err != nil {
		t.Fatal(err)
	}
	root := os.Getenv("ARBITER_TERMINAL_CUSTODY")
	home, _ := os.UserHomeDir()
	if filepath.Dir(root) != home || !strings.HasPrefix(filepath.Base(root), ".arbiter-terminal-test-") {
		t.Fatal("invalid private test directory")
	}
	in, out, err := openEnrollmentTerminal()
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	defer out.Close()
	expected := context.DeadlineExceeded
	if os.Getenv("ARBITER_TERMINAL_MODE") == "confirm" {
		expected = nil
	}
	checkTerminalCustodyOutcome(t, root, expected, func(ctx context.Context) error {
		return confirmEnrollment(ctx, in, out, "probe", "synthetic-not-secret")
	})
	fmt.Fprint(report, "SETTLED")
}

func TestEnrollmentTerminalRetry(t *testing.T) {
	for _, wouldBlock := range []error{unix.EAGAIN, unix.EWOULDBLOCK} {
		t.Run(wouldBlock.Error(), func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			attempts, waits := 0, 0
			stop := errors.New("stop backoff")
			_, err := retryEnrollmentTerminal(ctx, func() (int, error) {
				attempts++
				return 0, wouldBlock
			}, func() error { waits++; return stop })
			if !errors.Is(err, stop) || attempts != 1 || waits != 1 {
				t.Fatal("would-block did not wait before retry", attempts, waits, err)
			}
		})
	}
	t.Run("interrupted then would-block then success", func(t *testing.T) {
		attempts, waits := 0, 0
		n, err := retryEnrollmentTerminal(context.Background(), func() (int, error) {
			attempts++
			if attempts == 1 {
				return 0, unix.EINTR
			}
			if attempts == 2 {
				return 0, unix.EAGAIN
			}
			return 7, nil
		}, func() error { waits++; return nil })
		if n != 7 || err != nil || attempts != 3 || waits != 1 {
			t.Fatal("retry sequence lost result or backoff", n, err, attempts, waits)
		}
	})
	t.Run("cancelled before syscall", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, err := retryEnrollmentTerminal(ctx, func() (int, error) {
			t.Fatal("cancelled operation attempted syscall")
			return 0, nil
		}, func() error { t.Fatal("cancelled operation waited"); return nil })
		if !errors.Is(err, context.Canceled) {
			t.Fatal("cancellation lost", err)
		}
	})
	t.Run("cancelled while waiting", func(t *testing.T) {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		attempts := 0
		_, err := retryEnrollmentTerminal(ctx, func() (int, error) {
			attempts++
			return 0, unix.EAGAIN
		}, func() error { cancel(); return ctx.Err() })
		if !errors.Is(err, context.Canceled) || attempts != 1 {
			t.Fatal("backoff cancellation did not stop actual work", attempts, err)
		}
	})
}

func TestEnrollmentTerminalInvalidDescriptor(t *testing.T) {
	terminal := enrollmentTerminal{context.Background(), -1}
	for _, writing := range []bool{false, true} {
		n, err := terminal.transfer([]byte{'x'}, writing)
		if n != 0 || !errors.Is(err, unix.EBADF) {
			t.Fatal("syscall error returned a negative byte count", n, err)
		}
	}
}
