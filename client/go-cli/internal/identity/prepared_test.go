package identity

import (
	"context"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestPreparedCleanupPreservesUnknownNamesAndReportsNoProgress(t *testing.T) {
	root, dir, err := custodyRoot(filepath.Join(custodyTestDir(t), "prepared"), true)
	if err != nil {
		t.Fatal(err)
	}
	dir.Close()
	// These are valid bare selectors, but none is a managed .json/.pending
	// record. Treating them as removals would permit an endless false-progress
	// retry loop. The existing bounded contract must report no progress.
	for i := 0; i <= provisionalCleanupBatch; i++ {
		var value [16]byte
		value[0] = byte(i)
		name := base64.RawURLEncoding.EncodeToString(value[:])
		f, err := nativeOpenCustody(filepath.Join(root, name), false, true)
		if err != nil {
			t.Fatal(err)
		}
		f.Close()
	}
	if err := CleanupPrepared(context.Background(), root); !errors.Is(err, ErrProvisionalUnexpectedContents) {
		t.Fatal("unknown names were claimed as cleanup progress", err)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != provisionalCleanupBatch+2 {
		t.Fatal("unknown files were removed", err)
	}
}
