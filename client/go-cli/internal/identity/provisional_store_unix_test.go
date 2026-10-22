//go:build !windows

package identity

import (
	"context"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func custodyTestDir(t *testing.T) string { t.Helper(); return t.TempDir() }

func TestUnixCustodyParentSyncFailureAndExistingRootRetry(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses the directory-read permission failure fixture")
	}
	parent := t.TempDir()
	root := filepath.Join(parent, "state")
	p, _ := provisional(t)
	defer os.Chmod(parent, 0o700)
	err := saveProvisional(context.Background(), root, p, func(phase string) error {
		if phase == "published" {
			// Search still permits opening the known root, but the owning
			// parent cannot be opened for its required directory flush.
			return os.Chmod(parent, 0o300)
		}
		return nil
	})
	if !os.IsPermission(err) {
		t.Fatal("parent flush failure was reported as a successful save", err)
	}
	if err := os.Chmod(parent, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err != nil {
		t.Fatal("published material was not recoverable after flush failure", err)
	}
	other, _ := provisional(t)
	if err := SaveProvisional(context.Background(), root, other); err != nil {
		t.Fatal("existing-root retry could not complete durability", err)
	}
	if _, err := LoadProvisional(root, other.record.Selector); err != nil {
		t.Fatal("existing-root retry did not preserve new material", err)
	}
}

func TestUnixCustodyRejectsUnsafePathsAndPermissions(t *testing.T) {
	root, p, _ := storedProvisional(t)
	final, pending, _ := custodyNames(root, p.record.Selector)
	if err := os.Chmod(final, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("loaded publicly readable keys")
	}
	os.Chmod(final, 0o600)
	alias := filepath.Join(root, "alias")
	if err := os.Link(final, alias); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("loaded multiply linked keys")
	}
	if err := RemoveProvisional(root, p.record.Selector); err == nil {
		t.Fatal("removed a record with an unknown hard-link alias")
	}
	os.Remove(alias)
	if err := os.Link(final, pending); err != nil {
		t.Fatal(err)
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal("could not recover interrupted link publication", err)
	}
	outside := filepath.Join(t.TempDir(), "outside")
	os.WriteFile(outside, []byte("unchanged"), 0o600)
	if err := os.Symlink(outside, final); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("followed a file symlink")
	}
	if err := RemoveProvisional(root, p.record.Selector); err == nil {
		t.Fatal("followed a symlink during removal")
	}
	os.Remove(final)
	if err := syscall.Mkfifo(final, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("loaded a nonregular file")
	}
	os.Remove(final)
	os.Chmod(root, 0o777)
	if err := SaveProvisional(context.Background(), root, p); err == nil {
		t.Fatal("silently accepted or repaired an unsafe directory")
	}
	info, _ := os.Stat(root)
	if info.Mode().Perm() != 0o777 {
		t.Fatal("rewrote existing permissions")
	}
	os.Chmod(root, 0o700)
	link := filepath.Join(filepath.Dir(root), "directory-alias")
	os.Symlink(root, link)
	if err := SaveProvisional(context.Background(), link, p); err == nil {
		t.Fatal("followed final-directory symlink")
	}
	f, err := nativeOpenCustody(pending, false, true)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	flags, _, errno := syscall.Syscall(syscall.SYS_FCNTL, f.Fd(), syscall.F_GETFD, 0)
	if errno != 0 || flags&syscall.FD_CLOEXEC == 0 {
		t.Fatal("private file descriptor can be inherited by subprocesses")
	}
}
