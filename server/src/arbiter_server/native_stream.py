"""Shared private carrier credits and serialized memory-BIO stream pump.

This module does not select credentials, authorize callers or expose a route.
Stream consumers must consume each returned chunk before requesting another,
and charge any retained application copies to their own bounded owner.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from OpenSSL import SSL

if TYPE_CHECKING:
    from .pairing_carrier import PairingProtocol

CHUNK = 16384
RECORD_RESERVE = 36 * 1024


class CarrierCredits:
    """One carrier ledger; fixed control remains cumulative, streams move."""

    def __init__(self, maximum: int, lock: Any) -> None:
        self.maximum = maximum
        self.lock = lock
        self.fed = self.pending = self.queued = 0
        self.output = self.application = self.adapter = 0
        self.streaming = False

    def check(self, extra: int = 0) -> None:
        if (
            extra < 0
            or self.fed
            + self.pending
            + 2 * self.queued
            + self.output
            + self.application
            + self.adapter
            + extra
            > self.maximum
        ):
            raise ValueError("Control TLS pending data exceeded limit")

    def reserve_input(self, size: int) -> None:
        with self.lock:
            if size < 0 or (
                not self.streaming and self.fed + size > max(1, self.maximum // 2)
            ):
                raise ValueError("Control TLS input exceeded limit")
            charge = size * (2 if self.streaming else 1)
            self.check(charge)
            self.fed += charge

    def update_pending(self, size: int, *, wants_input: bool = False) -> None:
        with self.lock:
            if size < 0:
                raise ValueError("Control TLS pending data exceeded limit")
            self.pending = size
            if self.streaming and wants_input and not size:
                # pending() cannot reveal an incomplete encrypted record. Never
                # discard its conservative record-and-copy reserve between feeds.
                self.fed = RECORD_RESERVE
            self.check()


class TLSStream:
    """One in-flight operation, on the carrier's existing bounded crypto pool.

    Credits are not cleared by cancellation: the actual worker still owns its
    inputs and lease. The closed carrier is discarded only after that owner
    ends. Outgoing BIO data drains before any new input or application write.
    """

    def __init__(self, carrier: PairingProtocol, connection: SSL.Connection) -> None:
        self.carrier, self.connection = carrier, connection
        self._calling = False
        self._input = b""
        self._offset = 0

    async def _call(
        self, operation: Callable[[], Any], *, application: int | None = None
    ) -> Any:
        with self.carrier.credits.lock:
            if self._calling or (
                self.carrier.lease is not None and self.carrier.lease._busy
            ):
                raise ValueError("Concurrent TLS operation")
            if application is not None:
                self.carrier.credits.application = application
                self.carrier.credits.check()
            self._calling = True
        try:
            return await self._pump(operation)
        finally:
            self._calling = False

    async def _pump(self, operation: Callable[[], Any]) -> Any:
        carrier, connection = self.carrier, self.connection
        assert carrier.lease is not None
        first, drained = True, False
        result: Any = None
        wants: str | None = None
        while True:
            limit = max(1, min(CHUNK, carrier.frame_limit // 2))
            with carrier.credits.lock:
                if carrier.credits.streaming:
                    carrier.credits.output = 3 * limit
                    assert carrier.transport is not None
                    carrier.credits.check(carrier.transport.get_write_buffer_size())

            def step() -> tuple[Any, str | None, bytes | None]:
                value: Any = None
                need: str | None = None
                if first:
                    try:
                        value = operation()
                    except SSL.WantReadError:
                        need = "read"
                    except SSL.WantWriteError:
                        need = "write"
                try:
                    chunk = connection.bio_read(limit)
                except SSL.WantReadError:
                    chunk = None
                carrier.credits.update_pending(
                    connection.pending(), wants_input=need == "read"
                )
                return value, need, chunk

            value, need, chunk = await carrier.responder._run(carrier.lease, step)
            if first:
                result, wants = value, need
                first = False
            if chunk is not None:
                drained = True
                await carrier.send(chunk)
                del chunk
                continue
            carrier.credits.output = 0
            if wants is None:
                return result
            if wants == "write":
                if not drained:
                    raise ValueError("TLS write made no progress")
                first, drained = True, False
                continue
            await self._feed()
            first, drained = True, False

    async def _feed(self) -> None:
        carrier = self.carrier
        assert carrier.lease is not None
        if not self._input:
            self._input = await carrier.receive()
            self._offset = 0
            carrier.credits.adapter = len(self._input)
            carrier.credits.check()
        part = self._input[self._offset : self._offset + CHUNK]
        carrier.reserve_control_tls_input(len(part))
        written = await carrier.responder._run(
            carrier.lease, lambda: self.connection.bio_write(part)
        )
        if not isinstance(written, int) or not 0 < written <= len(part):
            raise ValueError("TLS input made no progress")
        self._offset += written
        if self._offset == len(self._input):
            self._input = b""  # Drop the adapter owner before another read.
            carrier.credits.adapter = 0

    async def read(self, maximum: int = CHUNK) -> bytes:
        if not 1 <= maximum <= CHUNK:
            raise ValueError("TLS application chunk exceeded limit")
        return await self._call(
            lambda: self.connection.recv(maximum), application=2 * maximum
        )

    async def write(self, data: bytes) -> None:
        if not 1 <= len(data) <= CHUNK:
            raise ValueError("TLS application chunk exceeded limit")
        view = memoryview(data)
        while view:
            written = await self._call(
                lambda: self.connection.send(view), application=2 * len(data)
            )
            if not isinstance(written, int) or not 0 < written <= len(view):
                raise ValueError("TLS output made no progress")
            view = view[written:]

    async def close(self) -> None:
        try:
            await self._call(self.connection.shutdown)
        finally:
            self.carrier.abort()
