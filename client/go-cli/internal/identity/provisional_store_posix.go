//go:build !windows && !darwin

package identity

import "os"

func nativeCheckDarwinACL(*os.File, bool, bool) error { return nil }
