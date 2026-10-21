from __future__ import annotations

import asyncio
import ctypes as ct
from ctypes import wintypes as wt
from contextlib import contextmanager
import os
import secrets
from typing import cast

import pytest

from arbiter_server import windows_pipe as w
from arbiter_server.authority import AuthorityError
from arbiter_server.file_protection.windows import (
    _WindowsAccessAce,
    _WindowsFileSecurity,
)

native = pytest.mark.skipif(os.name != "nt", reason="native Windows pipe boundary")


@pytest.fixture
def api():
    return w.Native()


def name():
    return r"\\.\pipe\arbiter-test-" + secrets.token_hex(16)


def test_native_error_preserves_ctypes_private_error_code(monkeypatch):
    monkeypatch.setattr(ct, "get_last_error", lambda: 234, raising=False)
    monkeypatch.setattr(
        ct, "WinError", lambda code: OSError(code, "native"), raising=False
    )
    api = cast(w.Native, object.__new__(w.Native))
    assert api.error().errno == 234


@pytest.mark.parametrize(
    "address",
    [r"\\host\pipe\arbiter-test-1234567890", "anything", r"\\.\pipe\other-1234567890"],
)
def test_nonlocal_or_unexpected_name_rejected(address):
    with pytest.raises(AuthorityError, match="pipe name"):
        w._local_name(address)


@pytest.mark.parametrize(
    "security",
    [
        _WindowsFileSecurity("other", (_WindowsAccessAce("self", 1),), False),
        _WindowsFileSecurity("self", (), True),
        _WindowsFileSecurity("self", (), False),
        _WindowsFileSecurity("self", (_WindowsAccessAce("Everyone", 1),), False),
        _WindowsFileSecurity("self", (_WindowsAccessAce("self", 1),), False, True),
    ],
)
def test_acl_fails_closed(monkeypatch, security):
    monkeypatch.setattr(w, "_windows_file_security", lambda handle: security)
    instance = cast(w.Native, object.__new__(w.Native))
    instance.sid = "self"
    with pytest.raises(AuthorityError, match="ACL or owner"):
        instance.verify_acl(123)


def test_acl_allowlist(monkeypatch):
    security = _WindowsFileSecurity(
        "self",
        tuple(
            _WindowsAccessAce(sid, 0x1F01FF)
            for sid in ["self", "S-1-5-18", "S-1-5-32-544"]
        ),
        False,
    )
    monkeypatch.setattr(w, "_windows_file_security", lambda handle: security)
    instance = cast(w.Native, object.__new__(w.Native))
    instance.sid = "self"
    instance.verify_acl(123)


def test_cancel_retains_event_buffer_and_handle_until_actual_completion(monkeypatch):
    events, error = [], [997]
    finished = False

    class Kernel:
        def CreateEventW(self, *args):
            return 111

        def ReadFile(self, handle, buffer, size, count, overlapped):
            self.buffer, self.overlapped = buffer, overlapped
            error[0] = 997
            return False

        def GetOverlappedResult(self, *args):
            error[0] = 995 if finished else 996
            return False

        def CancelIoEx(self, *args):
            events.append("cancel-requested")

        def CloseHandle(self, handle):
            events.append(("closed", handle))

    monkeypatch.setattr(ct, "get_last_error", lambda: error[0], raising=False)
    api = cast(w.Native, object.__new__(w.Native))
    api.k = Kernel()
    pipe = w.Pipe(api, 222)

    async def exercise():
        nonlocal finished
        task = asyncio.create_task(pipe.read())
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and pipe.pending
        assert events == ["cancel-requested"]
        with pytest.raises(AuthorityError, match="before native I/O"):
            pipe.close()
        finished = True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not pipe.pending and events == ["cancel-requested", ("closed", 111)]
        pipe.close()
        assert events[-1] == ("closed", 222)

    asyncio.run(exercise())


@native
def test_native_exchange_and_boundaries(api):
    async def exercise():
        async def check_request(data):
            assert await server.read() == data
            api.verify_client(server.handle)

        async def check_reply(data):
            assert await client.read() == data

        address = name()
        server = api.create(address, first=True)
        try:
            with pytest.raises(OSError):
                api.create(address, first=True)
            accepting = asyncio.create_task(server.connect())
            client = api.open(address)
            try:
                await accepting
                for size in (1, w.CAPACITY):
                    data = b"x" * size
                    await asyncio.gather(client.write(data), check_request(data))
                    await asyncio.gather(server.write(data), check_reply(data))
                for invalid in (b"", b"x" * (w.CAPACITY + 1)):
                    with pytest.raises(AuthorityError, match="size"):
                        await client.write(invalid)
            finally:
                client.close()
        finally:
            server.close()

    asyncio.run(exercise())


@native
@pytest.mark.parametrize("operation", ["connect", "read", "write"])
def test_native_cancel_joins_io_before_handle_close(api, operation):
    async def exercise():
        address = name()
        server = api.create(address, first=True)
        client = None
        try:
            if operation != "connect":
                client = api.open(address)
                await server.connect()
            task = asyncio.create_task(
                server.connect()
                if operation == "connect"
                else (
                    server.read()
                    if operation == "read"
                    else server.write(b"x" * w.CAPACITY)
                )
            )
            await asyncio.sleep(0.03)
            if task.done() and operation == "write":
                # Fill the native outbound buffer before testing a pending write.
                await task
                task = asyncio.create_task(server.write(b"y" * w.CAPACITY))
                await asyncio.sleep(0.03)
            assert server.pending
            with pytest.raises(AuthorityError, match="before native I/O"):
                server.close()
            with pytest.raises(AuthorityError, match="pending I/O"):
                await server.read()
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert not server.pending
        finally:
            if client is not None:
                client.close()
            server.close()
            assert not server.handle
            server.close()

    asyncio.run(exercise())


@native
def test_native_peer_disconnect(api):
    async def exercise():
        address = name()
        server = api.create(address, first=True)
        client = api.open(address)
        try:
            await server.connect()
            client.close()
            with pytest.raises(OSError):
                await server.read()
            assert not server.pending
        finally:
            client.close()
            server.close()

    asyncio.run(exercise())


@native
def test_native_oversize_message_fails_closed(api):
    async def exercise():
        address = name()
        server, client = api.create(address, first=True), None
        task = None
        try:
            client = api.open(address)
            await server.connect()
            # Bypass the sender's public limit to exercise native hostile input.
            task = asyncio.create_task(client._io("WriteFile", b"x" * (w.CAPACITY + 1)))
            with pytest.raises(OSError) as error:
                await asyncio.wait_for(server.read(), 2)
            assert getattr(error.value, "winerror", None) == 234  # ERROR_MORE_DATA
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if client is not None:
                client.close()
            server.close()

    asyncio.run(exercise())


def set_acl(api, handle, sddl):
    descriptor = ct.c_void_p()
    assert api.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ct.byref(descriptor), None
    )
    setter = api.a.SetKernelObjectSecurity
    setter.argtypes, setter.restype = [wt.HANDLE, wt.DWORD, ct.c_void_p], wt.BOOL
    try:
        assert setter(handle, 4, descriptor)
    finally:
        api.k.LocalFree(descriptor)


@native
def test_native_permissive_acl_rejected_by_both_ends(api):
    address = name()
    server = api.create(address, first=True)
    try:
        set_acl(api, server.handle, f"D:P(A;;GA;;;{api.sid})(A;;GR;;;WD)")
        with pytest.raises(AuthorityError, match="ACL or owner"):
            api.verify_acl(server.handle)
        with pytest.raises(AuthorityError, match="ACL or owner"):
            api.open(address)
    finally:
        server.close()


@contextmanager
def different_user(api):
    # Real disposable local account. Credentials never enter argv/env/logs.
    class User(ct.Structure):
        _fields_ = [
            ("name", wt.LPWSTR),
            ("password", wt.LPWSTR),
            ("age", wt.DWORD),
            ("privilege", wt.DWORD),
            ("home", wt.LPWSTR),
            ("comment", wt.LPWSTR),
            ("flags", wt.DWORD),
            ("script", wt.LPWSTR),
        ]

    dll = getattr(ct, "WinDLL")("netapi32", use_last_error=True)
    dll.NetUserAdd.argtypes = [wt.LPCWSTR, wt.DWORD, ct.c_void_p, ct.c_void_p]
    dll.NetUserAdd.restype = wt.DWORD
    dll.NetUserDel.argtypes, dll.NetUserDel.restype = [wt.LPCWSTR, wt.LPCWSTR], wt.DWORD
    username, password = (
        "arb" + secrets.token_hex(6),
        secrets.token_urlsafe(24) + "Aa1!",
    )
    user = User(username, password, 0, 1, None, None, 0x10200, None)
    assert dll.NetUserAdd(None, 1, ct.byref(user), None) == 0
    token = wt.HANDLE()
    login = api.a.LogonUserW
    login.argtypes, login.restype = [
        wt.LPCWSTR,
        wt.LPCWSTR,
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        ct.c_void_p,
    ], wt.BOOL
    try:
        authenticated = login(username, ".", password, 2, 0, ct.byref(token))
        assert authenticated, f"LogonUserW failed: {getattr(ct, 'get_last_error')()}"
        yield token
    finally:
        if token:
            api.k.CloseHandle(token)
        assert dll.NetUserDel(None, username) == 0


@native
def test_real_different_windows_user_is_rejected(api):
    async def exercise(token):
        address = name()
        server = api.create(address, first=True)
        client = None
        impersonate = api.a.ImpersonateLoggedOnUser
        impersonate.argtypes, impersonate.restype = [wt.HANDLE], wt.BOOL

        def connect_as_other():
            assert impersonate(token)
            try:
                return api.k.CreateFileW(
                    address, 0xC0020000, 0, None, 3, 0x40110000, None
                )
            finally:
                if not api.a.RevertToSelf():
                    os._exit(1)

        try:
            denied = connect_as_other()
            assert denied == w._INVALID
            # Let the real other token reach the caller-token check. This ACL
            # broadening exists only in this test, never the product listener.
            set_acl(
                api,
                server.handle,
                f"D:P(A;;GA;;;{api.sid})(A;;GA;;;{api.token_sid(token.value)})",
            )
            handle = connect_as_other()
            assert handle != w._INVALID
            client = w.Pipe(api, handle)
            await server.connect()
            await asyncio.gather(client.write(b"probe"), server.read())
            with pytest.raises(AuthorityError, match="not the instance user"):
                api.verify_client(server.handle)
            assert api.process_sid(api.k.GetCurrentProcess()) == api.sid
        finally:
            if client is not None:
                client.close()
            server.close()

    with different_user(api) as token:
        asyncio.run(exercise(token))
