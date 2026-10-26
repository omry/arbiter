"""Bounded RFC 6455 carrier for authenticated pairing and receipt control."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import re
from typing import Any, cast

from wsproto import ConnectionType, WSConnection, events
from wsproto.utilities import LocalProtocolError, RemoteProtocolError

from .limits import NativeTransportLimitsConfig
from .pairing import PairingLease, PairingResponder, PairingSession, ReplayedHandshake

PAIRING_PATH = "/api/v1/enrollment/requests/"
CONTROL_PATH = "/api/v1/native/control"
_TARGET = re.compile(re.escape(PAIRING_PATH) + r"([A-Za-z0-9_-]{22})/pair\Z")


class PairingChannel:
    def __init__(self, carrier: PairingProtocol, session: PairingSession) -> None:
        self._carrier = carrier
        self.session = session

    async def prove(
        self, root: Path, accounts: Callable[[], set[tuple[str, str]]]
    ) -> None:
        """Commit only after mutual proof and durable client readiness."""
        proof = await self._carrier.responder.prove_server_from_authority(
            self.session, root, datetime.now(timezone.utc)
        )
        await self._carrier.send(proof)
        await self._carrier.responder.prove_client(
            self.session, await self._carrier.receive()
        )
        result = await self._carrier.responder.prepare(self.session, root)
        await self.send(result)
        await self._carrier.responder.ready(self.session, await self.receive())
        receipt = await self._carrier.responder.activate(self.session, root, accounts)
        await self._carrier.send(receipt)

    async def receive(self) -> bytes:
        return await self._carrier.responder.message(
            self.session, await self._carrier.receive()
        )

    async def send(self, plaintext: bytes) -> None:
        encrypted = await self._carrier.responder.message(
            self.session, plaintext, encrypt=True
        )
        await self._carrier.send(encrypted)


class PairingProtocol(asyncio.Protocol):
    def __init__(
        self,
        config: Any,
        server_state: Any,
        app_state: dict[str, Any],
        *,
        responder: PairingResponder,
        limits: NativeTransportLimitsConfig,
        continue_pairing: Callable[[PairingChannel], Awaitable[None]] | None = None,
        continue_control: Callable[[PairingProtocol], Awaitable[None]] | None = None,
        lease: PairingLease | None = None,
        header_bytes: int = 0,
        initial_header: bytes = b"",
        _loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.responder = responder
        self.limits = replace(limits)
        self.continue_pairing = continue_pairing
        self.continue_control = continue_control
        self.loop = _loop or asyncio.get_running_loop()
        self.connections, self.tasks = server_state.connections, server_state.tasks
        self.transport: asyncio.Transport | None = None
        self.lease = lease
        self.task: asyncio.Task[None] | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.conn = WSConnection(ConnectionType.SERVER)
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self.closed_event = asyncio.Event()
        self.writable = asyncio.Event()
        self.writable.set()
        self.buffer = bytearray()
        self.initial_header, self.received = initial_header, header_bytes
        self.sent = 0
        self.control_started = self.loop.time()
        self.control_count = self.control_bytes = 0
        self.accepted = self.closed = False
        self.control = False
        self.control_started_once = False
        # TLS keeps a copy of accepted ciphertext while it may also retain
        # decoded plaintext.  Keep a conservative half-budget owner for that
        # input so both native sides fit inside the inclusive carrier budget.
        self._control_tls_fed = 0
        self._control_tls_pending = 0
        self._control_queued = 0
        self.frame_limit = min(
            limits.max_noise_frame_bytes,
            limits.max_websocket_message_bytes,
            limits.max_buffered_bytes // 4,
        )

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.Transport, transport)
        self.connections.add(self)
        try:
            self.lease = self.lease or self.responder.reserve()
            if self.lease.remaining_seconds <= 0:
                raise ValueError("Pairing deadline expired")
        except Exception:
            self.transport.write(
                b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            self.abort()
            return
        self.transport.set_write_buffer_limits(
            high=self.limits.max_buffered_bytes // 4, low=0
        )
        self.timer = self.loop.call_later(
            self.lease.remaining_seconds, cast(Callable[..., object], self.abort)
        )

    def connection_lost(self, exc: Exception | None) -> None:
        self.abort()
        if self.lease is not None:
            self.lease.close()
        self.connections.discard(self)

    def eof_received(self) -> None:
        self.abort()

    shutdown = eof_received

    def pause_writing(self) -> None:
        self.writable.clear()

    def resume_writing(self) -> None:
        self.writable.set()

    def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.closed_event.set()
        if self.timer is not None:
            self.timer.cancel()
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.buffer.clear()
        if not self.queue.empty():
            self.queue.get_nowait()
        self.writable.set()
        if self.transport is not None:
            self.transport.abort()

    def data_received(self, data: bytes) -> None:
        if self.closed:
            return
        if self.initial_header:
            data, self.initial_header = self.initial_header, b""
            chunk = len(data)
        else:
            self.received += len(data)
            chunk = max(1, min(4096, self.limits.max_buffered_bytes // 4))
        if len(data) > self.limits.max_buffered_bytes:
            self.abort()
            return
        if self.received > self.limits.max_handshake_bytes or (
            not self.accepted and self.received > self.limits.max_headers_bytes
        ):
            self.abort()
            return
        try:
            for offset in range(0, len(data), chunk):
                if self.closed:
                    break
                self.conn.receive_data(data[offset : offset + chunk])
                for event in self.conn.events():
                    if self.closed:
                        break
                    self._event(event)
        except (RemoteProtocolError, LocalProtocolError, ValueError):
            self.abort()

    def _event(self, event: events.Event) -> None:
        if isinstance(event, events.Request):
            match = _TARGET.fullmatch(event.target)
            control = event.target == CONTROL_PATH and self.continue_control is not None
            if (
                (match is None and not control)
                or event.extensions
                or event.subprotocols
            ):
                self.abort()
                return
            self._write(events.AcceptConnection())
            self.accepted = True
            self.control = control
            if control:
                self.frame_limit = min(
                    self.limits.max_websocket_message_bytes,
                    self.limits.max_buffered_bytes // 4,
                )
            selector = match[1] if match is not None else None
            self.task = self.loop.create_task(
                self._exchange(None if control else selector)
            )
            self.tasks.add(self.task)
            self.task.add_done_callback(self.tasks.discard)
        elif isinstance(event, events.BytesMessage):
            if (not self.control and not self.queue.empty()) or len(self.buffer) + len(
                event.data
            ) > self.frame_limit:
                self.abort()
                return
            frame_size = len(self.buffer) + len(event.data)
            if self.control:
                if (
                    self._control_tls_fed
                    + self._control_tls_pending
                    + 2 * self._control_queued
                    + 3 * frame_size
                    > self.limits.max_buffered_bytes
                ):
                    self.abort()
                    return
            self.buffer.extend(event.data)
            if event.message_finished:
                pending = self.queue.get_nowait() if not self.queue.empty() else b""
                pending_size = len(pending)
                frame_size = len(self.buffer)
                projected_live = 2 * pending_size + 3 * frame_size
                if (
                    projected_live + self._control_tls_fed + self._control_tls_pending
                    > self.limits.max_buffered_bytes
                ):
                    # pending + buffer + bytes(buffer) + pending+buffer remain
                    # live during coalescing. TLS-owned input is charged
                    # separately before allocating the concatenation.
                    self.abort()
                    return
                combined = pending + bytes(self.buffer)
                if self.control:
                    self._control_queued = len(combined)
                self.queue.put_nowait(combined)
                self.buffer.clear()
                assert self.transport is not None
                self.transport.pause_reading()
        elif isinstance(event, (events.Ping, events.Pong)):
            now = self.loop.time()
            if now - self.control_started >= 1:
                self.control_started, self.control_count, self.control_bytes = now, 0, 0
            self.control_count += 1
            self.control_bytes += len(event.payload) + 6
            if (
                self.control_count > self.limits.max_control_frames_per_second
                or self.control_bytes > self.limits.max_control_bytes_per_second
            ):
                self.abort()
            elif isinstance(event, events.Ping):
                self._write(event.response())
        else:
            self.abort()

    def _write(self, event: events.Event) -> None:
        if self.closed or self.transport is None:
            raise ValueError("Pairing carrier is closed")
        wire = self.conn.send(event)
        if (
            self.sent + len(wire) > self.limits.max_handshake_bytes
            or self.transport.get_write_buffer_size() + len(wire)
            > self.limits.max_buffered_bytes
        ):
            raise ValueError("Pairing carrier byte limit exceeded")
        self.sent += len(wire)
        self.transport.write(wire)

    async def receive(self) -> bytes:
        if self.closed or self.transport is None:
            raise ValueError("Pairing carrier is closed")
        if self.control and self.queue.empty():
            self.transport.resume_reading()
        if self.queue.empty():
            queue_task = self.loop.create_task(self.queue.get())
            closed_task = self.loop.create_task(self.closed_event.wait())
            try:
                done, _ = await asyncio.wait(
                    (queue_task, closed_task), return_when=asyncio.FIRST_COMPLETED
                )
                if queue_task not in done:
                    raise ValueError("Pairing carrier is closed")
                result = queue_task.result()
            finally:
                for task in (queue_task, closed_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(queue_task, closed_task, return_exceptions=True)
        else:
            result = await self.queue.get()
        if self.control:
            self._control_queued -= len(result)
        if not self.control:
            self.transport.resume_reading()
        return result

    async def send(self, wire: bytes) -> None:
        if not 1 <= len(wire) <= self.frame_limit:
            raise ValueError("Pairing frame exceeds limit")
        await self.writable.wait()
        self._write(events.BytesMessage(data=wire))
        await self.writable.wait()

    def begin_control(self) -> None:
        """Start exactly one fixed exchange budget after authenticated TLS."""
        if not self.control or self.control_started_once or self.closed:
            raise ValueError("Control is unavailable")
        assert self.lease is not None and self.timer is not None
        if self._control_queued > self._control_tls_budget:
            raise ValueError("Control TLS input exceeded limit")
        with self.responder._lock:
            self.responder._check(self.lease)
            self.responder._handshakes.discard(self.lease)
            self.lease.deadline = self.responder._clock() + 10.0
        self.control_started_once = True
        self.timer.cancel()
        self.timer = self.loop.call_later(10.0, cast(Callable[..., object], self.abort))

    @property
    def _control_tls_budget(self) -> int:
        return max(1, self.limits.max_buffered_bytes // 2)

    def reserve_control_tls_input(self, size: int) -> None:
        if (
            not self.control
            or size < 0
            or self._control_tls_fed + size > self._control_tls_budget
        ):
            raise ValueError("Control TLS input exceeded limit")
        self._control_tls_fed += size

    def update_control_tls_pending(self, size: int) -> None:
        if size < 0 or self._control_tls_fed + size > self.limits.max_buffered_bytes:
            raise ValueError("Control TLS pending data exceeded limit")
        self._control_tls_pending = size

    async def _exchange(self, selector: str | None) -> None:
        assert self.lease is not None
        lease = self.lease

        async def run() -> None:
            if selector is None:
                assert self.continue_control is not None
                await self.continue_control(self)
                return
            session = await self.responder.start(lease, selector, await self.receive())
            await self.send(session.response)
            if not isinstance(session, ReplayedHandshake) and self.continue_pairing:
                await self.continue_pairing(PairingChannel(self, session))

        try:
            if selector is None:
                # The timer covers handshake, then the independent control
                # budget. Cancellation still joins the same underlying owner.
                await run()
            else:
                await asyncio.wait_for(run(), lease.remaining_seconds)
            if not self.closed:
                self._write(events.CloseConnection(code=1000))
                assert self.transport is not None
                self.transport.close()
        except (Exception, asyncio.CancelledError):
            self.abort()


def protocol_factory(
    responder: PairingResponder,
    limits: NativeTransportLimitsConfig,
    *,
    continue_pairing: Callable[[PairingChannel], Awaitable[None]] | None = None,
    continue_control: Callable[[PairingProtocol], Awaitable[None]] | None = None,
) -> type[PairingProtocol]:
    class BoundPairingProtocol(PairingProtocol):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(
                *args,
                responder=responder,
                limits=limits,
                continue_pairing=continue_pairing,
                continue_control=continue_control,
                **kwargs,
            )

    return BoundPairingProtocol


def http_protocol_factory(
    responder: PairingResponder, limits: NativeTransportLimitsConfig
) -> type[Any]:
    from uvicorn.protocols.http.h11_impl import H11Protocol

    class GuardedHTTP(H11Protocol):
        def connection_made(self, transport: asyncio.Transport) -> None:
            super().connection_made(transport)
            self.header = bytearray()
            self.classified: bool | None = None
            self.native_lease = cast(PairingLease | None, None)
            self.forwarded = False
            self.deadline = self.loop.time() + limits.handshake_timeout_seconds
            self.decision_timer = self.loop.call_later(
                limits.handshake_timeout_seconds,
                cast(Callable[..., object], self.transport.abort),
            )

        def connection_lost(self, exc: Exception | None) -> None:
            self.decision_timer.cancel()
            if self.native_lease is not None:
                self.native_lease.close()
            super().connection_lost(exc)

        def reject(self, code: int = 400) -> None:
            self.decision_timer.cancel()
            self.transport.write(
                f"HTTP/1.1 {code} Rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode()
            )
            self.transport.close()

        def data_received(self, data: bytes) -> None:
            if self.forwarded:
                super().data_received(data)
                return
            prefix = bytes(self.header) + data[: limits.max_headers_bytes + 4]
            line_end = prefix.find(b"\r\n")
            if self.classified is None and line_end >= 0:
                request_line = prefix[:line_end].split(b" ")
                self.classified = len(request_line) == 3 and (
                    request_line[1].startswith(PAIRING_PATH.encode())
                    or request_line[1] == CONTROL_PATH.encode()
                )
                if self.classified:
                    try:
                        self.native_lease = responder.reserve()
                        self.native_lease.deadline = min(
                            self.native_lease.deadline, self.deadline
                        )
                        if self.native_lease.remaining_seconds <= 0:
                            raise ValueError("Pairing deadline expired")
                    except Exception:
                        self.reject(503)
                        return
                else:
                    self.decision_timer.cancel()
                    self.forwarded = True
            end = prefix.find(b"\r\n\r\n")
            if not self.forwarded and (
                (end < 0 and len(prefix) > limits.max_headers_bytes)
                or (end >= 0 and end + 4 > limits.max_headers_bytes)
            ):
                self.reject()
                return
            if self.forwarded or end >= 0:
                original = bytes(self.header) + data
                self.native_header_bytes = original[: end + 4]
                self.header.clear()
                remainder = original[end + 4 :] if self.classified else b""
                super().data_received(original[: end + 4] if remainder else original)
                if self.native_lease is not None:
                    self.reject()
                    return
                if remainder:
                    cast(Any, self.transport.get_protocol()).data_received(remainder)
            else:
                self.header.extend(data)

        def handle_websocket_upgrade(self, event: Any) -> None:
            if (
                self.native_lease is None
                or self.ws_protocol_class is None
                or self.cycle is not None
            ):
                self.reject()
                return
            self.ws_protocol_class = cast(
                Any,
                partial(
                    self.ws_protocol_class,
                    lease=self.native_lease,
                    header_bytes=len(self.native_header_bytes),
                    initial_header=self.native_header_bytes,
                ),
            )
            self.decision_timer.cancel()
            try:
                super().handle_websocket_upgrade(event)
            except BaseException:
                self.native_lease.close()
                self.transport.abort()
                raise
            self.native_lease = cast(Any, None)

    return GuardedHTTP
