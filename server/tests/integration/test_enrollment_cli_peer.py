"""The real Go CLI uses Python HTTPS registration and the production carrier.

The separate loopback control socket is the trusted synthetic human handoff.
Secrets stay in memory and are never printed, passed in argv/env or persisted.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import cast

import pytest
from starlette.applications import Starlette

from arbiter_server.authority import initialize_authority
from arbiter_server.limits import NativeTransportLimitsConfig
from arbiter_server.main import _generate_self_signed_tls_certificate
from arbiter_server.prepared import PREPARED_TAG
from server.tests.unit.conftest import private_windows_test_parent, tmp_path
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "closed",
        "restart",
        "wrong-secret",
        "server-id",
        "agent-id",
        "issuer",
        "leaf",
        "recipient",
        "recovery",
        "expiry",
        "grants",
        "trailing",
        "truncated",
        "save-failure",
        "lost-reply",
        "delayed-reply",
        "reject-ready",
        "cancel-ready",
        "promotion-failure",
    ],
)
def test_fresh_python_go_enrollment_cli(
    tmp_path, private_windows_test_parent, mode, monkeypatch
):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    deadline_modes = {"lost-reply", "delayed-reply"}
    server_root = tmp_path / "server" / "authority"
    initialize_authority(server_root, recovery_file=tmp_path / "operator.json")
    native_transport = (
        NativeTransportLimitsConfig(handshake_timeout_seconds=10.0)
        if mode in deadline_modes
        else None
    )
    app = cast(Starlette, _client(tmp_path, native_transport=native_transport)._app)
    responder = app.state.pairing_responder
    original_prepare, original_ready = responder.prepare, responder.ready
    original_activate = responder.activate
    ready_calls = []
    saved_result = []
    request_clock = [0.0]
    request_deadline = None
    prepared_expiry = None
    commit_before_deadline = False
    reply_after_deadline = False

    async def prepare(session, root):
        nonlocal prepared_expiry
        raw = await original_prepare(session, root)
        saved_result.append(raw)
        fixed = len(PREPARED_TAG) + 144 + 22 + 32 + 32 + 8
        header, data = bytearray(raw[:fixed]), raw[fixed:]
        fields: list[bytes] = []
        while data:
            size = int.from_bytes(data[:2], "big")
            fields.append(data[2 : 2 + size])
            data = data[2 + size :]
        if mode in {"server-id", "agent-id"}:
            position = len(PREPARED_TAG) + (72 if mode == "agent-id" else 0)
            header[position] = ord("1") if header[position] != ord("1") else ord("2")
        if mode == "issuer":
            fields[2] = fields[0]
        if mode == "leaf":
            fields[4] = fields[3]
        if mode == "recipient":
            fields[5] = bytes(44)
        if mode == "recovery":
            fields[6] = bytes(91)
        if mode == "expiry":
            header[-8:] = int(datetime.now(timezone.utc).timestamp() - 1).to_bytes(
                8, "big"
            )
        if mode == "grants":
            fields[8] = b'[["smtp","bot"],["smtp","bot"]]'
        raw = bytes(header) + b"".join(
            len(value).to_bytes(2, "big") + value for value in fields
        )
        if mode in deadline_modes:
            assert raw == saved_result[-1]
            assert request_deadline is not None
            remaining = request_deadline - request_clock[0]
            assert 5 <= remaining <= 7
            prepared_expiry = int.from_bytes(raw[fixed - 8 : fixed], "big")
            wall_now = datetime.now(timezone.utc).timestamp()
            assert (
                int(wall_now + remaining) - 1
                <= prepared_expiry
                <= int(wall_now + remaining) + 1
            )
            assert prepared_expiry > int(wall_now) + 4
        if mode == "trailing":
            raw += b"X"
        if mode == "truncated":
            raw = raw[:-1]
        return raw

    async def ready(session, data):
        # Inspect the real private publication before the server sees ready.
        path = (
            tmp_path
            / "client"
            / "prepared"
            / (session.approval.request.advertisement.selector + ".json")
        )
        bundle = path.read_bytes()
        stored = json.loads(bundle)
        assert stored["warning"].startswith("NEVER SHARE THIS:")
        private = stored["provisional"]
        assert base64.b64decode(stored["result"], validate=True) == saved_result[-1]
        assert (
            base64.urlsafe_b64encode(session.approval.secret).rstrip(b"=") not in bundle
        )
        assert set(private) >= {
            "access_key_pkcs8",
            "portable_key_pkcs8",
            "recipient_key_pkcs8",
        }
        assert "psk" not in private and "secret" not in private
        ready_calls.append(hashlib.sha256(data).hexdigest())
        await original_ready(session, data)
        if mode == "reject-ready":
            app.state.enrollment_requests.exhaust_approval(session.approval)
        if mode == "cancel-ready":
            session.lease.close()

    async def activate(*args):
        nonlocal commit_before_deadline, reply_after_deadline
        receipt = await original_activate(*args)
        if mode in deadline_modes:
            assert request_deadline is not None and prepared_expiry is not None
            commit_before_deadline = request_clock[0] < request_deadline
            assert commit_before_deadline
            request_clock[0] = request_deadline + 1
            assert request_clock[0] > request_deadline
            await asyncio.sleep(8)
            assert datetime.now(timezone.utc).timestamp() > prepared_expiry
            reply_after_deadline = True
            if mode == "lost-reply":
                raise ValueError(
                    "synthetic dropped reply after durable commit and expiry"
                )
        return receipt

    monkeypatch.setattr(responder, "prepare", prepare)
    monkeypatch.setattr(responder, "ready", ready)
    monkeypatch.setattr(responder, "activate", activate)
    cert, key = tmp_path / "outer.crt", tmp_path / "outer.key"
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
        requests = app.state.enrollment_requests
        if mode in deadline_modes:
            requests._clock = lambda: request_clock[0]
        if mode != "closed":
            requests.open_window()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(30)

            def operator():
                nonlocal request_deadline
                connection, _ = listener.accept()
                with connection, connection.makefile("rwb") as stream:
                    connection.settimeout(20)
                    start = {
                        "URL": f"https://127.0.0.1:{port}",
                        "Directory": str(tmp_path / "client"),
                        "Mode": mode,
                    }
                    stream.write(json.dumps(start).encode() + b"\n")
                    stream.flush()
                    if mode == "closed":
                        return
                    handoff = json.loads(stream.readline(1025))
                    pending = next(
                        r
                        for r in requests.pending()
                        if r.advertisement.selector == handoff["selector"]
                    )
                    secret = handoff["secret"]
                    assert len(secret) == 43
                    if mode == "wrong-secret":
                        import base64

                        secret = (
                            base64.urlsafe_b64encode(os.urandom(32))
                            .decode()
                            .rstrip("=")
                        )
                    requests.control(
                        "approve",
                        dict(
                            selector=pending.advertisement.selector,
                            revision=pending.revision,
                            request_digest=pending.advertisement.digest,
                            psk=secret,
                            grants=[],
                        ),
                        set(),
                    )
                    if mode in deadline_modes:
                        request_deadline = pending.deadline
                        assert pending.deadline - pending.created_at == pytest.approx(
                            600
                        )
                        request_clock[0] = request_deadline - 6
                        assert request_clock[0] < request_deadline
                    if mode == "restart":
                        requests.reset()
                    if mode == "save-failure":
                        # A failed no-replace save must not emit ready or erase
                        # an existing private record.
                        path = (
                            tmp_path
                            / "client"
                            / "prepared"
                            / (pending.advertisement.selector + ".json")
                        )
                        path.write_text("preserve existing private record")
                        path.chmod(0o600)
                    if mode == "promotion-failure":
                        (tmp_path / "client" / "enrolled").write_text(
                            "preserve existing file"
                        )
                    stream.write(b"true\n")
                    stream.flush()

            with ThreadPoolExecutor(max_workers=1) as pool:
                owner = pool.submit(operator)
                result = subprocess.run(
                    [
                        "go",
                        "test",
                        "-count=1",
                        "-run",
                        "^TestPythonEnrollmentCLI$",
                        "./internal/cli",
                    ],
                    cwd=Path(__file__).parents[3] / "client" / "go-cli",
                    env={
                        **os.environ,
                        "ARBITER_TEST_ENROLLMENT_CONTROL": f"127.0.0.1:{listener.getsockname()[1]}",
                    },
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                owner.result(timeout=5)
        success = mode in {"ok", "lost-reply", "delayed-reply"}
        if mode in deadline_modes:
            assert commit_before_deadline
            assert reply_after_deadline
            assert prepared_expiry is not None
            assert datetime.now(timezone.utc).timestamp() > prepared_expiry
        assert (tmp_path / "client" / "enrolled").is_dir() == success
        assert len(ready_calls) == (
            1
            if success or mode in {"reject-ready", "cancel-ready", "promotion-failure"}
            else 0
        )
        if mode in {"reject-ready", "cancel-ready", "promotion-failure"}:
            assert len(list((tmp_path / "client" / "prepared").glob("*.json"))) == 1
        elif mode != "save-failure":
            assert not list((tmp_path / "client" / "prepared").glob("*.json"))
        if success:
            enrolled_files = list((tmp_path / "client" / "enrolled").glob("*.json"))
            assert len(enrolled_files) == 1
            enrolled = json.loads(enrolled_files[0].read_text())
            assert (
                base64.b64decode(enrolled["result"], validate=True) == saved_result[-1]
            )
            assert set(enrolled["provisional"]) >= {
                "access_key_pkcs8",
                "portable_key_pkcs8",
                "recipient_key_pkcs8",
            }
            assert not list((tmp_path / "client" / "prepared").glob("*.json"))
            sessions = app.state.pairing_responder._attempts.values()
            assert all(state.active is None for state in sessions)
            from contextlib import closing
            import sqlite3
            from server.tests.unit.test_enrollment_activation import database

            enrollment_id = saved_result[-1][
                len(PREPARED_TAG) + 108 : len(PREPARED_TAG) + 144
            ].decode()
            with closing(sqlite3.connect(database(server_root))) as db:
                row = db.execute(
                    "SELECT COUNT(*), prepared FROM enrollments WHERE enrollment_id=?",
                    (enrollment_id,),
                ).fetchone()
                assert row[0] == 1 and bytes(row[1]) == saved_result[-1]
                assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone() == (1,)
            assert not requests.pending() and not responder._attempts
