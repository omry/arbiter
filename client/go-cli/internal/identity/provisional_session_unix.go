//go:build !windows

package identity

import (
	"errors"
	"os"
	"syscall"
)

func nativeLockProvisional(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}

func nativeProvisionalBusy(err error) bool {
	return errors.Is(err, syscall.EWOULDBLOCK)
}
