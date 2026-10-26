package identity

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestPromotionCrashHelper(t *testing.T) {
	phase := os.Getenv("ARBITER_TEST_PROMOTION_PHASE")
	if phase == "" {
		return
	}
	material, prepared, err := loadPrepared(os.Getenv("ARBITER_TEST_PROMOTION_SOURCE"), os.Getenv("ARBITER_TEST_PROMOTION_SELECTOR"), time.Now(), true)
	if err != nil {
		t.Fatal(err)
	}
	private, _ := material.EncodePrivate()
	bundle, _ := json.Marshal(preparedPrivateRecord{privateWarning, 1, private, prepared.raw})
	err = savePrivateRecord(context.Background(), os.Getenv("ARBITER_TEST_PROMOTION_DEST"), material.record.Selector, bundle, func(at string) error {
		if at == phase {
			fmt.Println("READY")
			time.Sleep(time.Minute) // Parent kills this process without cleanup.
		}
		return nil
	})
	t.Fatal("crash checkpoint not reached", err)
}

// Called only after the real Python TLS peer supplied an authenticated receipt.
func testPromotionRecovery(t *testing.T, receipt *EnrollmentReceipt, source string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	base, selector := filepath.Dir(source), receipt.material.record.Selector
	owner := filepath.Join(base, "provisional")
	for _, phase := range []string{"written", "flushed", "published"} {
		destination := filepath.Join(base, phase)
		err := WithEnrollmentOwner(ctx, owner, func() error {
			cmd := exec.CommandContext(ctx, executable, "-test.run=^TestPromotionCrashHelper$")
			cmd.Env = append(os.Environ(), "ARBITER_TEST_PROMOTION_PHASE="+phase,
				"ARBITER_TEST_PROMOTION_SOURCE="+source, "ARBITER_TEST_PROMOTION_SELECTOR="+selector,
				"ARBITER_TEST_PROMOTION_DEST="+destination)
			output, err := cmd.StdoutPipe()
			if err != nil {
				return err
			}
			if err := cmd.Start(); err != nil {
				return err
			}
			line, readErr := bufio.NewReader(output).ReadString('\n')
			killErr := cmd.Process.Kill()
			waitErr := cmd.Wait()
			if readErr != nil || line != "READY\n" || killErr != nil || waitErr == nil {
				t.Fatalf("%s crash did not execute: %q %v %v %v", phase, line, readErr, killErr, waitErr)
			}
			final, pending, _ := custodyNames(destination, selector)
			_, finalErr := os.Lstat(final)
			if (finalErr == nil) != (phase == "published") {
				t.Fatal("unexpected publication boundary", phase, finalErr)
			}
			if phase != "published" {
				if _, err := os.Lstat(pending); err != nil {
					t.Fatal("missing killed writer staging", err)
				}
			} else if err := os.Link(final, pending); err != nil {
				t.Fatal("interrupted two-link publication fixture", err)
			}
			for range 2 {
				if err := receipt.Promote(ctx, destination); err != nil {
					return err
				}
			}
			_, saved, err := loadPrepared(destination, selector, time.Now(), true)
			if err != nil || !bytes.Equal(saved.raw, receipt.prepared.raw) {
				t.Fatal("exact committed result not recovered", err)
			}
			if _, err := os.Lstat(pending); !os.IsNotExist(err) {
				t.Fatal("staging remains after recovery", err)
			}
			return nil
		})
		if err != nil {
			t.Fatal("recover killed writer", phase, err)
		}
	}
	for _, mode := range []string{"different-final", "directory", "hardlink", "symlink", "permissions"} {
		if runtime.GOOS == "windows" && (mode == "symlink" || mode == "permissions") {
			continue // Native ACL/reparse guards have separate Windows tests.
		}
		t.Run(mode, func(t *testing.T) {
			destination := filepath.Join(base, mode)
			root, dir, err := custodyRoot(destination, true)
			if err != nil {
				t.Fatal(err)
			}
			dir.Close()
			final, pending, _ := custodyNames(root, selector)
			marker := []byte("preserve existing private state")
			path := pending
			if mode == "different-final" {
				path = final
			}
			if mode == "directory" {
				err = os.Mkdir(pending, 0700)
			} else if mode == "hardlink" || mode == "symlink" {
				target := filepath.Join(root, "other")
				err = os.WriteFile(target, marker, 0600)
				if err == nil && mode == "hardlink" {
					err = os.Link(target, pending)
				} else if err == nil {
					err = os.Symlink(target, pending)
				}
			} else {
				err = os.WriteFile(path, marker, 0600)
				if mode == "permissions" && err == nil {
					err = os.Chmod(path, 0644)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			if err := WithEnrollmentOwner(ctx, owner, func() error { return receipt.Promote(ctx, destination) }); err == nil {
				t.Fatal("unsafe/different existing state accepted")
			}
			if mode == "directory" {
				if info, err := os.Lstat(pending); err != nil || !info.IsDir() {
					t.Fatal("existing directory changed", err)
				}
			} else if saved, err := os.ReadFile(path); err != nil || !bytes.Equal(saved, marker) {
				t.Fatal("existing state changed", err)
			}
		})
	}
}
