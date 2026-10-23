//go:build windows

package cli

import (
	"bytes"
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

func TestEnrollmentTerminalWindows(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"console", "early-read", "early-write"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, exe, "-test.run=^TestEnrollmentTerminalWindowsChild$")
			cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_CONSOLE}
			cmd.Env = append(os.Environ(), "ARBITER_TERMINAL_WINDOWS_CHILD="+mode,
				"ARBITER_TERMINAL_CUSTODY="+terminalCustodyPath(t))
			output, err := cmd.CombinedOutput()
			if bytes.Contains(output, []byte("synthetic-not-secret")) {
				t.Fatal("protected prompt reached redirected stdout/stderr")
			}
			if err != nil {
				t.Fatal("native Windows terminal regression failed", err, string(output))
			}
		})
	}
}

func TestEnrollmentTerminalWindowsChild(t *testing.T) {
	mode := os.Getenv("ARBITER_TERMINAL_WINDOWS_CHILD")
	if mode == "" {
		t.Skip("private console child only")
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
	if mode == "console" {
		events, err := os.OpenFile("CONIN$", os.O_RDWR, 0)
		if err != nil {
			t.Fatal(err)
		}
		defer events.Close()
		// A focus event signals console readiness but supplies no character.
		var event struct {
			Kind uint16
			Pad  uint16
			Data [16]byte
		}
		event.Kind, event.Data[0] = 0x10, 1 // FOCUS_EVENT, bSetFocus=TRUE
		var written uint32
		writeEvent := windows.NewLazySystemDLL("kernel32.dll").NewProc("WriteConsoleInputW")
		ok, _, err := writeEvent.Call(events.Fd(), uintptr(unsafe.Pointer(&event)), 1, uintptr(unsafe.Pointer(&written)))
		if ok == 0 || written != 1 {
			t.Fatal("could not queue non-character console input", err)
		}
		checkTerminalCustody(t, root, func(ctx context.Context) error {
			return confirmEnrollment(ctx, in, out, "probe", "synthetic-not-secret")
		})
		return
	}
	var read, write windows.Handle
	if err := windows.CreatePipe(&read, &write, nil, 4096); err != nil {
		t.Fatal(err)
	}
	reader, writer := os.NewFile(uintptr(read), "test pipe read"), os.NewFile(uintptr(write), "test pipe write")
	defer reader.Close()
	defer writer.Close()
	if mode == "early-write" {
		var outputSize, inputSize uint32
		if err := windows.GetNamedPipeInfo(read, nil, &outputSize, &inputSize, nil); err != nil {
			t.Fatal(err)
		}
		size := max(outputSize, inputSize)
		if size == 0 || size > 1<<20 {
			t.Fatal("unexpected private pipe buffer size")
		}
		if _, err := writer.Write(make([]byte, size)); err != nil {
			t.Fatal(err)
		}
		out = writer
	} else if mode == "early-read" {
		in = reader
	} else {
		t.Fatal("unknown terminal test mode")
	}
	checkTerminalCustody(t, root, func(ctx context.Context) error {
		return enrollmentTerminalIO(ctx, in, out, func(input io.Reader, output io.Writer) error {
			// Force submission after the first cancellation request. One-shot
			// cancellation would miss this read/write and leave custody stuck.
			<-ctx.Done()
			time.Sleep(30 * time.Millisecond)
			if mode == "early-write" {
				_, err := output.Write([]byte{1})
				return err
			}
			var one [1]byte
			_, err := input.Read(one[:])
			return err
		})
	})
}
