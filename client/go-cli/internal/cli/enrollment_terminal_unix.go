//go:build !windows

package cli

import (
	"context"
	"io"
	"os"
	"time"

	"golang.org/x/sys/unix"
)

type enrollmentTerminal struct {
	ctx context.Context
	fd  int
}

// Retrying a nonblocking device must wait on would-block, even on platforms
// where poll does not support /dev/tty. All attempts stay in the caller.
func retryEnrollmentTerminal(ctx context.Context, attempt func() (int, error), backoff func() error) (int, error) {
	for {
		if err := ctx.Err(); err != nil {
			return 0, err
		}
		n, err := attempt()
		if err == unix.EINTR {
			continue
		}
		if err == unix.EAGAIN || err == unix.EWOULDBLOCK {
			if err := backoff(); err != nil {
				return 0, err
			}
			continue
		}
		return n, err
	}
}

func (t enrollmentTerminal) transfer(buffer []byte, writing bool) (int, error) {
	n, err := retryEnrollmentTerminal(t.ctx, func() (int, error) {
		if writing {
			return unix.Write(t.fd, buffer)
		}
		return unix.Read(t.fd, buffer)
	}, func() error {
		timer := time.NewTimer(50 * time.Millisecond)
		defer timer.Stop()
		select {
		case <-t.ctx.Done():
			return t.ctx.Err()
		case <-timer.C:
			return nil
		}
	})
	if err != nil {
		return 0, err
	}
	if n == 0 && err == nil && !writing {
		err = io.EOF
	}
	return n, err
}

func (t enrollmentTerminal) Read(b []byte) (int, error) { return t.transfer(b, false) }
func (t enrollmentTerminal) Write(b []byte) (int, error) {
	n := 0
	for n < len(b) {
		written, err := t.transfer(b[n:], true)
		n += written
		if err != nil {
			return n, err
		}
		if written == 0 {
			return n, io.ErrShortWrite
		}
	}
	return n, nil
}

func enrollmentTerminalIO(ctx context.Context, in, out *os.File, action func(io.Reader, io.Writer) error) error {
	input, output := int(in.Fd()), int(out.Fd())
	// Fd can switch a Go file back to blocking mode. No Fd calls follow this.
	for _, fd := range []int{input, output} {
		if err := unix.SetNonblock(fd, true); err != nil {
			return err
		}
	}
	err := action(enrollmentTerminal{ctx, input}, enrollmentTerminal{ctx, output})
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}
