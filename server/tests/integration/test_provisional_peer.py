"""A real Python peer verifies newly generated Go provisional keys after reload."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from arbiter_server.errors import OperationError
from starlette.applications import Starlette

# Reuse the native test setup: short Unix socket paths and real private Windows
# parent ACLs. The new loopback server runs the normal local-control lifespan.
from server.tests.unit.conftest import private_windows_test_parent, tmp_path
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client


def test_fresh_go_provisional_material_in_python() -> None:
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    private = x25519.X25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    result = subprocess.run(
        [
            "go",
            "test",
            "-count=1",
            "-run",
            "^TestPythonProvisionalPeer$",
            "-v",
            "./internal/identity",
        ],
        cwd=Path(__file__).resolve().parents[3] / "client" / "go-cli",
        env={
            **os.environ,
            "ARBITER_TEST_PROVISIONAL_PEER": base64.b64encode(public).decode("ascii"),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, "Go provisional material peer failed"
    prefix = "ARBITER_PUBLIC_PROVISIONAL_PEER="
    output = [
        line[len(prefix) :]
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(output) == 1, "fresh Go peer did not execute exactly once"
    proof = json.loads(output[0])
    advertised = proof["public"]
    assert set(advertised) == {
        "version",
        "selector",
        "name",
        "access_spki",
        "portable_spki",
        "recipient_spki",
    }
    assert advertised["version"] == 1
    assert len(base64.urlsafe_b64decode(advertised["selector"] + "==")) == 16
    assert advertised["name"] == "Agent Smith"
    message = b"arbiter-provisional-peer-v1"
    for role in ("access", "portable"):
        key = serialization.load_der_public_key(
            base64.b64decode(advertised[f"{role}_spki"])
        )
        assert isinstance(key, ec.EllipticCurvePublicKey)
        assert isinstance(key.curve, ec.SECP256R1)
        key.verify(
            base64.b64decode(proof[f"{role}_signature"]),
            message,
            ec.ECDSA(hashes.SHA256()),
        )
    assert advertised["access_spki"] != advertised["portable_spki"]
    recipient = serialization.load_der_public_key(
        base64.b64decode(advertised["recipient_spki"])
    )
    assert isinstance(recipient, x25519.X25519PublicKey)
    expected = hmac.digest(private.exchange(recipient), message, hashlib.sha256)
    assert hmac.compare_digest(expected, base64.b64decode(proof["recipient_proof"]))


@pytest.mark.usefixtures("private_windows_test_parent")
def test_fresh_go_public_request_reaches_python_server(tmp_path) -> None:
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    app = cast(Starlette, _client(tmp_path)._app)
    with live_server(app) as port:
        # Synthetic local administration opens only this test server. The
        # product local open/list/approve/reject entrypoint belongs to2.2.2.
        app.state.enrollment_requests.open_window()
        result = subprocess.run(
            [
                "go",
                "test",
                "-count=1",
                "-run",
                "^TestPythonRegistrationPeer$",
                "-v",
                "./internal/identity",
            ],
            cwd=Path(__file__).resolve().parents[3] / "client" / "go-cli",
            env={
                **os.environ,
                "ARBITER_TEST_REGISTRATION_URL": f"http://127.0.0.1:{port}/api/v1/enrollment/requests",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, "fresh Go registration peer failed"
        public = [
            line.removeprefix("ARBITER_PUBLIC_REGISTRATION=")
            for line in result.stdout.splitlines()
            if line.startswith("ARBITER_PUBLIC_REGISTRATION=")
        ]
        assert len(public) == 1
        advertised = json.loads(public[0])
        (pending,) = app.state.enrollment_requests.pending()
        assert pending.advertisement.selector == advertised["selector"]
        assert pending.advertisement.access_spki == base64.b64decode(
            advertised["access_spki"]
        )
        assert pending.advertisement.portable_spki == base64.b64decode(
            advertised["portable_spki"]
        )
        assert pending.advertisement.recipient_spki == base64.b64decode(
            advertised["recipient_spki"]
        )
        assert pending.advertisement.name == "Agent Smith"
    assert app.state.enrollment_requests.pending() == ()
    with pytest.raises(OperationError, match="Enrollment is closed"):
        app.state.enrollment_requests.require_open()
