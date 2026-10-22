package identity

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func storedProvisional(t *testing.T) (string, *Provisional, PairingSecret) {
	t.Helper()
	root := filepath.Join(custodyTestDir(t), "state")
	p, secret := provisional(t)
	if err := SaveProvisional(context.Background(), root, p); err != nil {
		t.Fatal(err)
	}
	return root, p, secret
}

func TestProvisionalPrivateStorage(t *testing.T) {
	root, p, secret := storedProvisional(t)
	loaded, err := LoadProvisional(root, p.record.Selector)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := p.Public()
	b, _ := loaded.Public()
	if !reflect.DeepEqual(a, b) {
		t.Fatal("private reload changed keys or request metadata")
	}
	final, pending, _ := custodyNames(root, p.record.Selector)
	raw, err := os.ReadFile(final)
	if err != nil || bytes.Contains(raw, []byte(secret.Encoded())) {
		t.Fatal("private file contains pairing secret or cannot be read")
	}
	if err := SaveProvisional(context.Background(), root, p); !errors.Is(err, os.ErrExist) {
		t.Fatal("existing request was not preserved")
	}
	unchanged, _ := os.ReadFile(final)
	if !bytes.Equal(raw, unchanged) {
		t.Fatal("retry overwrote private material")
	}
	if _, err := os.Stat(pending); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("successful save left staging")
	}
	for _, bad := range []string{"", "../outside", strings.Repeat("A", 21) + "B"} {
		if _, err := LoadProvisional(root, bad); err == nil {
			t.Fatal("accepted invalid selector")
		}
		if err := RemoveProvisional(root, bad); err == nil {
			t.Fatal("removed an invalid selector")
		}
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("removed request remains usable")
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal("explicit removal was not idempotent")
	}
}

func TestPrivateStorageRejectsCorruptionAndPendingCollision(t *testing.T) {
	root, p, _ := storedProvisional(t)
	final, pending, _ := custodyNames(root, p.record.Selector)
	other, _ := provisional(t)
	raw, _ := other.EncodePrivate()
	for _, bad := range [][]byte{raw, bytes.Repeat([]byte("x"), MaxProvisionalBytes+1), []byte("unfinished")} {
		if err := os.WriteFile(final, bad, 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := LoadProvisional(root, p.record.Selector); err == nil {
			t.Fatal("loaded wrong-request, oversized or torn private state")
		}
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal(err)
	}
	f, err := nativeOpenCustody(pending, false, true)
	if err != nil {
		t.Fatal(err)
	}
	f.Write([]byte("incomplete"))
	f.Close()
	if err := SaveProvisional(context.Background(), root, p); !errors.Is(err, os.ErrExist) {
		t.Fatal("stale staging was overwritten or treated as publication")
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal("incomplete private staging could not be explicitly removed", err)
	}
}

func TestPrivateStorageFailureAndCancellationBoundaries(t *testing.T) {
	p, _ := provisional(t)
	if err := SaveProvisional(context.Background(), "", p); err == nil {
		t.Fatal("empty directory silently selected the current working directory")
	}
	for _, phase := range []string{"written", "flushed", "published"} {
		t.Run(phase, func(t *testing.T) {
			root := filepath.Join(custodyTestDir(t), "state")
			p, _ := provisional(t)
			fault := errors.New("injected publication boundary failure")
			err := saveProvisional(context.Background(), root, p, func(at string) error {
				if at == phase {
					return fault
				}
				return nil
			})
			if !errors.Is(err, fault) {
				t.Fatal("publication boundary failure was swallowed")
			}
			_, err = LoadProvisional(root, p.record.Selector)
			if (err == nil) != (phase == "published") {
				t.Fatal("failure exposed partial data or lost a recoverable publication")
			}
			if err := RemoveProvisional(root, p.record.Selector); err != nil {
				t.Fatal(err)
			}
		})
	}
	root := filepath.Join(custodyTestDir(t), "state")
	p, _ = provisional(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := SaveProvisional(ctx, root, p); !errors.Is(err, context.Canceled) {
		t.Fatal("ignored cancellation before mutation")
	}
	if _, err := os.Lstat(root); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("cancelled save created state")
	}
	ctx, cancel = context.WithCancel(context.Background())
	err := saveProvisional(ctx, root, p, func(at string) error {
		if at == "flushed" {
			cancel()
		}
		return nil
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatal("ignored cancellation before publication")
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 0 {
		t.Fatal("cancelled save left published or staging material")
	}
}

func TestPrivateStorageConcurrentSaveDoesNotOverwrite(t *testing.T) {
	root := filepath.Join(custodyTestDir(t), "state")
	p, _ := provisional(t)
	ready, release := make(chan struct{}), make(chan struct{})
	releaseNow := sync.OnceFunc(func() { close(release) })
	defer releaseNow()
	result := make(chan error, 1)
	go func() {
		result <- saveProvisional(context.Background(), root, p, func(at string) error {
			if at == "flushed" {
				close(ready)
				<-release
			}
			return nil
		})
	}()
	select {
	case <-ready:
	case err := <-result:
		t.Fatal(err)
	case <-time.After(5 * time.Second):
		t.Fatal("save did not reach the publication boundary")
	}
	if err := SaveProvisional(context.Background(), root, p); !errors.Is(err, os.ErrExist) {
		t.Fatal("concurrent writer changed existing staging")
	}
	releaseNow()
	if err := <-result; err != nil {
		t.Fatal("winning writer could not finish", err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err != nil {
		t.Fatal("winning publication could not be reloaded", err)
	}
}

func TestCustodyCrashHelper(t *testing.T) {
	phase := os.Getenv("ARBITER_TEST_CUSTODY_PHASE")
	if phase == "" {
		return
	}
	p, _ := provisional(t)
	fmt.Print(p.record.Selector) // Public selector only, never private material.
	err := saveProvisional(context.Background(), os.Getenv("ARBITER_TEST_CUSTODY_ROOT"), p, func(at string) error {
		if at == phase {
			os.Exit(71)
		}
		return nil
	})
	t.Fatal("crash checkpoint not reached", err)
}

func TestPrivateStorageProcessCrashRecovery(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, phase := range []string{"written", "flushed", "published"} {
		root := filepath.Join(custodyTestDir(t), "state")
		cmd := exec.Command(executable, "-test.run=^TestCustodyCrashHelper$")
		cmd.Env = append(os.Environ(), "ARBITER_TEST_CUSTODY_PHASE="+phase, "ARBITER_TEST_CUSTODY_ROOT="+root)
		output, err := cmd.Output()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 71 {
			t.Fatal("private-storage crash helper did not execute")
		}
		selector := strings.TrimSpace(string(output))
		_, err = LoadProvisional(root, selector)
		if (err == nil) != (phase == "published") {
			t.Fatal("crash exposed partial data or made committed material unrecoverable")
		}
		if err := RemoveProvisional(root, selector); err != nil {
			t.Fatal("crash staging could not be explicitly recovered", err)
		}
		entries, _ := os.ReadDir(root)
		if len(entries) != 0 {
			t.Fatal("explicit recovery left private junk")
		}
	}
}
