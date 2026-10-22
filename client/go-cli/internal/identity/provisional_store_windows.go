package identity

import (
	"fmt"
	"os"
	"runtime"
	"unsafe"

	"golang.org/x/sys/windows"
)

const custodySystem = "S-1-5-18"
const custodyAdmins = "S-1-5-32-544"
const custodyInstaller = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"

func custodyCaller() (string, error) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return "", err
	}
	return user.User.Sid.String(), nil
}

func custodySecurity() (*windows.SECURITY_DESCRIPTOR, error) {
	caller, err := custodyCaller()
	if err != nil {
		return nil, err
	}
	return windows.SecurityDescriptorFromString("D:P(A;OICI;FA;;;" + caller + ")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")
}

func nativeMkdirCustody(path string) error {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	sd, err := custodySecurity()
	if err != nil {
		return err
	}
	sa := windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), SecurityDescriptor: sd}
	return windows.CreateDirectory(name, &sa)
}

func nativeOpenCustody(path string, directory, create bool) (*os.File, error) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	access := uint32(windows.READ_CONTROL | windows.FILE_READ_ATTRIBUTES)
	flags := uint32(windows.FILE_FLAG_OPEN_REPARSE_POINT)
	share := uint32(windows.FILE_SHARE_READ)
	creation := uint32(windows.OPEN_EXISTING)
	var sa *windows.SecurityAttributes
	if directory {
		flags |= windows.FILE_FLAG_BACKUP_SEMANTICS
		share |= windows.FILE_SHARE_WRITE | windows.FILE_SHARE_DELETE
	} else {
		access |= windows.GENERIC_READ
	}
	if create {
		creation = windows.CREATE_NEW
		access |= windows.GENERIC_WRITE
		sd, err := custodySecurity()
		if err != nil {
			return nil, err
		}
		sa = &windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), SecurityDescriptor: sd}
	}
	handle, err := windows.CreateFile(name, access, share, sa, creation, flags, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open private state", Path: path, Err: err}
	}
	return os.NewFile(uintptr(handle), path), nil
}

func nativeCheckCustody(f *os.File, directory, ancestor bool, links uint32) error {
	handle := windows.Handle(f.Fd())
	var info windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(handle, &info); err != nil {
		return err
	}
	typeOfFile, err := windows.GetFileType(handle)
	if err != nil || typeOfFile != windows.FILE_TYPE_DISK || info.FileAttributes&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 ||
		(info.FileAttributes&windows.FILE_ATTRIBUTE_DIRECTORY != 0) != directory || (!directory && info.NumberOfLinks != links) {
		return errCustody
	}
	sd, err := windows.GetSecurityInfo(handle, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION|windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return err
	}
	defer runtime.KeepAlive(sd)
	caller, err := custodyCaller()
	if err != nil {
		return err
	}
	owner, _, err := sd.Owner()
	if err != nil || owner == nil {
		return errCustody
	}
	trusted := func(sid string) bool {
		return sid == caller || sid == custodySystem || sid == custodyAdmins || (ancestor && sid == custodyInstaller)
	}
	ownerSID := owner.String()
	if !trusted(ownerSID) {
		return fmt.Errorf("untrusted Windows owner: %w", errCustody)
	}
	acl, _, err := sd.DACL()
	if err != nil || acl == nil { // An absent/null DACL permits everyone.
		return fmt.Errorf("missing Windows DACL: %w", errCustody)
	}
	mask := uint32(0xf00f019f) // File read/write/metadata/delete/security + all generic rights.
	if directory {
		mask = 0x500d0046 // Add files/subdirectories, delete children or change security.
	}
	if ancestor {
		mask = 0x500d0040 // Creating unrelated siblings does not replace this directory.
	}
	for index := uint32(0); index < uint32(acl.AceCount); index++ {
		var ace *windows.ACCESS_ALLOWED_ACE
		if windows.GetAce(acl, index, &ace) != nil || ace == nil {
			return errCustody
		}
		if ancestor && ace.Header.AceFlags&windows.INHERIT_ONLY_ACE != 0 {
			continue
		}
		if ace.Header.AceType == windows.ACCESS_DENIED_ACE_TYPE {
			continue // Denial cannot grant additional authority.
		}
		if ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE || uintptr(ace.Header.AceSize) < unsafe.Sizeof(*ace) {
			return fmt.Errorf("unsupported Windows DACL entry: %w", errCustody)
		}
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		if !sid.IsValid() {
			return errCustody
		}
		principal := sid.String()
		if ancestor && principal == "S-1-3-4" {
			principal = ownerSID
		}
		if uint32(ace.Mask)&mask != 0 && !trusted(principal) {
			return fmt.Errorf("Windows DACL grants access to %s outside caller/SYSTEM/Administrators: %w", principal, errCustody)
		}
	}
	return nil
}

func nativePublishCustody(pending, final string) error {
	from, err := windows.UTF16PtrFromString(pending)
	if err != nil {
		return err
	}
	to, err := windows.UTF16PtrFromString(final)
	if err != nil {
		return err
	}
	// Never replace an existing file or permit a cross-volume copy/delete.
	return windows.MoveFileEx(from, to, windows.MOVEFILE_WRITE_THROUGH)
}

// MoveFileExW WRITE_THROUGH supplies Windows publication durability. Directory
// FlushFileBuffers is not a supported substitute and must not cause a fallback.
func nativeSyncCustody(string) error { return nil }
