//go:build darwin

package identity

import (
	"encoding/binary"
	"os"
	"runtime"
	"syscall"
	"unsafe"

	"golang.org/x/sys/unix"
)

const darwinACLBufferSize, darwinACLMaxEntries, darwinACLMagic = 4096, 128, 0x012cc16d
const darwinACLNoACL, darwinACLPermit, darwinACEDeny = ^uint32(0), 1, 2
const darwinACEInherit = 1<<4 | 1<<5 | 1<<6 | 1<<7 | 1<<8
const darwinACLWrite = 1<<2 | 1<<4 | 1<<5 | 1<<6 | 1<<8 | 1<<10 | 1<<12 | 1<<13 | 1<<23 | 1<<21

func nativeCheckDarwinACL(f *os.File, _, ancestor bool) error {
	list := unix.Attrlist{Bitmapcount: unix.ATTR_BIT_MAP_COUNT, Commonattr: unix.ATTR_CMN_EXTENDED_SECURITY}
	buf := make([]byte, darwinACLBufferSize)
	_, _, errno := syscall.Syscall6(unix.SYS_FGETATTRLIST, uintptr(f.Fd()), uintptr(unsafe.Pointer(&list)), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), 0, 0)
	runtime.KeepAlive(f)
	if errno != 0 || len(buf) < 12 {
		return errCustody
	}
	total := int(binary.LittleEndian.Uint32(buf[:4]))
	if total < 12 || total > len(buf) {
		return errCustody
	}
	offset := int(int32(binary.LittleEndian.Uint32(buf[4:8])))
	length := int(binary.LittleEndian.Uint32(buf[8:12]))
	if length == 0 {
		return nil
	}
	data := 4 + offset
	if offset < 8 || data < 12 || length < 44 || length > total-data || data > total {
		return errCustody
	}
	sec := buf[data : data+length]
	if binary.LittleEndian.Uint32(sec) != darwinACLMagic {
		return errCustody
	}
	count := binary.LittleEndian.Uint32(sec[36:])
	if count == darwinACLNoACL || count == 0 {
		return nil
	}
	if count > darwinACLMaxEntries || length < 44+int(count)*24 {
		return errCustody
	}
	for i := 0; i < int(count); i++ {
		ace := sec[44+i*24:]
		kind := binary.LittleEndian.Uint32(ace[16:]) & 0xf
		if kind == darwinACEDeny {
			continue
		}
		if kind != darwinACLPermit {
			return errCustody
		}
		rights := binary.LittleEndian.Uint32(ace[20:])
		flags := binary.LittleEndian.Uint32(ace[16:])
		if !ancestor || rights&darwinACLWrite != 0 || flags&darwinACEInherit != 0 {
			return errCustody
		}
	}
	return nil
}
