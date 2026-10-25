"""Fresh public trust and real Go TLS over the production bounded WSS carrier."""

import base64
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast
from uuid import uuid4

import pytest
from cryptography import x509
from OpenSSL import SSL
from starlette.applications import Starlette
from wsproto import events

from arbiter_server import control_tls
from arbiter_server.authority import initialize_authority
from arbiter_server.limits import NativeTransportLimitsConfig
from arbiter_server.main import _generate_self_signed_tls_certificate
from arbiter_server.pairing_carrier import protocol_factory
from arbiter_server.pairing_identity import load_pairing_identity
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client
from server.tests.unit.conftest import private_windows_test_parent, tmp_path


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "maximum",
        "server-id",
        "issuer",
        "alpn",
        "version",
        "oversized-reply",
        "truncated",
        "cancel",
        "separate-deadline",
        "handshake-timeout",
        "control-timeout",
    ],
)
def test_fresh_python_go_control_tls(
    tmp_path, private_windows_test_parent, monkeypatch, mode
):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    root = tmp_path / "server" / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    material = load_pairing_identity(root)
    assert material is not None
    leaf, authority = material
    server_id = leaf.identity.subject_id
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*.sqlite3")
    }
    limits = NativeTransportLimitsConfig(
        handshake_timeout_seconds=2 if mode == "separate-deadline" else 10
    )
    app = cast(Starlette, _client(tmp_path, native_transport=limits)._app)
    seen = []

    async def handler(carrier):
        if mode in {"separate-deadline", "handshake-timeout"}:
            await asyncio.sleep(1.2)
        pongs = []
        expected_pongs = [b"setup-ping", b"control-ping"]
        observed_pongs = asyncio.Event()
        event_handler = carrier._event

        def capture(event):
            if isinstance(event, events.Pong):
                pongs.append(bytes(event.payload))
                if pongs == expected_pongs:
                    observed_pongs.set()
            event_handler(event)

        carrier._event = capture
        carrier._write(events.Ping(payload=b"setup-ping"))
        channel = await control_tls.ControlTLS.accept(carrier, root)
        carrier._write(events.Ping(payload=b"control-ping"))
        data = await channel.receive()
        watch = asyncio.create_task(carrier.receive())
        try:
            await asyncio.wait_for(
                observed_pongs.wait(), min(carrier.lease.remaining_seconds, 1.0)
            )
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        assert pongs == expected_pongs
        assert data[:32] == channel.exporter()
        seen.append(data)
        if mode == "separate-deadline":
            await asyncio.sleep(1.2)
        if mode == "control-timeout":
            await asyncio.sleep(2)
        if mode == "oversized-reply":
            await channel._call(
                lambda: channel.connection.send((65537).to_bytes(4, "big"))
            )
        elif mode == "truncated":
            await channel._call(lambda: channel.connection.send(b"\0\0\0\x10short"))
        else:
            await channel.send(data)

    if mode == "alpn":
        monkeypatch.setattr(control_tls, "ALPN", b"unrelated-protocol")
    if mode == "version":

        def tls12(path):
            # Synthetic negative control only; product permits TLS1.3 only.
            other = SSL.Context(SSL.TLS_SERVER_METHOD)
            other.set_min_proto_version(SSL.TLS1_2_VERSION)
            other.set_max_proto_version(SSL.TLS1_2_VERSION)
            other.use_certificate(x509.load_der_x509_certificate(leaf.certificate_der))
            other.use_privatekey(leaf.private_key)
            result = SSL.Connection(other, None)
            result.set_accept_state()
            return result

        monkeypatch.setattr(control_tls, "_connection", tls12)
    app.state.native_websocket_protocol = protocol_factory(
        app.state.pairing_responder,
        limits,
        continue_control=handler,
    )
    cert, key = tmp_path / "outer.pem", tmp_path / "outer.key"
    _generate_self_signed_tls_certificate(
        cert_path=cert, key_path=key, names=("127.0.0.1",)
    )
    with live_server(
        app,
        ws=app.state.native_websocket_protocol,
        http=app.state.native_http_protocol,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
    ) as port:

        def encoded(data):
            return base64.b64encode(data).decode("ascii")

        fixture = tmp_path / "public.json"
        fixture.write_text(
            json.dumps(
                {
                    "URL": f"https://127.0.0.1:{port}",
                    "Mode": mode,
                    "Trust": {
                        "ServerID": str(uuid4()) if mode == "server-id" else server_id,
                        "AuthorityDER": encoded(
                            leaf.certificate_der if mode == "issuer" else authority
                        ),
                    },
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
                "^TestPythonControlTLSPeer$",
                "./internal/identity",
            ],
            cwd=Path(__file__).parents[3] / "client" / "go-cli",
            env={**os.environ, "ARBITER_TEST_CONTROL_TLS": str(fixture)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert len(seen) == (
        1
        if mode
        in {
            "ok",
            "maximum",
            "oversized-reply",
            "truncated",
            "separate-deadline",
            "control-timeout",
        }
        else 0
    )
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*.sqlite3")
    }
    assert not app.state.pairing_responder._leases
