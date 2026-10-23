//go:build windows

package cli

import (
	"context"
	"io"
	"os"
	"time"

	"golang.org/x/sys/windows"
)

func enrollmentTerminalIO(ctx context.Context, in, out *os.File, action func(io.Reader, io.Writer) error) error {
	input, output := windows.Handle(in.Fd()), windows.Handle(out.Fd())
	returned, cancelled := make(chan struct{}), make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		defer close(cancelled)
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-returned:
				return
			default:
			}
			// Cancellation can precede I/O submission. Keep cancelling only
			// these owned handles until the actual read/write owner returns.
			windows.CancelIoEx(input, nil)
			windows.CancelIoEx(output, nil)
			select {
			case <-returned:
				return
			case <-ticker.C:
			}
		}
	})
	defer func() {
		close(returned)
		if !stop() {
			<-cancelled
		}
	}()
	if err := ctx.Err(); err != nil {
		return err
	}
	err := action(in, out)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}
