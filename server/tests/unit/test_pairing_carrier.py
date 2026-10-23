"""Real Uvicorn/Noise peers and transport barriers test carrier admission."""

import asyncio
import base64
from contextlib import contextmanager
import json
import os
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.applications import Starlette
from wsproto import ConnectionType, WSConnection, events

from arbiter_server.authority import initialize_authority
from arbiter_server.limits import NativeTransportLimitsConfig as Limits
from arbiter_server.pairing import PairingResponder, first_payload
from arbiter_server.pairing_carrier import (
    PAIRING_PATH,
    PairingProtocol,
    protocol_factory,
)
from server.tests.unit.test_artifact_http_admission import live_server, wire_request
from server.tests.unit.test_enrollment_requests import advertisement
from server.tests.unit.test_native_http_protocol import _client
from server.tests.unit.test_pairing import approved, initiator


class Peer:
    def __init__(self, port, target, *, extensions=(), tail=b""):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.ws = WSConnection(ConnectionType.CLIENT)
        self.socket.sendall(
            self.ws.send(
                events.Request(host="localhost", target=target, extensions=extensions)
            )
            + tail
        )

    def next(self):
        while True:
            pending = list(self.ws.events())
            if pending:
                return pending
            try:
                data = self.socket.recv(4096)
            except ConnectionResetError:
                return []
            if not data:
                return []
            self.ws.receive_data(data)

    def send(self, event):
        self.socket.sendall(self.ws.send(event))

    def message(self):
        result = bytearray()
        while True:
            pending = self.next()
            assert pending, "closed before complete reply"
            for event in pending:
                if isinstance(event, events.BytesMessage):
                    result.extend(event.data)
                    if event.message_finished:
                        return bytes(result)
                elif isinstance(event, events.CloseConnection):
                    raise AssertionError("closed before reply")

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def binary_frame(payload):
    return b"\x82" + bytes([0x80 | len(payload)]) + b"\0" * 4 + payload


@pytest.fixture
def server(tmp_path, private_windows_test_parent):
    @contextmanager
    def run(*, handler=None, **kwargs):
        app = cast(Starlette, _client(tmp_path, native_transport=Limits(**kwargs))._app)
        initialize_authority(
            tmp_path / "server" / "authority", recovery_file=tmp_path / "operator.json"
        )
        if handler is not None:
            app.state.native_websocket_protocol = protocol_factory(
                app.state.pairing_responder, Limits(**kwargs), continue_pairing=handler
            )
        with live_server(
            app,
            ws=app.state.native_websocket_protocol,
            http=app.state.native_http_protocol,
            h11_max_incomplete_event_size=32768,
        ) as port:
            requests = app.state.enrollment_requests
            requests.open_window()
            pending = requests.register(json.dumps(advertisement()).encode())
            selector = pending.advertisement.selector
            secret = os.urandom(32)
            requests.control(
                "approve",
                dict(
                    selector=selector,
                    revision=pending.revision,
                    request_digest=pending.advertisement.digest,
                    psk=base64.urlsafe_b64encode(secret).decode().rstrip("="),
                    grants=[],
                ),
                set(),
            )
            yield app, port, requests.approved(
                selector
            ), PAIRING_PATH + selector + "/pair"

    return run


@pytest.mark.parametrize(
    "fragmented,coalesced", [(False, False), (True, False), (False, True)]
)
def test_real_server_noise_reply_and_fragmented_binary(server, fragmented, coalesced):
    with server() as (app, port, context, path):
        noise, first = initiator(context)
        with Peer(port, path, tail=binary_frame(first) if coalesced else b"") as peer:
            assert isinstance(peer.next()[0], events.AcceptConnection)
            if fragmented:
                peer.send(events.BytesMessage(data=first[:20], message_finished=False))
                peer.send(events.BytesMessage(data=first[20:]))
            elif not coalesced:
                peer.send(events.BytesMessage(data=first))
            assert bytes(noise.read_message(peer.message())) == first_payload(
                context.request.advertisement, server=True
            )
            assert wire_request(port, "GET", "/_health_")[0] == 200
            assert (
                app.state.enrollment_requests.approved(
                    context.request.advertisement.selector
                )
                is context
            )


@pytest.mark.parametrize(
    "bad", ["text", "oversized", "fragmented", "control", "wrong-psk", "unknown"]
)
def test_bad_carriers_close_generically_without_charging_anonymous_attempt(server, bad):
    with server() as (app, port, context, path):
        with Peer(
            port, path if bad != "unknown" else PAIRING_PATH + "X" * 22 + "/pair"
        ) as peer:
            assert isinstance(peer.next()[0], events.AcceptConnection)
            if bad == "text":
                peer.send(events.TextMessage(data="not binary"))
            elif bad == "oversized":
                peer.send(events.BytesMessage(data=b"x" * 65536))
            elif bad == "fragmented":
                peer.send(
                    events.BytesMessage(data=b"x" * 40000, message_finished=False)
                )
                peer.send(events.BytesMessage(data=b"x" * 30000))
            elif bad == "control":
                peer.socket.sendall(
                    b"".join(peer.ws.send(events.Ping(payload=b"x")) for _ in range(40))
                )
            else:
                _, first = initiator(context, secret=os.urandom(32))
                peer.send(events.BytesMessage(data=first))
            try:
                while peer.socket.recv(65536):
                    pass
            except ConnectionResetError:
                pass
            assert wire_request(port, "GET", "/_health_")[0] == 200
            state = app.state.pairing_responder._attempts.get(
                context.request.advertisement.selector
            )
            assert state is None or not state.digests


def test_overload_rejected_before_upgrade_and_capacity_recovers(server):
    with server(max_connections=1) as (_, port, _, path):
        with Peer(port, path) as peer, Peer(port, path) as second:
            assert isinstance(peer.next()[0], events.AcceptConnection)
            rejection = second.next()[0]
            assert isinstance(rejection, events.RejectConnection)
            assert rejection.status_code == 503
            assert wire_request(port, "GET", "/_health_")[0] == 200
        deadline = time.monotonic() + 2
        while True:
            replacement = Peer(port, path)
            try:
                if isinstance(replacement.next()[0], events.AcceptConnection):
                    break
                assert time.monotonic() < deadline
            finally:
                replacement.close()


def test_absolute_deadline_pings_do_not_extend_and_health_survives(server):
    with server(handshake_timeout_seconds=0.15) as (_, port, _, path):
        with Peer(port, path) as peer:
            assert isinstance(peer.next()[0], events.AcceptConnection)
            peer.send(events.Ping(payload=b"alive"))
            assert isinstance(peer.next()[0], events.Pong)
            assert peer.socket.recv(1) == b""
            assert wire_request(port, "GET", "/_health_")[0] == 200


@pytest.mark.parametrize("case", ["extension", "near", "query"])
def test_upgrade_header_policy(server, case):
    with server() as (_, port, _, path):
        wire = (
            f"GET {path + ('?secret=no' if case == 'query' else '')} HTTP/1.1\r\n"
            "Host: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        )
        wire += (
            "Sec-WebSocket-Extensions: permessage-deflate\r\n"
            if case == "extension"
            else ""
        )
        wire += "X-Padding: " + "x" * 30000 + "\r\n" if case == "near" else ""
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall((wire + "\r\n").encode())
            assert peer.recv(4096).startswith(b"HTTP/1.1 101 ") == (case == "near")
        assert wire_request(port, "GET", "/_health_")[0] == 200


class Transport:
    def __init__(self):
        self.output = bytearray()
        self.closed = False
        self.buffered = 0
        self.paused = False

    def write(self, data):
        self.output.extend(data)

    def abort(self):
        self.closed = True

    def close(self):
        self.closed = True

    def get_write_buffer_size(self):
        return self.buffered

    def set_write_buffer_limits(self, **kwargs):
        pass

    def pause_reading(self):
        self.paused = True

    def resume_reading(self):
        self.paused = False


def setup_protocol(context=None, **limits):
    requests, context, _ = approved() if context is None else context
    responder = PairingResponder(requests, Limits(**limits))
    transport = Transport()
    protocol = PairingProtocol(
        None,
        SimpleNamespace(connections=set(), tasks=set()),
        {},
        responder=responder,
        limits=Limits(**limits),
    )
    protocol.connection_made(cast(Any, transport))
    client = WSConnection(ConnectionType.CLIENT)
    request = client.send(
        events.Request(
            host="localhost",
            target=PAIRING_PATH + context.request.advertisement.selector + "/pair",
        )
    )
    protocol.data_received(request)
    client.receive_data(bytes(transport.output))
    assert isinstance(next(client.events()), events.AcceptConnection)
    transport.output.clear()
    return responder, context, protocol, transport, client


def test_coalesced_messages_cannot_build_an_unbounded_receive_queue():
    async def run():
        responder, _, protocol, transport, client = setup_protocol()
        try:
            protocol.data_received(
                b"".join(
                    client.send(events.BytesMessage(data=b"x")) for _ in range(100)
                )
            )
            assert transport.closed and protocol.queue.qsize() <= 1
        finally:
            protocol.connection_lost(None)
            responder.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind", ["outbound-budget", "backpressure", "shutdown", "inbound-budget"]
)
def test_byte_budgets_backpressure_and_shutdown_release_carriers(kind):
    async def run():
        responder, _, protocol, transport, _ = setup_protocol(
            handshake_timeout_seconds=0.05
        )
        try:
            if kind == "outbound-budget":
                transport.buffered = 256 * 1024
                with pytest.raises(ValueError):
                    await protocol.send(b"x")
            elif kind == "inbound-budget":
                protocol.data_received(b"x" * (256 * 1024))
                assert transport.closed
            elif kind == "shutdown":
                protocol.shutdown()
                assert transport.closed
            else:
                protocol.pause_writing()
                task = asyncio.create_task(protocol.send(b"x"))
                await asyncio.sleep(0)
                assert not task.done()
                await asyncio.sleep(0.08)
                with pytest.raises(ValueError):
                    await task
                assert transport.closed
            protocol.connection_lost(None)
            assert not responder._leases
        finally:
            protocol.connection_lost(None)
            responder.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_real_encrypted_continuation_and_immutable_duplicate(server):
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def continuation(channel):
        calls.append(channel.session.handshake_hash)
        entered.set()
        assert await channel.receive() == b"client proof placeholder"
        await channel.send(b"server proof placeholder")
        while not release.is_set():
            await asyncio.sleep(0.001)

    with server(handler=continuation) as (_, port, context, path):
        with Peer(port, path) as peer:
            duplicate = None
            try:
                assert isinstance(peer.next()[0], events.AcceptConnection)
                noise, first = initiator(context)
                peer.send(events.BytesMessage(data=first))
                reply = peer.message()
                assert bytes(noise.read_message(reply)) == first_payload(
                    context.request.advertisement, server=True
                )
                assert entered.wait(1)
                duplicate = Peer(port, path)
                assert isinstance(duplicate.next()[0], events.AcceptConnection)
                duplicate.send(events.BytesMessage(data=first))
                assert duplicate.message() == reply
                assert len(calls) == 1
                peer.send(
                    events.BytesMessage(data=noise.encrypt(b"client proof placeholder"))
                )
                assert (
                    bytes(noise.decrypt(peer.message())) == b"server proof placeholder"
                )
                assert len(calls) == 1
            finally:
                release.set()
                if duplicate is not None:
                    duplicate.close()


def test_disconnect_during_real_crypto_retains_capacity_until_thread_finishes(
    server, monkeypatch
):
    from noise.connection import NoiseConnection

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = NoiseConnection.read_message

    def blocked(noise, data):
        if threading.current_thread().name == "arbiter-operation":
            entered.set()
            assert release.wait(3)
            try:
                return original(noise, data)
            finally:
                finished.set()
        return original(noise, data)

    monkeypatch.setattr(NoiseConnection, "read_message", blocked)
    with server(max_connections=1) as (app, port, context, path):
        peer = Peer(port, path)
        assert isinstance(peer.next()[0], events.AcceptConnection)
        try:
            _, first = initiator(context)
            peer.send(events.BytesMessage(data=first))
            assert entered.wait(1)
            peer.close()
            with Peer(port, path) as second:
                rejection = second.next()[0]
                assert isinstance(rejection, events.RejectConnection)
                assert rejection.status_code == 503
            assert wire_request(port, "GET", "/_health_")[0] == 200
            assert len(app.state.pairing_responder._leases) == 1
        finally:
            release.set()
            peer.close()
        assert finished.wait(1)
        deadline = time.monotonic() + 2
        while app.state.pairing_responder._leases:
            assert time.monotonic() < deadline
            time.sleep(0.001)


def test_real_server_shutdown_closes_idle_native_carrier(server):
    with server() as (app, port, _, path):
        peer = Peer(port, path)
        assert isinstance(peer.next()[0], events.AcceptConnection)
    try:
        assert peer.socket.recv(1) == b""
        assert not app.state.pairing_responder._leases
    finally:
        peer.close()


def test_negative_native_framing_settings_are_rejected():
    for name in (
        "max_headers_bytes",
        "max_websocket_message_bytes",
        "max_buffered_bytes",
        "max_control_frames_per_second",
        "max_control_bytes_per_second",
    ):
        for value in (0, -1, True, 1.5):
            with pytest.raises(ValueError):
                Limits(**cast(Any, {name: value}))


def test_partial_headers_reserve_before_parse_and_transfer_original_deadline(server):
    with server(max_connections=1, handshake_timeout_seconds=0.5) as (
        app,
        port,
        _,
        path,
    ):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as first:
            first.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n".encode())
            until = time.monotonic() + 1
            while not app.state.pairing_responder._leases:
                assert time.monotonic() < until
                time.sleep(0.001)
            (lease,) = app.state.pairing_responder._leases
            original = lease.deadline
            with socket.create_connection(("127.0.0.1", port), timeout=2) as second:
                second.sendall(f"GET {path} HTTP/1.1\r\n".encode())
                assert b"503" in second.recv(4096)
            assert wire_request(port, "GET", "/_health_")[0] == 200
            time.sleep(0.1)
            first.sendall(
                b"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            )
            assert first.recv(4096).startswith(b"HTTP/1.1 101 ")
            assert tuple(app.state.pairing_responder._leases) == (lease,)
            assert lease.deadline == original
            assert first.recv(1) == b""
        replacement = Peer(port, path)
        try:
            assert isinstance(replacement.next()[0], events.AcceptConnection)
        finally:
            replacement.close()


def test_initial_incomplete_and_complete_oversized_headers_never_reach_h11(
    server, monkeypatch
):
    from uvicorn.protocols.http.h11_impl import H11Protocol

    calls = []
    original = H11Protocol.data_received

    def parsed(protocol, data):
        if PAIRING_PATH.encode() in data:
            calls.append(len(data))
        return original(protocol, data)

    monkeypatch.setattr(H11Protocol, "data_received", parsed)
    with server(handshake_timeout_seconds=0.1) as (app, port, _, path):
        for suffix in (b"", b"\r\n\r\n"):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
                peer.sendall(
                    f"GET {path} HTTP/1.1\r\nX: ".encode() + b"x" * 100000 + suffix
                )
                assert b"101" not in peer.recv(4096)
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall(b"G")
            assert peer.recv(1) == b""
        assert calls == []
        assert wire_request(port, "GET", "/_health_")[0] == 200
        ordinary = (
            f"GET {PAIRING_PATH}ordinary HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        )
        upgrade = (
            (
                f"GET {path} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nX-Pad: "
            ).encode()
            + b" " * 100000
            + b"\r\n\r\n"
        )
        for pipelined in (False, True):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
                peer.sendall(ordinary + upgrade[:64] if pipelined else ordinary)
                if not pipelined:
                    peer.recv(4096)
                try:
                    peer.sendall(upgrade[64:] if pipelined else upgrade)
                    assert b"101" not in peer.recv(4096)
                except OSError:
                    pass
        assert max(calls) < 1024
        deadline = time.monotonic() + 1
        while app.state.pairing_responder._leases:
            assert time.monotonic() < deadline
            time.sleep(0.001)


def test_ordinary_http_body_outlives_native_decision_deadline(server):
    import http.client

    with server(handshake_timeout_seconds=0.05) as (_, port, _, _):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        body = json.dumps({"args": {"text": "ready", "times": 1}}).encode()
        try:
            connection.putrequest("POST", "/api/v1/operations/echo:repeat")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders()
            time.sleep(0.15)
            connection.send(body)
            response = connection.getresponse()
            assert response.status == 200
            assert b"ready" in response.read()
        finally:
            connection.close()
