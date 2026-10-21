"""Bounded live authority control over OS-authenticated Windows pipes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import struct
from typing import Any

from . import authority as a, generations, local_control as c, windows_pipe as w

_LOCATOR = "control-pipe.json"


def _locator(root: Path) -> tuple[str, tuple[int, int]]:
    path = a._root(root, create=False) / _LOCATOR
    a._check_path(path)
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        data = stream.read(513)
    current = path.lstat()
    identity = info.st_dev, info.st_ino
    if identity != (current.st_dev, current.st_ino) or len(data) > 512:
        raise a.AuthorityError("local control locator changed or is too large")
    a._check_path(path)
    value = c._decode(data)
    if (
        set(value) != {"version", "name"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["name"], str)
    ):
        raise a.AuthorityError("invalid local control locator")
    w._local_name(value["name"])
    return value["name"], identity


def _message(data: bytes) -> dict[str, Any]:
    if (
        len(data) < 4
        or len(data) > c.MAX_MESSAGE + 4
        or struct.unpack("!I", data[:4])[0] != len(data) - 4
    ):
        raise a.AuthorityError("invalid local control message length")
    return c._decode(data[4:])


def request(root: Path, value: dict[str, object]) -> dict[str, Any]:
    name, identity = _locator(root)
    api = w.Native()

    async def exchange() -> dict[str, Any]:
        pipe = api.open(name)
        try:
            if _locator(root) != (name, identity):
                raise a.AuthorityError("local control endpoint was replaced")
            await pipe.write(c._frame(value))
            reply = _message(await pipe.read())
            # The server must know its reply was consumed before disconnecting:
            # DisconnectNamedPipe discards any unread outbound bytes.
            await pipe.write(b"OK")
            try:
                if await pipe.read():
                    raise a.AuthorityError("unexpected local control reply")
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {109, 233}:
                    raise
            return reply
        finally:
            pipe.close()

    return c._result(asyncio.run(asyncio.wait_for(exchange(), c.EXCHANGE_TIMEOUT)))


class WindowsControl(c.AuthorityControl):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.api = w.Native()
        self.path = self.root / _LOCATOR
        self.name = r"\\.\pipe\arbiter-" + secrets.token_hex(16)
        self.identity: tuple[int, int] | None = None
        self.pipes: list[w.Pipe] = []
        self.handlers: list[asyncio.Task[None]] = []
        self.closed = False

    async def start(self) -> None:
        if self.path.exists() or self.path.is_symlink():
            _locator(self.root)  # Only a validated reserved stale locator is replaced.
        # Own every native instance before publishing the unpredictable name.
        # Handles remain open through shutdown, so the namespace is never vacant.
        for index in range(c.MAX_CONNECTIONS):
            self.pipes.append(self.api.create(self.name, first=index == 0))
        generations._write_control(
            self.root, _LOCATOR, {"version": 1, "name": self.name}
        )
        _, self.identity = _locator(self.root)
        for pipe in self.pipes:
            task = asyncio.create_task(self.serve(pipe))
            self.handlers.append(task)
            task.add_done_callback(self.acceptor_finished)

    def dispatch(self, value: dict[str, Any]) -> dict[str, Any]:
        # Filesystem/ACL validation and SQLite work share the one retained job.
        if _locator(self.root) != (self.name, self.identity):
            raise a.AuthorityError("local control endpoint was replaced")
        return super().dispatch(value)

    async def exchange(self, pipe: w.Pipe) -> None:
        self.api.verify_acl(pipe.handle)
        data = await pipe.read()
        self.api.verify_client(pipe.handle)
        response = await self.response(_message(data))
        await pipe.write(c._frame(response))
        if await pipe.read() != b"OK":
            raise a.AuthorityError("invalid local control acknowledgement")

    async def serve(self, pipe: w.Pipe) -> None:
        while not self.closed:
            try:
                await pipe.connect()
                await asyncio.wait_for(self.exchange(pipe), c.EXCHANGE_TIMEOUT)
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {109, 232, 233, 234, 995}:
                    raise
            except (ValueError, asyncio.TimeoutError):
                pass
            finally:
                if not self.api.k.DisconnectNamedPipe(pipe.handle) and not self.closed:
                    if getattr(w.ct, "get_last_error")() != 233:
                        raise self.api.error()

    async def close(self) -> None:
        self.closed = True
        for task in self.handlers:
            task.cancel()
        joined = asyncio.gather(*self.handlers, return_exceptions=True)
        cancelled = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                cancelled = True
        await joined
        cancelled = await self.finish_work() or cancelled
        for pipe in self.pipes:
            pipe.close()
        if self.identity is not None:
            try:
                current = _locator(self.root)
            except FileNotFoundError:
                pass
            else:
                if current == (self.name, self.identity):
                    self.path.unlink()
        if cancelled:
            raise asyncio.CancelledError
