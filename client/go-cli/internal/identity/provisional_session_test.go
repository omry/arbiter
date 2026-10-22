package identity

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func assertOnlyProvisionalLock(t *testing.T, root string) {
	t.Helper()
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 1 || entries[0].Name() != provisionalLockName {
		t.Fatal("request files remain after cleanup", entries, err)
	}
}

func TestProvisionalSessionDurableBeforeExchangeAndCleanup(t *testing.T) {
	for _, failed := range []bool{false, true} {
		t.Run(fmt.Sprint(failed), func(t *testing.T) {
			root := filepath.Join(custodyTestDir(t), "state")
			fault := errors.New("exchange failed")
			calls := 0
			err := RunProvisional(context.Background(), root, "https://localhost", "Agent Smith",
				func(ctx context.Context, p *Provisional, secret PairingSecret) error {
					calls++
					if _, err := LoadProvisional(root, p.record.Selector); err != nil {
						t.Fatal("advertisement preceded recoverable private save", err)
					}
					raw, _ := os.ReadFile(filepath.Join(root, p.record.Selector+".json"))
					if len(secret.Encoded()) != 43 || bytes.Contains(raw, []byte(secret.Encoded())) {
						t.Fatal("pairing secret missing or persisted")
					}
					deadline, ok := ctx.Deadline()
					if !ok || !deadline.Equal(p.record.CreatedAt.Add(10*time.Minute)) {
						t.Fatal("deadline was not bound to original creation")
					}
					if err := CleanupProvisional(context.Background(), root); !errors.Is(err, ErrProvisionalBusy) {
						t.Fatal("cleanup entered active enrollment", err)
					}
					if failed {
						return fault
					}
					return nil
				})
			if calls != 1 || (failed && !errors.Is(err, fault)) || (!failed && err != nil) {
				t.Fatal("exchange outcome was lost", err)
			}
			assertOnlyProvisionalLock(t, root)
			if err := CleanupProvisional(context.Background(), root); err != nil {
				t.Fatal("lock was not released", err)
			}
		})
	}
}

func TestProvisionalSessionCancellationRetainsActualOwner(t *testing.T) {
	for _, expired := range []bool{false, true} {
		t.Run(fmt.Sprint(expired), func(t *testing.T) {
			root := filepath.Join(custodyTestDir(t), "state")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			entered, cancelled, release := make(chan struct{}), make(chan struct{}), make(chan struct{})
			result := make(chan error, 1)
			lifetime := 10 * time.Minute
			if expired {
				lifetime = 5 * time.Second
			}
			go func() {
				result <- runProvisional(ctx, root, "https://localhost", "Agent Smith", lifetime,
					func(ctx context.Context, _ *Provisional, _ PairingSecret) error {
						close(entered)
						<-ctx.Done()
						close(cancelled)
						<-release // Simulate underlying work that has not actually stopped.
						return nil
					})
			}()
			select {
			case <-entered:
			case err := <-result:
				t.Fatal("exchange did not start", err)
			case <-time.After(10 * time.Second):
				t.Fatal("exchange startup stalled")
			}
			if !expired {
				cancel()
			}
			<-cancelled
			if err := CleanupProvisional(context.Background(), root); !errors.Is(err, ErrProvisionalBusy) {
				close(release)
				t.Fatal("cancelled underlying work lost ownership", err)
			}
			close(release)
			want := context.Canceled
			if expired {
				want = context.DeadlineExceeded
			}
			if err := <-result; !errors.Is(err, want) {
				t.Fatal("late successful return hid cancellation/expiry", err)
			}
			assertOnlyProvisionalLock(t, root)
		})
	}
}

func TestProvisionalSessionDoesNotAdvertiseFailedSave(t *testing.T) {
	root := filepath.Join(custodyTestDir(t), "state")
	called := false
	err := runProvisional(context.Background(), root, "https://localhost", "Agent Smith", -time.Second,
		func(context.Context, *Provisional, PairingSecret) error { called = true; return nil })
	if called || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("failed private save advertised keys", err)
	}
	assertOnlyProvisionalLock(t, root)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	absent := filepath.Join(custodyTestDir(t), "absent")
	if err := CleanupProvisional(ctx, absent); !errors.Is(err, context.Canceled) {
		t.Fatal("ignored cancellation before mutation", err)
	}
	if _, err := os.Lstat(absent); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("cancelled cleanup created state")
	}
}

func TestProvisionalCleanupRejectsAliasedLockAndReportsConsumerCleanupFailure(t *testing.T) {
	root := filepath.Join(custodyTestDir(t), "state")
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(root, "lock-alias")
	if err := os.Link(filepath.Join(root, provisionalLockName), alias); err != nil {
		t.Fatal(err)
	}
	if err := CleanupProvisional(context.Background(), root); err == nil {
		t.Fatal("accepted multiply-linked ownership file")
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	fault := errors.New("consumer failure")
	var selector string
	err := RunProvisional(context.Background(), root, "https://localhost", "Agent Smith",
		func(_ context.Context, p *Provisional, _ PairingSecret) error {
			selector = p.record.Selector
			if err := os.Link(filepath.Join(root, selector+".json"), alias); err != nil {
				t.Fatal(err)
			}
			return fault
		})
	if !errors.Is(err, fault) || !errors.Is(err, errCustody) {
		t.Fatal("cleanup failure hid consumer error or reported success", err)
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, selector); err != nil {
		t.Fatal("failed cleanup destroyed recoverable state", err)
	}
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal("failed cleanup retained ownership forever", err)
	}
	assertOnlyProvisionalLock(t, root)
}

func TestProvisionalCleanupBoundsAndPreservesUnknownState(t *testing.T) {
	root, p, _ := storedProvisional(t)
	unknown := filepath.Join(root, "enrolled-state")
	if err := os.WriteFile(unknown, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, pending, _ := custodyNames(root, p.record.Selector)
	if err := os.Link(filepath.Join(root, p.record.Selector+".json"), pending); err != nil {
		t.Fatal(err)
	}
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal("startup could not reconcile crash publication", err)
	}
	if raw, err := os.ReadFile(unknown); err != nil || string(raw) != "keep" {
		t.Fatal("cleanup changed unknown state")
	}
	if err := SaveProvisional(context.Background(), root, p); err != nil {
		t.Fatal(err)
	}
	final := filepath.Join(root, p.record.Selector+".json")
	if err := os.WriteFile(final, []byte(`{"prepared":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := CleanupProvisional(context.Background(), root); err == nil {
		t.Fatal("cleanup treated an unrecognized record as disposable")
	}
	if raw, _ := os.ReadFile(final); string(raw) != `{"prepared":true}` {
		t.Fatal("cleanup removed an unrecognized complete record")
	}
	if err := os.Remove(final); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < provisionalCleanupBatch+2; i++ {
		p, _ := provisional(t)
		_, pending, _ := custodyNames(root, p.record.Selector)
		if err := os.WriteFile(pending, []byte("torn"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := CleanupProvisional(context.Background(), root); !errors.Is(err, ErrProvisionalCleanupIncomplete) {
		t.Fatal("unbounded cleanup or hidden remaining work", err)
	}
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal("explicit cleanup retry did not make progress", err)
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 2 {
		t.Fatal("cleanup leaked staging or deleted unknown file", entries)
	}
}

func TestProvisionalCleanupNoProgressIsActionable(t *testing.T) {
	root := filepath.Join(custodyTestDir(t), "state")
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal(err)
	}
	var names []string
	for i := 0; i < provisionalCleanupBatch+1; i++ {
		name := fmt.Sprintf("unrelated-%03d", i)
		if err := os.WriteFile(filepath.Join(root, name), []byte("preserve"), 0o600); err != nil {
			t.Fatal(err)
		}
		names = append(names, name)
	}
	// Every possible first window is unrelated: do not promise a useful retry.
	for attempt := 0; attempt < 2; attempt++ {
		if err := CleanupProvisional(context.Background(), root); !errors.Is(err, ErrProvisionalUnexpectedContents) {
			t.Fatal("no-progress directory returned a retryable result", err)
		}
	}
	p, _ := provisional(t)
	if err := SaveProvisional(context.Background(), root, p); err != nil {
		t.Fatal(err)
	}
	_, lock, err := lockProvisional(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	// Model a filesystem window preceding the owned file without depending on
	// platform-specific directory ordering. The actual files and deletion are real.
	if err := cleanupProvisionalBatch(context.Background(), root, names[:provisionalCleanupBatch], true); !errors.Is(err, ErrProvisionalUnexpectedContents) {
		t.Fatal("unseen provisional file caused a false progress claim", err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err != nil {
		t.Fatal("cleanup changed the unseen provisional file", err)
	}
	for _, name := range names {
		if raw, err := os.ReadFile(filepath.Join(root, name)); err != nil || string(raw) != "preserve" {
			t.Fatal("cleanup changed unrelated files", err)
		}
	}
}

func TestProvisionalSessionProcessOwner(t *testing.T) {
	if root := os.Getenv("ARBITER_TEST_SESSION_DIRECTORY"); root != "" {
		err := RunProvisional(context.Background(), root, "https://localhost", "Agent Smith",
			func(_ context.Context, p *Provisional, _ PairingSecret) error {
				fmt.Println("SESSION_READY=" + p.record.Selector)
				var input [1]byte
				_, err := os.Stdin.Read(input[:])
				return err
			})
		if err != nil {
			t.Fatal("session helper failed")
		}
		return
	}
	root := filepath.Join(custodyTestDir(t), "state")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.CommandContext(ctx, executable, "-test.run=^TestProvisionalSessionProcessOwner$", "-test.v")
	cmd.Env = append(os.Environ(), "ARBITER_TEST_SESSION_DIRECTORY="+root)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer stdin.Close()
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var diagnostics bytes.Buffer
	cmd.Stderr = &diagnostics
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cmd.Process.Kill(); cmd.Wait() })
	// Read a public selector only. No private material/PSK crosses the test pipe.
	var selector string
	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		if strings.HasPrefix(scanner.Text(), "SESSION_READY=") {
			selector = strings.TrimPrefix(scanner.Text(), "SESSION_READY=")
			break
		}
	}
	if selector == "" {
		cmd.Process.Kill()
		cmd.Wait()
		t.Fatal("helper did not become ready", diagnostics.String(), scanner.Err())
	}
	if err := CleanupProvisional(context.Background(), root); !errors.Is(err, ErrProvisionalBusy) {
		t.Fatal("another process could clean active keys", err)
	}
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Wait(); err == nil {
		t.Fatal("helper was not killed")
	}
	if _, err := LoadProvisional(root, selector); err != nil {
		t.Fatal("killed owner's recoverable file was lost", err)
	}
	if err := CleanupProvisional(context.Background(), root); err != nil {
		t.Fatal("restart did not recover ownership and clean stale keys", err)
	}
	assertOnlyProvisionalLock(t, root)
}
