"""Fresh Go reconciliation against Python's real bounded authenticated control."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from typing import cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import pytest
from starlette.applications import Starlette

from arbiter_server.authority import _read_record, initialize_authority
from arbiter_server import enrollment_receipt
from arbiter_server.credentials import (
    Identity,
    Role,
    create_issuer,
    issue_certificate,
)
from arbiter_server.generations import ACTIVE, _control, _database
from arbiter_server.main import _generate_self_signed_tls_certificate
from arbiter_server.prepared import PREPARED_TAG, prepare
from server.tests.unit.conftest import private_windows_test_parent, tmp_path
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client
from server.tests.unit.test_enrollment_receipt import synthetic_commit


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "lost-reply",
        "replay",
        "expired-prepare",
        "denied",
        "inactive",
        "changed-result",
        "historical-expiry",
        "unknown",
    ],
)
def test_fresh_python_go_receipt(
    tmp_path, private_windows_test_parent, mode, monkeypatch
):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    root = tmp_path / "server" / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    app = cast(Starlette, _client(tmp_path)._app)
    cert, key = tmp_path / "outer.crt", tmp_path / "outer.key"
    _generate_self_signed_tls_certificate(
        cert_path=cert, key_path=key, names=("127.0.0.1",)
    )
    original = enrollment_receipt.retrieve
    calls = []
    historical_authority = historical_server_key = historical_now = None
    if mode == "historical-expiry":
        active = _control(root / ACTIVE)
        assert active is not None
        path = _database(root, active["active"])
        record = _read_record(path)
        historical_now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
            days=366
        )
        historical_authority = create_issuer(
            Identity(Role.SERVER_AUTHORITY, record.server_id), now=historical_now
        )
        historical_server_key = ec.generate_private_key(ec.SECP256R1())
        current_server = issue_certificate(
            Identity(Role.SERVER_TLS, record.server_id),
            historical_server_key.public_key(),
            historical_authority,
        )
        authority_private = historical_authority.private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        server_private = historical_server_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "UPDATE credentials SET certificate_der=?, private_key_der=? WHERE role=?",
                (
                    historical_authority.certificate_der,
                    authority_private,
                    Role.SERVER_AUTHORITY.value,
                ),
            )
            db.execute(
                "UPDATE credentials SET certificate_der=?, private_key_der=? WHERE role=?",
                (current_server, server_private, Role.SERVER_TLS.value),
            )
            db.commit()

    def retrieve(*args):
        result = original(*args)
        calls.append(True)
        if mode == "lost-reply" and len(calls) == 1:
            raise ValueError("synthetic lost receipt after successful retrieval")
        return result

    monkeypatch.setattr(enrollment_receipt, "retrieve", retrieve)
    with live_server(
        app,
        ws=app.state.native_websocket_protocol,
        http=app.state.native_http_protocol,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
    ) as port:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(60)

            def fixture():
                connection, _ = listener.accept()
                with connection, connection.makefile("rwb") as stream:
                    connection.settimeout(15)
                    raw = stream.readline(16385).rstrip(b"\n")
                    assert len(raw) <= 16384
                    requests = app.state.enrollment_requests
                    requests.open_window()
                    pending = requests.register(raw)
                    requests.control(
                        "approve",
                        dict(
                            selector=pending.advertisement.selector,
                            revision=pending.revision,
                            request_digest=pending.advertisement.digest,
                            psk=base64.urlsafe_b64encode(os.urandom(32))
                            .rstrip(b"=")
                            .decode(),
                            grants=[],
                        ),
                        set(),
                    )
                    approval = requests.approved(pending.advertisement.selector)
                    assert approval is not None
                    prepared = prepare(
                        root,
                        approval,
                        int(datetime.now(timezone.utc).timestamp()) + 300,
                    )
                    if mode == "historical-expiry":
                        assert historical_server_key is not None
                        assert historical_authority is not None
                        assert historical_now is not None
                        ids, fields = enrollment_receipt.result_fields(prepared)
                        old_leaf = issue_certificate(
                            Identity(Role.SERVER_TLS, ids[0]),
                            historical_server_key.public_key(),
                            historical_authority,
                            now=historical_now,
                        )
                        fixed = len(PREPARED_TAG) + 144 + 22 + 32 + 32 + 8
                        fields[1] = old_leaf
                        prepared = prepared[:fixed] + b"".join(
                            len(value).to_bytes(2, "big") + value for value in fields
                        )
                    if mode == "expired-prepare":
                        offset = len(PREPARED_TAG) + 144 + 22 + 32 + 32
                        prepared = (
                            prepared[:offset]
                            + int(datetime.now(timezone.utc).timestamp() - 60).to_bytes(
                                8, "big"
                            )
                            + prepared[offset + 8 :]
                        )
                    path = synthetic_commit(root, prepared)
                    # All pairing PSKs and pending approval state are gone before
                    # reconciliation; the disk result is the only source.
                    requests.reset()
                    with closing(sqlite3.connect(path)) as db:
                        if mode == "denied":
                            db.execute("UPDATE enrollments SET denied=1")
                        if mode == "inactive":
                            db.execute("UPDATE enrollments SET inactive_after=0")
                        if mode == "unknown":
                            db.execute("DELETE FROM enrollments")
                        if mode == "changed-result":
                            db.execute(
                                "UPDATE enrollments SET prepared=?",
                                (prepared[:-1] + b"x",),
                            )
                        db.commit()
                    stream.write(
                        json.dumps(
                            dict(
                                URL=f"https://127.0.0.1:{port}",
                                Mode=mode,
                                Result=base64.b64encode(prepared).decode(),
                            )
                        ).encode()
                        + b"\n"
                    )
                    stream.flush()

            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(fixture)
                result = subprocess.run(
                    [
                        "go",
                        "test",
                        "-count=1",
                        "-run",
                        "^TestPythonReceiptPeer$",
                        "./internal/identity",
                    ],
                    cwd=Path(__file__).parents[3] / "client" / "go-cli",
                    env={
                        **os.environ,
                        "ARBITER_TEST_RECEIPT_CONTROL": f"127.0.0.1:{listener.getsockname()[1]}",
                    },
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                task.result(timeout=5)
                assert result.returncode == 0, result.stdout + result.stderr
        assert len(calls) == (
            {
                "ok": 1,
                "lost-reply": 2,
                "replay": 2,
                "expired-prepare": 1,
                "historical-expiry": 1,
            }.get(mode, 0)
        )
