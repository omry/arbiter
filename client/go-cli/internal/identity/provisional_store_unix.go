//go:build !windows

package identity

import (
	"os"
	"path/filepath"
	"syscall"
)

func nativeOpenCustody(path string, directory, create bool) (*os.File, error) {
	flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK | syscall.O_CLOEXEC
	if directory {
		flags |= syscall.O_DIRECTORY
	}
	if create {
		flags = syscall.O_WRONLY | syscall.O_CREAT | syscall.O_EXCL | syscall.O_NOFOLLOW | syscall.O_CLOEXEC
	}
	fd, err := syscall.Open(path, flags, 0o600)
	if err != nil {
		return nil, &os.PathError{Op: "open private state", Path: path, Err: err}
	}
	return os.NewFile(uintptr(fd), path), nil
}

func nativeMkdirCustody(path string) error { return os.Mkdir(path, 0o700) }

func nativeCheckCustody(f *os.File, directory, ancestor bool, links uint32) error {
	info, err := f.Stat()
	if err != nil {
		return err
	}
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok || (directory && !info.IsDir()) || (!directory && !info.Mode().IsRegular()) {
		return errCustody
	}
	if ancestor {
		if (s.Uid != 0 && s.Uid != uint32(os.Geteuid())) || (info.Mode().Perm()&0o022 != 0 && info.Mode()&os.ModeSticky == 0) {
			return errCustody
		}
	} else if s.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0o077 != 0 {
		return errCustody
	}
	if !directory && uint64(s.Nlink) != uint64(links) {
		return errCustody
	}
	return nativeCheckDarwinACL(f, directory, ancestor)
}

func nativePublishCustody(pending, final string) error {
	if err := os.Link(pending, final); err != nil {
		return err
	}
	return os.Remove(pending)
}

func nativeSyncCustody(path string) error {
	// The parent owns the custody root's directory entry. Flush it even on
	// retries: an existing root may remain from an earlier failed parent sync.
	for _, directory := range []string{path, filepath.Dir(path)} {
		f, err := nativeOpenCustody(directory, true, false)
		if err != nil {
			return err
		}
		err = f.Sync()
		closed := f.Close()
		if err != nil {
			return err
		}
		if closed != nil {
			return closed
		}
	}
	return nil
}
