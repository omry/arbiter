"""Inner TLS cancellation and actual worker ownership."""

import asyncio
from contextlib import suppress
import inspect
from pathlib import Path, PureWindowsPath
import threading
import time
from typing import Callable, cast

import pytest
from starlette.applications import Starlette
from wsproto import events

from arbiter_server import control_tls
from arbiter_server.authority import initialize_authority
from arbiter_server.errors import OperationError
from arbiter_server.limits import NativeTransportLimitsConfig as Limits
from arbiter_server.pairing_carrier import PAIRING_PATH, CONTROL_PATH
from server.tests.unit.test_control_carrier import setup
from server.tests.unit.test_pairing_carrier import Peer, Transport
from server.tests.unit.test_artifact_http_admission import live_server, wire_request
from server.tests.unit.test_native_http_protocol import _client


async def _real_tls_pair(
    root: Path,
    budget: int,
    *,
    input_chunk_limit: int | None = None,
    coalesce_input: bool = False,
):
    async def waiting(_carrier):
        await asyncio.Event().wait()

    owner, carrier, transport, ws = setup(waiting, max_buffered_bytes=budget)
    context = control_tls.SSL.Context(control_tls.SSL.TLS_CLIENT_METHOD)
    context.set_min_proto_version(control_tls.SSL.TLS1_3_VERSION)
    context.set_max_proto_version(control_tls.SSL.TLS1_3_VERSION)
    context.set_verify(control_tls.SSL.VERIFY_NONE, None)
    context.set_alpn_protos([control_tls.ALPN])
    client = control_tls.SSL.Connection(context, None)
    client.set_connect_state()
    original_send, original_receive = carrier.send, carrier.receive
    outgoing = bytearray()

    async def send(wire: bytes) -> None:
        await original_send(wire)
        ws.receive_data(bytes(transport.output))
        transport.output.clear()
        for event in ws.events():
            assert isinstance(event, events.BytesMessage)
            client.bio_write(event.data)

    async def receive():
        try:
            client.do_handshake()
        except control_tls.SSL.WantReadError:
            pass
        while True:
            try:
                outgoing.extend(client.bio_read(65536))
            except control_tls.SSL.WantReadError:
                break
        assert outgoing
        chunk_limit = input_chunk_limit or carrier.frame_limit
        frames = []
        while outgoing and (coalesce_input or not frames):
            data = bytes(outgoing[:chunk_limit])
            del outgoing[:chunk_limit]
            frames.append(ws.send(events.BytesMessage(data=data)))
        carrier.loop.call_soon(carrier.data_received, b"".join(frames))
        return await original_receive()

    carrier.send, carrier.receive = send, receive
    try:
        stream = await control_tls.ControlTLS.accept(carrier, root)
    except BaseException:
        carrier.connection_lost(None)
        owner.close()
        raise
    return owner, carrier, stream, client


def _client_recv(connection, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        result.extend(connection.recv(size - len(result)))
    return bytes(result)


def _client_send(connection, data: bytes) -> None:
    remaining = data
    while remaining:
        remaining = remaining[connection.send(remaining) :]


def test_cancelled_tls_loader_retains_slot_until_worker_exits(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(root):
        entered.set()
        assert release.wait(5)
        raise ValueError("synthetic loader exit")

    monkeypatch.setattr(control_tls, "_connection", blocked)

    async def run():
        async def handler(carrier):
            await control_tls.ControlTLS.accept(carrier, Path("unused"))

        owner, protocol, _, _ = setup(handler, max_connections=1)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            protocol.connection_lost(None)
            assert protocol.task is not None
            await protocol.task
            assert len(owner._leases) == 1
            with pytest.raises(OperationError):
                owner.reserve()
            release.set()

            async def settle():
                while owner._leases:
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(settle(), 2)
            replacement = owner.reserve()
            replacement.close()
        finally:
            release.set()
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_real_tls_disconnect_retains_capacity_and_health(
    tmp_path, private_windows_test_parent, monkeypatch
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = control_tls._connection

    def blocked(root):
        entered.set()
        assert release.wait(5)
        try:
            return original(root)
        finally:
            finished.set()

    monkeypatch.setattr(control_tls, "_connection", blocked)
    initialize_authority(
        tmp_path / "server" / "authority", recovery_file=tmp_path / "operator.json"
    )
    app = cast(
        Starlette, _client(tmp_path, native_transport=Limits(max_connections=1))._app
    )
    with live_server(
        app, ws=app.state.native_websocket_protocol, http=app.state.native_http_protocol
    ) as port:
        peer = Peer(port, CONTROL_PATH)
        try:
            assert isinstance(peer.next()[0], events.AcceptConnection)
            assert entered.wait(2)
            peer.close()
            with Peer(port, CONTROL_PATH) as other:
                rejection = other.next()[0]
                assert isinstance(rejection, events.RejectConnection)
                assert rejection.status_code == 503
            assert wire_request(port, "GET", "/_health_")[0] == 200
            assert len(app.state.pairing_responder._leases) == 1
        finally:
            release.set()
            peer.close()
        assert finished.wait(2)
        deadline = time.monotonic() + 2
        while app.state.pairing_responder._leases:
            assert time.monotonic() < deadline
            time.sleep(0.001)


def test_consumed_bio_input_is_released_before_another_carrier_read():
    released = threading.Event()

    class Input(bytes):
        def __del__(self):
            released.set()

    class Connection:
        calls = 0

        def operation(self):
            self.calls += 1
            if self.calls <= 2:
                raise control_tls.SSL.WantReadError()
            return b"complete"

        def bio_read(self, size):
            raise control_tls.SSL.WantReadError()

        def pending(self):
            return 0

        def bio_write(self, data):
            return len(data)

    async def run():
        async def handler(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(handler)
        reads = 0

        async def receive():
            nonlocal reads
            reads += 1
            if reads == 1:
                return Input(b"x" * 65536)
            assert released.is_set(), "consumed input retained across next read"
            return b"next input"

        carrier.receive = receive
        connection = Connection()
        channel = control_tls.ControlTLS(
            carrier, cast(control_tls.SSL.Connection, connection)
        )
        try:
            assert await channel._call(connection.operation) == b"complete"
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("size", [8192, 8193])
def test_decoded_control_respects_configured_buffer_budget(size):
    class Connection:
        pending_data = bytearray(size.to_bytes(4, "big") + b"x" * size)
        written = 0

        def recv(self, count):
            data = bytes(self.pending_data[:count])
            del self.pending_data[:count]
            return data

        def send(self, data):
            self.written += len(data)
            return len(data)

        def bio_read(self, count):
            raise control_tls.SSL.WantReadError()

        def pending(self):
            return 0

    async def run():
        async def handler(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(handler, max_buffered_bytes=32768)
        connection = Connection()
        stream = control_tls.ControlTLS(
            carrier, cast(control_tls.SSL.Connection, connection)
        )
        try:
            if size == 8192:
                assert len(await stream.receive()) == size
                await stream.send(b"x" * size)
            else:
                with pytest.raises(ValueError, match="limit"):
                    await stream.receive()
                assert len(connection.pending_data) == size
                with pytest.raises(ValueError, match="limit"):
                    await stream.send(b"x" * size)
                assert connection.written == 0
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("budget", [32768, 65536, 262144])
def test_real_tls_send_accepts_effective_plaintext_boundary(
    tmp_path, private_windows_test_parent, budget
):
    async def run():
        root = tmp_path / f"authority-{budget}"
        initialize_authority(root, recovery_file=tmp_path / f"operator-{budget}.json")
        owner, carrier, stream, client = await _real_tls_pair(root, budget)
        try:
            limit = min(control_tls.MAX_CONTROL, carrier.frame_limit)
            for size in (limit - 1, limit):
                payload = b"x" * size
                await stream.send(payload)
                assert (
                    _client_recv(client, size + 4) == size.to_bytes(4, "big") + payload
                )
            with pytest.raises(ValueError, match="limit"):
                await stream.send(b"x" * (limit + 1))
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_real_tls_rejects_partial_record_before_native_budget_overrun(
    tmp_path, private_windows_test_parent
):
    async def run():
        root = tmp_path / "authority"
        initialize_authority(root, recovery_file=tmp_path / "operator.json")
        owner, carrier, stream, client = await _real_tls_pair(root, 8192)
        try:
            assert client.send((2048).to_bytes(4, "big") + b"x" * (16384 - 4)) == 16384
            with pytest.raises(ValueError, match="Control TLS input|Pairing carrier"):
                await stream.receive()
            assert carrier.closed
            assert carrier._control_tls_fed <= carrier.limits.max_buffered_bytes // 2
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_real_tls_default_boundary_request_reply(tmp_path, private_windows_test_parent):
    async def run():
        root = tmp_path / "authority"
        initialize_authority(root, recovery_file=tmp_path / "operator.json")
        owner, carrier, stream, client = await _real_tls_pair(root, 262144)
        try:
            payload = b"r" * 65536
            _client_send(client, len(payload).to_bytes(4, "big") + payload)
            assert await stream.receive() == payload
            await stream.send(payload)
            assert _client_recv(client, len(payload) + 4) == (
                len(payload).to_bytes(4, "big") + payload
            )
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("coalesce_input", [False, True])
def test_real_tls_default_boundary_record_fragmentation(
    tmp_path, private_windows_test_parent, coalesce_input
):
    async def run():
        root = tmp_path / f"authority-{coalesce_input}"
        initialize_authority(
            root, recovery_file=tmp_path / f"operator-{coalesce_input}.json"
        )
        owner, carrier, stream, client = await _real_tls_pair(
            root, 262144, input_chunk_limit=16406, coalesce_input=coalesce_input
        )
        try:
            payload = b"r" * 65536
            _client_send(client, len(payload).to_bytes(4, "big") + payload)
            assert await asyncio.wait_for(stream.receive(), 3) == payload
            assert carrier._control_tls_fed <= carrier.limits.max_buffered_bytes // 2
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def _live_tls_bytes() -> int:
    objects: dict[int, int] = {}
    frame = inspect.currentframe()
    assert frame is not None
    frame = frame.f_back
    while frame is not None:
        name = frame.f_code.co_name
        filename = frame.f_code.co_filename.replace("\\", "/")
        if (filename.endswith("/control_tls.py") and name in {"send", "_call"}) or (
            filename.endswith("/pairing_carrier.py") and name in {"send", "_write"}
        ):
            for value in frame.f_locals.values():
                if isinstance(value, (bytes, bytearray)) and value:
                    objects.setdefault(id(value), len(value))
        frame = frame.f_back
    return sum(objects.values())


def _compiled_live_tls_bytes(filename: str, function_name: str) -> int:
    namespace: dict[str, object] = {"_live_tls_bytes": _live_tls_bytes}
    code = compile(
        f"def {function_name}():\n"
        "    payload = b'x' * 64\n"
        "    return _live_tls_bytes()\n",
        filename,
        "exec",
    )
    exec(code, namespace)
    function = cast(Callable[[], int], namespace[function_name])
    return function()


@pytest.mark.parametrize(
    ("filename", "function_name"),
    [
        (str(Path("arbiter_server") / "control_tls.py"), "send"),
        (str(PureWindowsPath("arbiter_server") / "control_tls.py"), "send"),
        (str(Path("arbiter_server") / "pairing_carrier.py"), "_write"),
        (str(PureWindowsPath("arbiter_server") / "pairing_carrier.py"), "_write"),
    ],
)
def test_live_tls_bytes_counts_matching_frame_payload(filename, function_name):
    assert _compiled_live_tls_bytes(filename, function_name) == 64


@pytest.mark.parametrize("budget", [8192, 32768])
def test_real_tls_output_ownership_stays_within_small_budget(
    tmp_path, private_windows_test_parent, budget
):
    async def run():
        root = tmp_path / f"authority-output-{budget}"
        initialize_authority(
            root, recovery_file=tmp_path / f"operator-output-{budget}.json"
        )
        owner, carrier, stream, _client = await _real_tls_pair(root, budget)
        assert isinstance(carrier.transport, Transport)
        transport: Transport = carrier.transport
        original_write = transport.write
        samples: list[int] = []
        writes = 0
        blocked = asyncio.Event()

        def write(data):
            nonlocal writes
            original_write(data)
            # Include the actual transport write-buffer copy in the live
            # ownership sample, not only the pre-write Python owners.
            samples.append(_live_tls_bytes() + len(transport.output))
            writes += 1
            if writes == 1:
                carrier.writable.clear()
                blocked.set()

        transport.write = write
        try:
            task = asyncio.create_task(stream.send(b"x" * carrier.frame_limit))
            await asyncio.wait_for(blocked.wait(), 2)
            assert not task.done()
            carrier.writable.set()
            await asyncio.wait_for(task, 2)
            assert writes >= 2
            assert max(samples) <= budget
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_real_tls_handshake_input_respects_small_budget(
    tmp_path, private_windows_test_parent
):
    async def run():
        root = tmp_path / "authority-handshake"
        initialize_authority(root, recovery_file=tmp_path / "operator-handshake.json")

        async def waiting(_carrier):
            await asyncio.Event().wait()

        owner, carrier, _transport, ws = setup(waiting, max_buffered_bytes=8192)
        original_receive = carrier.receive
        prefix = b"\x16\x03\x03\x40\x00\x01\x00\x3f\xfc" + b"x" * (12288 - 9)
        offset = 0

        async def receive():
            nonlocal offset
            part = prefix[offset : offset + 2048]
            offset += len(part)
            carrier.loop.call_soon(
                carrier.data_received, ws.send(events.BytesMessage(data=part))
            )
            return await original_receive()

        carrier.receive = receive
        try:
            with pytest.raises(ValueError, match="Control TLS input|Pairing carrier"):
                await asyncio.wait_for(control_tls.ControlTLS.accept(carrier, root), 3)
            assert carrier.closed
            assert not carrier.control_started_once
            assert offset <= carrier.limits.max_buffered_bytes
            assert carrier._control_tls_fed <= carrier.limits.max_buffered_bytes // 2
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_control_input_preflight_includes_pending_tls_owner():
    async def run():
        async def waiting(_carrier):
            await asyncio.Event().wait()

        owner, carrier, transport, ws = setup(waiting, max_buffered_bytes=8192)
        try:
            carrier._control_tls_fed = 2048
            carrier._control_tls_pending = 1024
            carrier.data_received(ws.send(events.BytesMessage(data=b"x" * 1710)))
            assert carrier.closed
            assert transport.closed
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("target", [CONTROL_PATH, PAIRING_PATH + ("A" * 22) + "/pair"])
def test_cancelled_receive_joins_race_children(target):
    async def run():
        async def waiting(_carrier):
            await asyncio.Event().wait()

        owner, carrier, _transport, _ws = setup(waiting, target=target)
        try:
            before = set(asyncio.all_tasks())
            reader = (
                asyncio.create_task(carrier.receive())
                if target == CONTROL_PATH
                else carrier.task
            )
            assert reader is not None
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
            await asyncio.sleep(0)
            assert not (asyncio.all_tasks() - before)
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())
