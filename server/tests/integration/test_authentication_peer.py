"""Private real stream peer; ordinary authentication/routing is not enabled."""

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest
from starlette.applications import Starlette

from arbiter_server import control_tls
from arbiter_server.authority import initialize_authority
from arbiter_server.main import _generate_self_signed_tls_certificate
from arbiter_server.native_stream import CHUNK, TLSStream
from arbiter_server.pairing_carrier import protocol_factory
from arbiter_server.pairing_identity import load_pairing_identity
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client
from server.tests.unit.conftest import private_windows_test_parent, tmp_path


@pytest.mark.parametrize("fragment", [False, True])
@pytest.mark.parametrize("outer_tls", [False, True])
def test_private_python_go_stream(
    tmp_path, private_windows_test_parent, fragment, outer_tls
):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    root = tmp_path / "server" / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    material = load_pairing_identity(root)
    assert material is not None
    leaf, authority = material
    app = cast(Starlette, _client(tmp_path)._app)
    completed = []
    server_batches = []

    async def handler(carrier):
        # Only this test factory replaces the fixed-control consumer. The
        # production route and credential policy remain unchanged.
        connection = await carrier.responder._run(
            carrier.lease, lambda: control_tls._connection(root)
        )
        stream = TLSStream(carrier, connection)
        await stream._call(connection.do_handshake)
        carrier.begin_stream()
        original_send = carrier.send
        pending = []

        async def send(data):
            if not fragment:
                # A bounded test relay holds multiple application records, including
                # every separately drained BIO chunk, for one outbound message.
                assert sum(map(len, pending)) + len(data) <= carrier.frame_limit
                pending.append(data)
                return
            for offset in range(0, len(data), 1375):
                await original_send(data[offset : offset + 1375])

        carrier.send = send
        total = 0
        batch_writes = 0
        # Exceed the fixed control's lifetime budget in both directions.
        while total < CHUNK * 32:
            data = await stream.read()
            assert data
            total += len(data)
            await stream.write(data)
            batch_writes += 1
            if not fragment and total % (CHUNK * 2) == 0:
                assert batch_writes >= 2
                assert len(pending) >= 2
                await original_send(b"".join(pending))
                pending.clear()
                server_batches.append(batch_writes)
                batch_writes = 0
            carrier.credits.check()
            await asyncio.sleep(0)  # Allow queued callbacks/backpressure.
        assert not pending
        completed.append(total)
        await stream.close()

    app.state.native_websocket_protocol = protocol_factory(
        app.state.pairing_responder,
        app.state.pairing_responder._limits,
        continue_control=handler,
    )
    kwargs = {}
    if outer_tls:
        cert, key = tmp_path / "outer.pem", tmp_path / "outer.key"
        _generate_self_signed_tls_certificate(
            cert_path=cert, key_path=key, names=("127.0.0.1",)
        )
        kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    with live_server(
        app,
        ws=app.state.native_websocket_protocol,
        http=app.state.native_http_protocol,
        **kwargs,
    ) as port:
        fixture = tmp_path / "public-stream-peer.json"
        fixture.write_text(
            json.dumps(
                {
                    "URL": f"{'wss' if outer_tls else 'ws'}://127.0.0.1:{port}/api/v1/native/control",
                    "Fragment": fragment,
                    "ServerID": leaf.identity.subject_id,
                    "Authority": base64.b64encode(authority).decode("ascii"),
                }
            )
        )
        result = subprocess.run(
            [
                "go",
                "test",
                "-count=1",
                "-v",
                "-run",
                "^TestPythonAuthenticationStreamPeer$",
                "./internal/identity",
            ],
            cwd=Path(__file__).parents[3] / "client/go-cli",
            env={**os.environ, "ARBITER_TEST_AUTH_STREAM": str(fixture)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines().count("ARBITER_STREAM_PEER=pass") == 1
    assert completed == [CHUNK * 32]
    if not fragment:
        assert len(server_batches) == 16 and min(server_batches) >= 2
    assert not app.state.pairing_responder._leases
