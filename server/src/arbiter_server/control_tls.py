"""Server-authenticated fixed control transport; never dispatches HTTP or activates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography import x509
from OpenSSL import SSL

from .pairing_carrier import PairingProtocol
from .pairing_identity import load_pairing_identity

ALPN = b"arbiter-control-v1"
MAX_CONTROL = 65536


def _connection(root: Path) -> SSL.Connection:
    material = load_pairing_identity(root)
    if material is None:
        raise ValueError("Control identity unavailable")
    credential, _ = material
    context = SSL.Context(SSL.TLS_SERVER_METHOD)
    context.set_min_proto_version(SSL.TLS1_3_VERSION)
    context.set_max_proto_version(SSL.TLS1_3_VERSION)
    context.set_options(SSL.OP_NO_TICKET)
    context.set_session_cache_mode(SSL.SESS_CACHE_OFF)
    context.set_verify(SSL.VERIFY_NONE, None)
    context.use_certificate(x509.load_der_x509_certificate(credential.certificate_der))
    context.use_privatekey(credential.private_key)
    context.check_privatekey()

    def select(connection: SSL.Connection, protocols: list[bytes]) -> bytes:
        if protocols != [ALPN]:
            raise ValueError("Invalid control protocol")
        return ALPN

    context.set_alpn_select_callback(select)
    connection = SSL.Connection(context, None)
    connection.set_accept_state()
    return connection


class ControlTLS:
    """One bounded exchange over the existing carrier and crypto ownership.

    Every BIO/crypto call is serialized through the carrier's shared worker pool.
    A cancelled worker retains its lease until actual completion. Outgoing BIO
    data is drained before accepting more input; no background read-ahead exists.
    """

    def __init__(self, carrier: PairingProtocol, connection: SSL.Connection) -> None:
        self.carrier, self.connection = carrier, connection

    @classmethod
    async def accept(cls, carrier: PairingProtocol, root: Path) -> ControlTLS:
        assert carrier.lease is not None
        connection = await carrier.responder._run(
            carrier.lease, lambda: _connection(root)
        )
        stream = cls(carrier, connection)
        await stream._call(connection.do_handshake)
        if connection.get_protocol_version() != SSL.TLS1_3_VERSION or (
            connection.get_alpn_proto_negotiated() != ALPN
        ):
            raise ValueError("Invalid control protocol")
        carrier.begin_control()
        return stream

    async def _call(self, operation: Callable[[], Any]) -> Any:
        carrier, connection = self.carrier, self.connection
        assert carrier.lease is not None
        first = True
        result: Any = None
        needs_input = False
        while True:

            def step() -> tuple[Any, bool, bytes | None]:
                operation_result: Any = None
                operation_needs_input = False
                if first:
                    try:
                        operation_result = operation()
                    except SSL.WantReadError:
                        operation_needs_input = True
                try:
                    # Leave headroom for the encrypted WebSocket frame and
                    # the transport's own write-buffer copy.
                    chunk = connection.bio_read(max(1, carrier.frame_limit // 2))
                except SSL.WantReadError:
                    chunk = None
                carrier.update_control_tls_pending(connection.pending())
                return operation_result, operation_needs_input, chunk

            step_result, step_needs_input, chunk = await carrier.responder._run(
                carrier.lease, step
            )
            if first:
                result, needs_input = step_result, step_needs_input
                first = False
            if chunk is not None:
                await carrier.send(chunk)
            if not needs_input:
                if chunk is None:
                    return result
                continue
            if chunk is not None:
                continue
            data = await carrier.receive()
            carrier.reserve_control_tls_input(len(data))
            await carrier.responder._run(
                carrier.lease, lambda: connection.bio_write(data)
            )
            del data  # BIO owns the input before the next carrier read.
            first = True

    async def receive(self) -> bytes:
        async def exact(size: int) -> bytes:
            result = bytearray()
            while len(result) < size:
                part = await self._call(
                    lambda: self.connection.recv(min(16384, size - len(result)))
                )
                if not part:
                    raise ValueError("Incomplete control")
                result.extend(part)
                del part
            return bytes(result)

        size = int.from_bytes(await exact(4), "big")
        if not 1 <= size <= min(MAX_CONTROL, self.carrier.frame_limit):
            raise ValueError("Control exceeded limit")
        return await exact(size)

    async def send(self, message: bytes) -> None:
        if not 1 <= len(message) <= min(MAX_CONTROL, self.carrier.frame_limit):
            raise ValueError("Control exceeded limit")
        await self._call(lambda: self.connection.send(len(message).to_bytes(4, "big")))
        view = memoryview(message)
        try:
            while view:
                written = await self._call(lambda: self.connection.send(view))
                view = view[written:]
        finally:
            view.release()

    def exporter(self) -> bytes:
        return self.connection.export_keying_material(
            b"EXPORTER-Channel-Binding", 32, None
        )
