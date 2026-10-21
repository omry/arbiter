"""One bounded administrative exchange, authenticated by the local OS."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import ctypes
from errno import ECONNABORTED, EMFILE, ENFILE, ENOBUFS, ENOMEM
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import sys
import time
from typing import Any, AsyncGenerator, cast

from . import authority as a

MAX_MESSAGE = 64 * 1024
MAX_CONNECTIONS = 16
EXCHANGE_TIMEOUT = 10
_TRANSIENT = {ECONNABORTED, EMFILE, ENFILE, ENOBUFS, ENOMEM}


def _socket_identity(path: Path) -> tuple[int, int]:
    a._root(path.parent, create=False)
    info = path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise a.AuthorityError("unsafe local control endpoint")
    return info.st_dev, info.st_ino


def _peer_uid(peer: Any) -> int:
    if sys.platform.startswith("linux"):
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.unpack("iII", credentials)[1]
    if sys.platform in {"darwin", "freebsd", "openbsd", "netbsd"}:
        libc = ctypes.CDLL(None, use_errno=True)
        getpeereid = libc.getpeereid
        getpeereid.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        getpeereid.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if getpeereid(peer.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        return uid.value
    raise a.AuthorityError("native local peer verification is unavailable")


def _frame(value: dict[str, object]) -> bytes:
    data = json.dumps(value, separators=(",", ":")).encode()
    if len(data) > MAX_MESSAGE:
        raise a.AuthorityError("local control message is too large")
    return struct.pack("!I", len(data)) + data


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise a.AuthorityError("duplicate local control field")
        result[key] = value
    return result


def _decode(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_unique)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise a.AuthorityError("invalid local control message") from exc


def _receive(peer: socket.socket, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("local control exchange timed out")
        peer.settimeout(remaining)
        chunk = peer.recv(count - len(data))
        if not chunk:
            raise a.AuthorityError("local control connection closed")
        data.extend(chunk)
    return bytes(data)


def request(
    state_dir: Path, action: str, recovery_file: Path | None, reuse: bool
) -> dict[str, Any]:
    root = a._root(state_dir, create=False)
    endpoint = root / "control.sock"
    identity = _socket_identity(endpoint)
    value: dict[str, object] = {"root": str(root), "action": action}
    if action == "init":
        if recovery_file is None:
            raise a.AuthorityError(
                "select an external private --recovery-file destination"
            )
        value.update(
            recovery_file=str(recovery_file.expanduser().absolute()), reuse=reuse
        )
    with socket.socket(socket.AF_UNIX) as peer:
        deadline = time.monotonic() + EXCHANGE_TIMEOUT
        peer.settimeout(EXCHANGE_TIMEOUT)
        peer.connect(str(endpoint))
        if _peer_uid(peer) != os.geteuid() or _socket_identity(endpoint) != identity:
            raise a.AuthorityError("local control server identity changed")
        peer.sendall(_frame(value))
        size = struct.unpack("!I", _receive(peer, 4, deadline))[0]
        if size > MAX_MESSAGE:
            raise a.AuthorityError("local control message is too large")
        reply = _decode(_receive(peer, size, deadline))
    if reply.get("ok") is not True:
        raise a.AuthorityError(str(reply.get("error", "local control failed")))
    result = reply.get("result")
    if not isinstance(result, dict) or type(result.get("initialized")) is not bool:
        raise a.AuthorityError("invalid local control result")
    return result


class _Listener(socket.socket):
    def __init__(self) -> None:
        super().__init__(socket.AF_UNIX, socket.SOCK_STREAM)
        self.pending: set[socket.socket] = set()

    def accept(self) -> tuple[socket.socket, Any]:
        peer, address = super().accept()
        # Own the descriptor before sock_accept's future can be cancelled.
        self.pending.add(peer)
        return peer, address


class _Protocol(asyncio.StreamReaderProtocol):
    """Keep even admission-rejected transports alive until connection_lost."""

    def __init__(self, control: LocalControl) -> None:
        self.control = control
        self.transport: asyncio.Transport | None = None
        super().__init__(asyncio.StreamReader(limit=MAX_MESSAGE), control.accept)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.Transport, transport)
        self.control.transports.add(self.transport)
        self.control.drained.clear()
        super().connection_made(transport)

    def connection_lost(self, exc: Exception | None) -> None:
        try:
            super().connection_lost(exc)
        finally:
            if self.transport is not None:
                self.control.transports.discard(self.transport)
                self.transport = None
            if not self.control.transports:
                self.control.drained.set()


class LocalControl:
    def __init__(self, root: Path) -> None:
        self.root = a._root(root, create=False)
        self.path = self.root / "control.sock"
        self.identity: tuple[int, int] | None = None
        self.listener: _Listener | None = None
        self.acceptor: asyncio.Task[None] | None = None
        self.connections: set[asyncio.StreamWriter] = set()
        self.handlers: set[asyncio.Task[Any]] = set()
        self.transports: set[asyncio.Transport] = set()
        self.drained = asyncio.Event()
        self.drained.set()
        self.work: asyncio.Task[dict[str, Any]] | None = None
        self.closed = False

    async def start(self) -> None:
        # The caller already owns the persistent authority lock, so no other
        # legitimate instance for this exact root can own this stale socket.
        if self.path.exists() or self.path.is_symlink():
            _socket_identity(self.path)
            self.path.unlink()
        self.listener = _Listener()
        try:
            self.listener.bind(str(self.path))
        except OSError as exc:
            raise a.AuthorityError(
                f"cannot bind private control socket {self.path}; use a short local "
                f"server-data path on a Unix-socket-capable filesystem: {exc}"
            ) from exc
        self.path.chmod(0o600)
        self.identity = _socket_identity(self.path)
        self.listener.listen(MAX_CONNECTIONS)
        self.listener.setblocking(False)
        self.acceptor = asyncio.create_task(self.listen())
        self.acceptor.add_done_callback(self.acceptor_finished)

    def acceptor_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            task.get_loop().call_exception_handler(
                {"message": "Arbiter local control listener failed", "exception": error}
            )

    async def listen(self) -> None:
        assert self.listener is not None
        loop = asyncio.get_running_loop()
        while not self.closed:
            peer = None
            try:
                peer, _ = await loop.sock_accept(self.listener)
                if self.closed or len(self.connections) >= MAX_CONNECTIONS:
                    peer.close()
                    continue
                await loop.connect_accepted_socket(lambda: _Protocol(self), peer)
            except OSError as exc:
                if peer is not None:
                    peer.close()
                if exc.errno not in _TRANSIENT:
                    raise
                await asyncio.sleep(1)
            except BaseException:
                if peer is not None:
                    peer.close()
                raise
            finally:
                if peer is not None:
                    self.listener.pending.discard(peer)

    def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.closed or len(self.connections) >= MAX_CONNECTIONS:
            # No request task or reply queue for rejected connections. The
            # protocol retains the transport until its close callback runs.
            writer.transport.abort()
            return
        self.connections.add(writer)
        task = asyncio.create_task(self.handle(reader, writer))
        self.handlers.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self.handlers.discard(done)
            # Cancellation may arrive before handle() executes its first line.
            self.connections.discard(writer)

        task.add_done_callback(finished)

    def dispatch(self, value: dict[str, Any]) -> dict[str, Any]:
        action = value.get("action")
        keys = (
            {"root", "action", "recovery_file", "reuse"}
            if action == "init"
            else {"root", "action"}
        )
        if value.get("root") != str(self.root) or set(value) != keys:
            raise a.AuthorityError("invalid local control request")
        if action == "init":
            if (
                not isinstance(value["recovery_file"], str)
                or type(value["reuse"]) is not bool
            ):
                raise a.AuthorityError("invalid local setup arguments")
            record = a._initialize_locked(
                self.root, Path(value["recovery_file"]), value["reuse"]
            )
        elif action == "status":
            record = a._current_record(self.root)
        else:
            raise a.AuthorityError("unsupported local control action")
        return record.to_dict() if record else {"initialized": False}

    async def exchange(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if _peer_uid(writer.get_extra_info("socket")) != os.geteuid():
            raise a.AuthorityError("local control caller is not the instance user")
        if _socket_identity(self.path) != self.identity:
            raise a.AuthorityError("local control endpoint was replaced")
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        if size > MAX_MESSAGE:
            raise a.AuthorityError("local control message is too large")
        value = _decode(await reader.readexactly(size))
        if self.work is not None and not self.work.done():
            writer.write(
                _frame(
                    {
                        "ok": False,
                        "error": "local authority work is busy; try again later",
                    }
                )
            )
            await writer.drain()
            return
        # Shield the actual work: a disconnect/deadline must not free capacity
        # or release the instance lock while file/SQLite work is still running.
        self.work = asyncio.create_task(asyncio.to_thread(self.dispatch, value))
        self.work.add_done_callback(
            lambda done: done.exception() if not done.cancelled() else None
        )
        try:
            result = await asyncio.shield(self.work)
            response: dict[str, object] = {"ok": True, "result": result}
        except (OSError, ValueError, sqlite3.Error) as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write(_frame(response))
        await writer.drain()

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await asyncio.wait_for(self.exchange(reader, writer), EXCHANGE_TIMEOUT)
        except (
            OSError,
            ValueError,
            sqlite3.Error,
            asyncio.IncompleteReadError,
            asyncio.TimeoutError,
        ):
            # Invalid or unauthenticated callers receive no state or error text.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def close(self) -> None:
        self.closed = True
        # Own and join the accept operation as well as established transports.
        # asyncio.Server can leave an already-scheduled accept task creating a
        # transport after Server.close(), before connection_made can track it.
        if self.listener is not None:
            self.listener.close()
        if self.acceptor is not None:
            self.acceptor.cancel()
            await asyncio.gather(self.acceptor, return_exceptions=True)
        if self.listener is not None:
            for peer in self.listener.pending:
                peer.close()
            self.listener.pending.clear()
        for transport in tuple(self.transports):
            transport.abort()
        for task in tuple(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)
        await self.drained.wait()
        cancelled = False
        while self.work is not None and not self.work.done():
            try:
                await asyncio.shield(self.work)
            except asyncio.CancelledError:
                cancelled = True
            except (OSError, ValueError, sqlite3.Error):
                pass
        if self.identity is not None and self.path.exists():
            if _socket_identity(self.path) == self.identity:
                self.path.unlink()
        if cancelled:
            raise asyncio.CancelledError


@asynccontextmanager
async def running(root: Path) -> AsyncGenerator[None, None]:
    if os.name == "nt":
        yield
        return
    control = LocalControl(root)
    try:
        await control.start()
        yield
    finally:
        closing = asyncio.create_task(control.close())
        cancelled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        await closing
        if cancelled:
            raise asyncio.CancelledError
