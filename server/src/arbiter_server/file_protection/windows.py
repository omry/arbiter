from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


_WINDOWS_PRINCIPAL_NAMES = {
    "S-1-1-0": "Everyone",
    "S-1-3-4": "Owner Rights",
    "S-1-5-4": "Interactive Users",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-18": "LocalSystem",
    "S-1-5-32-544": "Builtin Administrators",
    "S-1-5-32-545": "Builtin Users",
    "S-1-5-32-546": "Builtin Guests",
}
_WINDOWS_ALLOWED_RUNTIME_PERMISSION_SIDS = {
    "S-1-5-18",  # LocalSystem
    "S-1-5-32-544",  # Builtin Administrators
}
_WINDOWS_DOMAIN_USER_RID_PATTERN = re.compile(r"^S-1-5-21-\d+-\d+-\d+-(513|514)$")
_WINDOWS_FILE_READ_WRITE_ACCESS_MASK = (
    0x00000001  # FILE_READ_DATA
    | 0x00000002  # FILE_WRITE_DATA
    | 0x00000004  # FILE_APPEND_DATA
    | 0x00000008  # FILE_READ_EA
    | 0x00000010  # FILE_WRITE_EA
    | 0x00000080  # FILE_READ_ATTRIBUTES
    | 0x00000100  # FILE_WRITE_ATTRIBUTES
    | 0x00010000  # DELETE
    | 0x00020000  # READ_CONTROL
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x10000000  # GENERIC_ALL
    | 0x40000000  # GENERIC_WRITE
    | 0x80000000  # GENERIC_READ
)
_WINDOWS_DIRECTORY_MUTATING_ACCESS_MASK = (
    0x00000002  # FILE_ADD_FILE
    | 0x00000004  # FILE_ADD_SUBDIRECTORY
    | 0x00000040  # FILE_DELETE_CHILD
    | 0x00010000  # DELETE
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x10000000  # GENERIC_ALL
    | 0x40000000  # GENERIC_WRITE
)


@dataclass(frozen=True)
class _WindowsAccessAce:
    sid: str
    mask: int


@dataclass(frozen=True)
class _WindowsFileSecurity:
    owner_sid: str | None
    access_aces: tuple[_WindowsAccessAce, ...]
    has_null_dacl: bool


def _windows_principal_name(sid: str) -> str:
    if sid in _WINDOWS_PRINCIPAL_NAMES:
        return _WINDOWS_PRINCIPAL_NAMES[sid]
    match = _WINDOWS_DOMAIN_USER_RID_PATTERN.fullmatch(sid)
    if match is None:
        return sid
    rid = match.group(1)
    if rid == "513":
        return "Domain Users"
    return "Domain Guests"


def _windows_unallowed_access_reason(
    access_aces: Sequence[_WindowsAccessAce],
    *,
    owner_sid: str | None,
    access_mask: int,
) -> str | None:
    allowed_sids = set(_WINDOWS_ALLOWED_RUNTIME_PERMISSION_SIDS)
    if owner_sid is not None:
        allowed_sids.add(owner_sid)
    for ace in access_aces:
        if not ace.mask & access_mask:
            continue
        if ace.sid in allowed_sids:
            continue
        principal_name = _windows_principal_name(ace.sid)
        return f"{principal_name} ({ace.sid}) grants access outside the allowlist"
    return None


def _windows_file_security(path: Path) -> _WindowsFileSecurity:
    import ctypes
    from ctypes import wintypes

    if os.name != "nt":
        raise OSError("Windows ACL inspection is only available on Windows")

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("Header", AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    se_file_object = 1
    dacl_security_information = 0x00000004
    owner_security_information = 0x00000001
    acl_size_information = 2
    access_allowed_ace_type = 0

    psid = ctypes.c_void_p
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("ctypes.WinDLL is not available")

    def last_windows_error(message: str) -> OSError:
        get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
        return OSError(get_last_error(), message)

    advapi32 = win_dll("advapi32", use_last_error=True)
    kernel32 = win_dll("kernel32", use_last_error=True)

    get_named_security_info = advapi32.GetNamedSecurityInfoW
    get_named_security_info.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(psid),
        ctypes.POINTER(psid),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_named_security_info.restype = wintypes.DWORD

    get_acl_information = advapi32.GetAclInformation
    get_acl_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_acl_information.restype = wintypes.BOOL

    get_ace = advapi32.GetAce
    get_ace.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_ace.restype = wintypes.BOOL

    convert_sid_to_string_sid = advapi32.ConvertSidToStringSidW
    convert_sid_to_string_sid.argtypes = [
        psid,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    convert_sid_to_string_sid.restype = wintypes.BOOL

    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    dacl = ctypes.c_void_p()
    owner_sid = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    result = get_named_security_info(
        str(path),
        se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(owner_sid),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result != 0:
        raise OSError(result, f"GetNamedSecurityInfoW failed for {path}")
    try:
        owner_sid_string_value: str | None = None
        if owner_sid:
            owner_sid_string = wintypes.LPWSTR()
            if not convert_sid_to_string_sid(owner_sid, ctypes.byref(owner_sid_string)):
                raise last_windows_error("ConvertSidToStringSidW failed for owner")
            try:
                owner_sid_string_value = owner_sid_string.value
                if owner_sid_string_value is None:
                    raise OSError("ConvertSidToStringSidW returned a null owner SID")
            finally:
                local_free(ctypes.cast(owner_sid_string, ctypes.c_void_p))

        if not dacl:
            return _WindowsFileSecurity(
                owner_sid=owner_sid_string_value,
                access_aces=(),
                has_null_dacl=True,
            )

        acl_info = AclSizeInformation()
        if not get_acl_information(
            dacl,
            ctypes.byref(acl_info),
            ctypes.sizeof(acl_info),
            acl_size_information,
        ):
            raise last_windows_error("GetAclInformation failed")

        access_aces: list[_WindowsAccessAce] = []
        for index in range(acl_info.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace_pointer)):
                raise last_windows_error("GetAce failed")
            if ace_pointer.value is None:
                raise OSError("GetAce returned a null ACE pointer")
            ace = ctypes.cast(
                ace_pointer,
                ctypes.POINTER(AccessAllowedAce),
            ).contents
            if ace.Header.AceType != access_allowed_ace_type:
                continue
            sid_pointer = ctypes.c_void_p(
                ace_pointer.value + AccessAllowedAce.SidStart.offset
            )
            sid_string = wintypes.LPWSTR()
            if not convert_sid_to_string_sid(sid_pointer, ctypes.byref(sid_string)):
                raise last_windows_error("ConvertSidToStringSidW failed")
            try:
                if sid_string.value is None:
                    raise OSError("ConvertSidToStringSidW returned a null SID")
                access_aces.append(
                    _WindowsAccessAce(sid=sid_string.value, mask=int(ace.Mask))
                )
            finally:
                local_free(ctypes.cast(sid_string, ctypes.c_void_p))
        return _WindowsFileSecurity(
            owner_sid=owner_sid_string_value,
            access_aces=tuple(access_aces),
            has_null_dacl=False,
        )
    finally:
        if security_descriptor:
            local_free(security_descriptor)


def _windows_unallowed_permission_reason(path: Path, *, access_mask: int) -> str | None:
    security = _windows_file_security(path)
    if security.has_null_dacl:
        return "file has a null DACL, which grants full access"
    return _windows_unallowed_access_reason(
        security.access_aces,
        owner_sid=security.owner_sid,
        access_mask=access_mask,
    )


def _windows_icacls_remediation(path: Path) -> str:
    return (
        "repair ACLs from an elevated Command Prompt (cmd.exe) on the Arbiter "
        f'host by running `takeown /F "{path}"`, then '
        f'`icacls "{path}" /inheritance:r '
        '/grant:r "%USERDOMAIN%\\%USERNAME%:F" '
        '/grant:r "*S-1-5-18:F" /grant:r "*S-1-5-32-544:F"`'
    )


def ensure_runtime_config_permissions(
    *,
    config_dir: Path,
    env_file: Path | None,
) -> None:
    for directory in sorted(
        {config_dir, *(path.parent for path in config_dir.rglob("*.yaml"))}
    ):
        if not directory.is_dir():
            continue
        try:
            reason = _windows_unallowed_permission_reason(
                directory,
                access_mask=_WINDOWS_DIRECTORY_MUTATING_ACCESS_MASK,
            )
        except OSError as exc:
            raise ValueError(
                "unsafe config directory permissions: "
                f"could not verify Windows ACLs for {directory}; "
                "refusing to use runtime config with unverified permissions. "
                f"{_windows_icacls_remediation(directory)}"
            ) from exc
        if reason is not None:
            raise ValueError(
                "unsafe config directory permissions: "
                f"{directory} must not grant mutation access outside the owner, "
                f"SYSTEM, or Administrators ({reason}); "
                f"{_windows_icacls_remediation(directory)}"
            )

    for config_file in sorted(config_dir.rglob("*.yaml")):
        if not config_file.is_file():
            continue
        try:
            reason = _windows_unallowed_permission_reason(
                config_file,
                access_mask=_WINDOWS_FILE_READ_WRITE_ACCESS_MASK,
            )
        except OSError as exc:
            raise ValueError(
                "unsafe config file permissions: "
                f"could not verify Windows ACLs for {config_file}; "
                "refusing to use runtime config with unverified permissions. "
                f"{_windows_icacls_remediation(config_file)}"
            ) from exc
        if reason is not None:
            raise ValueError(
                "unsafe config file permissions: "
                f"{config_file} must not grant read/write access outside the "
                f"owner, SYSTEM, or Administrators ({reason}); "
                f"{_windows_icacls_remediation(config_file)}"
            )

    if env_file is None or not env_file.exists():
        return
    if env_file.parent.exists():
        try:
            reason = _windows_unallowed_permission_reason(
                env_file.parent,
                access_mask=_WINDOWS_DIRECTORY_MUTATING_ACCESS_MASK,
            )
        except OSError as exc:
            raise ValueError(
                "unsafe app env directory permissions: "
                f"could not verify Windows ACLs for {env_file.parent}; "
                "refusing to load runtime env file with unverified permissions. "
                f"{_windows_icacls_remediation(env_file.parent)}"
            ) from exc
        if reason is not None:
            raise ValueError(
                "unsafe app env directory permissions: "
                f"{env_file.parent} must not grant mutation access outside "
                f"owner, SYSTEM, or Administrators ({reason}); "
                f"{_windows_icacls_remediation(env_file.parent)}"
            )
    try:
        reason = _windows_unallowed_permission_reason(
            env_file,
            access_mask=_WINDOWS_FILE_READ_WRITE_ACCESS_MASK,
        )
    except OSError as exc:
        raise ValueError(
            "unsafe app env file permissions: "
            f"could not verify Windows ACLs for {env_file}; "
            "refusing to load runtime env file with unverified permissions. "
            f"{_windows_icacls_remediation(env_file)}"
        ) from exc
    if reason is not None:
        raise ValueError(
            "unsafe app env file permissions: "
            f"{env_file} must not grant read/write access outside the "
            f"owner, SYSTEM, or Administrators ({reason}); "
            f"{_windows_icacls_remediation(env_file)}"
        )
