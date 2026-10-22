package identity

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"golang.org/x/sys/windows"
)

func custodyTestDir(t *testing.T) string {
	t.Helper()
	// Windows' shared TEMP can grant other users replacement rights. Prepare
	// only this test-owned directory; never repair a host or existing ACL.
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, ".arbiter-custody-test-"+hex.EncodeToString(id[:]))
	if err := nativeMkdirCustody(path); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(path) })
	return path
}

func setCustodyTestACL(t *testing.T, path, sddl string) {
	t.Helper()
	sd, err := windows.SecurityDescriptorFromString(sddl)
	if err != nil {
		t.Fatal(err)
	}
	acl, _, err := sd.DACL()
	if err != nil {
		t.Fatal(err)
	}
	if err := windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, acl, nil); err != nil {
		t.Fatal(err)
	}
}

func TestWindowsCustodyRejectsUnsafeACLsAndReparsePoints(t *testing.T) {
	root, p, _ := storedProvisional(t)
	final, pending, _ := custodyNames(root, p.record.Selector)
	caller, err := custodyCaller()
	if err != nil {
		t.Fatal(err)
	}
	private := "D:P(A;;FA;;;" + caller + ")(A;;FA;;;SY)(A;;FA;;;BA)"
	setCustodyTestACL(t, final, private+"(A;;GR;;;WD)")
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("loaded keys readable outside caller/SYSTEM/Administrators")
	}
	setCustodyTestACL(t, final, "D:NO_ACCESS_CONTROL")
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("accepted a null DACL")
	}
	setCustodyTestACL(t, final, private)
	if err := os.Link(final, pending); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadProvisional(root, p.record.Selector); err == nil {
		t.Fatal("loaded multiply linked keys")
	}
	if err := RemoveProvisional(root, p.record.Selector); err != nil {
		t.Fatal("could not explicitly recover exact known publication links", err)
	}
	setCustodyTestACL(t, root, private+"(A;;GW;;;WD)")
	if err := SaveProvisional(context.Background(), root, p); err == nil {
		t.Fatal("accepted a replaceable private directory")
	}
	setCustodyTestACL(t, root, private)
	alias := filepath.Join(filepath.Dir(root), "junction")
	cmd := exec.Command("cmd", "/c", "mklink", "/J", alias, root)
	if err := cmd.Run(); err != nil {
		t.Fatal("native reparse-point fixture could not be created", err)
	}
	defer os.Remove(alias)
	if err := SaveProvisional(context.Background(), alias, p); err == nil {
		t.Fatal("followed a Windows directory junction")
	}
}
