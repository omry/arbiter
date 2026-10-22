//go:build darwin

package identity

import (
	"bytes"
	"context"
	"os/exec"
	"path/filepath"
	"testing"
)

func darwinACL(t *testing.T, path, entry string) []byte {
	t.Helper()
	if entry != "" {
		if err := exec.Command("chmod", "+a", entry, path).Run(); err != nil {
			t.Fatal(err)
		}
	}
	data, err := exec.Command("ls", "-led", path).Output()
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func darwinRejectExisting(t *testing.T, root, selector string) {
	t.Helper()
	if _, err := LoadProvisional(root, selector); err == nil {
		t.Fatal("loaded through unsafe ACL")
	}
	if err := RemoveProvisional(root, selector); err == nil {
		t.Fatal("removed through unsafe ACL")
	}
}

func TestDarwinCustodyRejectsACLGrantsAndPreservesDenyOnly(t *testing.T) {
	t.Run("safe", func(t *testing.T) {
		root, p, _ := storedProvisional(t)
		defer func() {
			if err := exec.Command("chmod", "-a", "everyone deny delete", root).Run(); err != nil {
				t.Error(err)
			}
		}()
		darwinACL(t, root, "everyone deny delete")
		if _, err := LoadProvisional(root, p.record.Selector); err != nil {
			t.Fatal(err)
		}
		if err := RemoveProvisional(root, p.record.Selector); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("root-inherited-read", func(t *testing.T) {
		root, p, _ := storedProvisional(t)
		before := darwinACL(t, root, "everyone allow read,execute,file_inherit,directory_inherit")
		other, _ := provisional(t)
		if err := SaveProvisional(context.Background(), root, other); err == nil {
			t.Fatal("accepted inherited ACL on custody root")
		}
		if after := darwinACL(t, root, ""); !bytes.Equal(before, after) {
			t.Fatal("rejection changed the root ACL")
		}
		darwinRejectExisting(t, root, p.record.Selector)
	})
	for _, tc := range []struct{ name, acl string }{{"ancestor-inherited-read", "everyone allow read,execute,file_inherit,directory_inherit"}, {"ancestor-mutation", "everyone allow write,delete_child,writesecurity"}} {
		t.Run(tc.name, func(t *testing.T) {
			parent := custodyTestDir(t)
			before := darwinACL(t, parent, tc.acl)
			p, _ := provisional(t)
			if err := SaveProvisional(context.Background(), filepath.Join(parent, "state"), p); err == nil {
				t.Fatal("accepted unsafe ancestor ACL")
			}
			if after := darwinACL(t, parent, ""); !bytes.Equal(before, after) {
				t.Fatal("rejection changed ancestor ACL")
			}
		})
	}
	t.Run("private-file-read-write", func(t *testing.T) {
		root, p, _ := storedProvisional(t)
		final, _, _ := custodyNames(root, p.record.Selector)
		before := darwinACL(t, final, "everyone allow read,write")
		darwinRejectExisting(t, root, p.record.Selector)
		if after := darwinACL(t, final, ""); !bytes.Equal(before, after) {
			t.Fatal("rejection changed private-file ACL")
		}
	})
}
