package cli

import (
	"bufio"
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/identity"
)

func terminalCustodyPath(t *testing.T) string {
	t.Helper()
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		t.Fatal(err)
	}
	// RunProvisional creates this absent path with its native private ACL/mode.
	path := filepath.Join(home, fmt.Sprintf(".arbiter-terminal-test-%x", id))
	t.Cleanup(func() { os.RemoveAll(path) })
	return path
}

func checkTerminalCustody(t *testing.T, root string, operation func(context.Context) error) {
	t.Helper()
	err := identity.RunProvisional(context.Background(), root, "https://localhost", "terminal probe",
		func(ctx context.Context, _ *identity.Provisional, _ identity.PairingSecret) error {
			ctx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
			defer cancel()
			start := time.Now()
			err := operation(ctx)
			if !errors.Is(err, context.DeadlineExceeded) || time.Since(start) > 2*time.Second {
				return fmt.Errorf("terminal did not settle on expiry: %w", err)
			}
			return err
		})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("expiry outcome lost", err)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 1 || entries[0].Name() != ".enrollment.lock" {
		t.Fatal("provisional files remain", err)
	}
	if err := identity.RunProvisional(context.Background(), root, "https://localhost", "reacquire",
		func(context.Context, *identity.Provisional, identity.PairingSecret) error { return nil }); err != nil {
		t.Fatal("custody was not released", err)
	}
}

func TestEnrollmentTerminalBoundedConfirmation(t *testing.T) {
	for _, value := range []string{"\n", strings.Repeat("x", 255) + "\n", strings.Repeat("x", 256) + "\n", ""} {
		t.Run(fmt.Sprint(len(value)), func(t *testing.T) {
			in, feed, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer in.Close()
			if _, err := feed.WriteString(value); err != nil {
				t.Fatal(err)
			}
			feed.Close()
			out, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer out.Close()
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			err = confirmEnrollment(ctx, in, out, "test", "synthetic-not-secret")
			expected := len(value) > 0 && len(value) <= 256
			if (err == nil) != expected || (!expected && len(value) > 256 && !errors.Is(err, bufio.ErrBufferFull)) {
				t.Fatal("confirmation boundary changed", err)
			}
		})
	}
}
