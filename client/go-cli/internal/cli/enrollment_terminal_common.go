package cli

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"runtime"

	"golang.org/x/term"
)

// Enrollment secrets have no redirected-stream, argument or environment fallback.
var openEnrollmentTerminal = func() (*os.File, *os.File, error) {
	input, output := "/dev/tty", "/dev/tty"
	if runtime.GOOS == "windows" {
		input, output = "CONIN$", "CONOUT$"
	}
	in, err := os.Open(input)
	if err != nil {
		return nil, nil, err
	}
	outputFlags := os.O_WRONLY
	if runtime.GOOS == "windows" {
		outputFlags = os.O_RDWR
	}
	out, err := os.OpenFile(output, outputFlags, 0)
	if err != nil {
		in.Close()
		return nil, nil, err
	}
	if !term.IsTerminal(int(in.Fd())) || !term.IsTerminal(int(out.Fd())) {
		in.Close()
		out.Close()
		return nil, nil, fmt.Errorf("terminal required")
	}
	return in, out, nil
}

// The caller owns both files until this returns. Actual I/O and cancellation
// finish before returning; a provisional custody owner can then clean up safely.
func confirmEnrollment(ctx context.Context, in, out *os.File, selector, secret string) error {
	return enrollmentTerminalIO(ctx, in, out, func(reader io.Reader, writer io.Writer) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if _, err := fmt.Fprintf(writer, "Pairing version: 1\nRequest: %s\nSecret (only enter at your server's local approval prompt): %s\nAfter operator approval, press Enter to continue: ", selector, secret); err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		_, err := bufio.NewReaderSize(reader, 256).ReadSlice('\n')
		return err
	})
}
