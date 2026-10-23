//go:build !windows

package cli

import (
	"context"
	"io"
	"os"

	"golang.org/x/sys/unix"
)

type enrollmentTerminal struct {
	ctx context.Context
	fd  int
}

func (t enrollmentTerminal) transfer(buffer []byte, writing bool) (int, error) {
	event := int16(unix.POLLIN)
	if writing {
		event = unix.POLLOUT
	}
	for {
		if err := t.ctx.Err(); err != nil {
			return 0, err
		}
		ready, err := unix.Poll([]unix.PollFd{{Fd: int32(t.fd), Events: event}}, 50)
		if err == unix.EINTR {
			continue
		}
		if err != nil {
			return 0, err
		}
		if ready == 0 {
			continue
		}
		var n int
		if writing {
			n, err = unix.Write(t.fd, buffer)
		} else {
			n, err = unix.Read(t.fd, buffer)
		}
		if err == unix.EAGAIN || err == unix.EWOULDBLOCK || err == unix.EINTR {
			continue
		}
		if err != nil {
			return 0, err
		}
		if n == 0 && err == nil && !writing {
			err = io.EOF
		}
		return n, err
	}
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
