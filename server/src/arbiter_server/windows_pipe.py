"""Native private Windows pipe primitives; no listener is activated here.

Every pending OVERLAPPED owns its event and buffer until native completion,
including cancellation. Handles stay local, non-inheritable and ACL restricted.
"""

from __future__ import annotations

import asyncio
import ctypes as ct
from ctypes import wintypes as wt
import os
import re
from typing import Any

from .authority import AuthorityError
from .file_protection.windows import _windows_file_security

CAPACITY = 64 * 1024 + 4
_INVALID = ct.c_void_p(-1).value


def _local_name(name: str) -> None:
    if re.fullmatch(r"\\\\\.\\pipe\\arbiter-[a-z0-9-]{16,96}", name) is None:
        raise AuthorityError("invalid local control pipe name")


class _Overlapped(ct.Structure):
    _fields_ = [
        ("Internal", ct.c_size_t),
        ("InternalHigh", ct.c_size_t),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", wt.HANDLE),
    ]


class _SecurityAttributes(ct.Structure):
    _fields_ = [
        ("length", wt.DWORD),
        ("descriptor", ct.c_void_p),
        ("inherit", wt.BOOL),
    ]


class Native:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows pipes require Windows")
        dll = getattr(ct, "WinDLL")
        self.k = dll("kernel32", use_last_error=True)
        self.a = dll("advapi32", use_last_error=True)
        h, p, d, b = wt.HANDLE, ct.c_void_p, wt.DWORD, wt.BOOL
        # Explicit signatures preserve pointer/handle widths on x64 and ARM64.
        signatures = {
            "CreateNamedPipeW": (h, [wt.LPCWSTR, d, d, d, d, d, d, p]),
            "CreateFileW": (h, [wt.LPCWSTR, d, d, p, d, d, h]),
            "CreateEventW": (h, [p, b, b, wt.LPCWSTR]),
            "CloseHandle": (b, [h]),
            "LocalFree": (p, [p]),
            "ConnectNamedPipe": (b, [h, p]),
            "SetNamedPipeHandleState": (b, [h, p, p, p]),
            "ReadFile": (b, [h, p, d, p, p]),
            "WriteFile": (b, [h, p, d, p, p]),
            "GetOverlappedResult": (b, [h, p, p, b]),
            "CancelIoEx": (b, [h, p]),
            "GetNamedPipeServerProcessId": (b, [h, p]),
            "OpenProcess": (h, [d, b, d]),
            "GetCurrentProcess": (h, []),
            "GetCurrentThread": (h, []),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.k, name)
            function.restype, function.argtypes = result, arguments
        for name, result, arguments in [
            (
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                b,
                [wt.LPCWSTR, d, p, p],
            ),
            ("OpenProcessToken", b, [h, d, p]),
            ("OpenThreadToken", b, [h, d, b, p]),
            ("GetTokenInformation", b, [h, d, p, d, p]),
            ("ConvertSidToStringSidW", b, [p, p]),
            ("ImpersonateNamedPipeClient", b, [h]),
            ("RevertToSelf", b, []),
        ]:
            function = getattr(self.a, name)
            function.restype, function.argtypes = result, arguments
        self.sid = self.process_sid(self.k.GetCurrentProcess())

    def error(self) -> OSError:
        return getattr(ct, "WinError")(getattr(ct, "get_last_error")())

    def token_sid(self, token: int) -> str:
        size = wt.DWORD()
        self.a.GetTokenInformation(token, 1, None, 0, ct.byref(size))
        if not 0 < size.value <= 65536:
            raise AuthorityError("invalid Windows token size")
        data = ct.create_string_buffer(size.value)
        if not self.a.GetTokenInformation(token, 1, data, size, ct.byref(size)):
            raise self.error()
        sid = ct.cast(data, ct.POINTER(ct.c_void_p))[0]
        text = wt.LPWSTR()
        if not self.a.ConvertSidToStringSidW(sid, ct.byref(text)):
            raise self.error()
        try:
            if text.value is None:
                raise AuthorityError("missing Windows token SID")
            return text.value
        finally:
            self.k.LocalFree(ct.cast(text, ct.c_void_p))

    def process_sid(self, process: int) -> str:
        token = wt.HANDLE()
        if not self.a.OpenProcessToken(process, 8, ct.byref(token)):
            raise self.error()
        try:
            if token.value is None:
                raise AuthorityError("missing Windows process token")
            return self.token_sid(token.value)
        finally:
            self.k.CloseHandle(token)

    def verify_acl(self, handle: int) -> None:
        security = _windows_file_security(handle)
        if (
            security.owner_sid != self.sid
            or security.has_null_dacl
            or security.has_unsupported_aces
            or not security.access_aces
            or any(
                ace.sid not in {self.sid, "S-1-5-18", "S-1-5-32-544"} and ace.mask
                for ace in security.access_aces
            )
        ):
            raise AuthorityError("unsafe local control pipe ACL or owner")

    def verify_client(self, handle: int) -> None:
        # Call after reading a message: impersonation uses its native token.
        if not self.a.ImpersonateNamedPipeClient(handle):
            raise self.error()
        token = wt.HANDLE()
        try:
            if not self.a.OpenThreadToken(
                self.k.GetCurrentThread(), 8, True, ct.byref(token)
            ):
                raise self.error()
            if token.value is None:
                raise AuthorityError("missing Windows caller token")
            if self.token_sid(token.value) != self.sid:
                raise AuthorityError("local control caller is not the instance user")
        finally:
            if token:
                self.k.CloseHandle(token)
            # Continuing the event loop under an un-reverted token is unsafe.
            if not self.a.RevertToSelf():
                os._exit(1)

    def verify_server(self, handle: int) -> None:
        self.verify_acl(handle)
        pid = wt.DWORD()
        if not self.k.GetNamedPipeServerProcessId(handle, ct.byref(pid)):
            raise self.error()
        process = self.k.OpenProcess(0x1000, False, pid)
        if not process:
            raise self.error()
        try:
            if self.process_sid(process) != self.sid:
                raise AuthorityError("local control server is not the instance user")
        finally:
            self.k.CloseHandle(process)

    def create(self, name: str, *, first: bool = False) -> Pipe:
        _local_name(name)
        descriptor = ct.c_void_p()
        sddl = f"O:{self.sid}D:P(A;;GA;;;{self.sid})(A;;GA;;;SY)(A;;GA;;;BA)"
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ct.byref(descriptor), None
        ):
            raise self.error()
        try:
            security = _SecurityAttributes(
                ct.sizeof(_SecurityAttributes), descriptor, False
            )
            # Duplex, overlapped, optional FIRST_PIPE_INSTANCE; message mode,
            # reject remote clients, at most16 native instances, bounded buffers.
            handle = self.k.CreateNamedPipeW(
                name,
                3 | 0x40000000 | 0x40000 | (0x80000 if first else 0),
                4 | 2 | 8,
                16,
                CAPACITY,
                CAPACITY,
                0,
                ct.byref(security),
            )
        finally:
            self.k.LocalFree(descriptor)
        if handle == _INVALID:
            raise self.error()
        pipe = Pipe(self, handle)
        try:
            self.verify_acl(handle)
            return pipe
        except BaseException:
            pipe.close()
            raise

    def open(self, name: str) -> Pipe:
        _local_name(name)
        # Identification-only prevents the server acting as the client.
        handle = self.k.CreateFileW(
            name, 0xC0020000, 0, None, 3, 0x40000000 | 0x100000 | 0x10000, None
        )
        if handle == _INVALID:
            raise self.error()
        pipe = Pipe(self, handle)
        try:
            self.verify_server(handle)
            mode = wt.DWORD(2)  # PIPE_READMODE_MESSAGE
            if not self.k.SetNamedPipeHandleState(handle, ct.byref(mode), None, None):
                raise self.error()
            return pipe
        except BaseException:
            pipe.close()
            raise


class Pipe:
    def __init__(self, native: Native, handle: int) -> None:
        self.native, self.handle = native, handle
        self.pending = False

    async def _io(self, operation: str, data: bytes = b"") -> bytes:
        if self.pending or not self.handle:
            raise AuthorityError("local pipe is closed or already has pending I/O")
        k = self.native.k
        event = k.CreateEventW(None, True, False, None)
        if not event:
            raise self.native.error()
        overlapped = _Overlapped(hEvent=event)
        buffer = (
            ct.create_string_buffer(data, len(data))
            if data
            else ct.create_string_buffer(CAPACITY)
        )
        count = wt.DWORD()
        self.pending = True
        pending = False
        try:
            args: list[Any] = [self.handle]
            if operation != "ConnectNamedPipe":
                args += [buffer, len(data) if data else CAPACITY, None]
            ok = getattr(k, operation)(*args, ct.byref(overlapped))
            error = getattr(ct, "get_last_error")()
            if not ok and not (operation == "ConnectNamedPipe" and error == 535):
                if error != 997:  # ERROR_IO_PENDING
                    raise self.native.error()
                pending = True
            if operation == "ConnectNamedPipe" and not pending:
                return b""
            while not k.GetOverlappedResult(
                self.handle, ct.byref(overlapped), ct.byref(count), False
            ):
                if getattr(ct, "get_last_error")() != 996:  # ERROR_IO_INCOMPLETE
                    pending = False
                    raise self.native.error()
                await asyncio.sleep(0.01)
            pending = False
            if operation == "WriteFile" and count.value != len(data):
                raise AuthorityError("incomplete local pipe write")
            return buffer.raw[: count.value] if operation == "ReadFile" else b""
        finally:
            if pending:
                k.CancelIoEx(self.handle, ct.byref(overlapped))
                # Cancellation is a request. Retain native memory until the
                # real operation finishes, even through repeated cancellation.
                while not k.GetOverlappedResult(
                    self.handle, ct.byref(overlapped), ct.byref(count), False
                ):
                    if getattr(ct, "get_last_error")() != 996:
                        break
                    try:
                        await asyncio.sleep(0.01)
                    except asyncio.CancelledError:
                        pass
            self.pending = False
            k.CloseHandle(event)

    async def connect(self) -> None:
        await self._io("ConnectNamedPipe")

    async def read(self) -> bytes:
        return await self._io("ReadFile")

    async def write(self, data: bytes) -> None:
        if not data or len(data) > CAPACITY:
            raise AuthorityError("invalid local pipe message size")
        await self._io("WriteFile", data)

    def close(self) -> None:
        if self.pending:
            raise AuthorityError("cannot close local pipe before native I/O completes")
        if self.handle:
            self.native.k.CloseHandle(self.handle)
            self.handle = 0
