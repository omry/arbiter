"""A real Python peer verifies newly generated Go provisional keys after reload."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import cast

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from arbiter_server.errors import OperationError
from arbiter_server.authority import initialize_authority
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
    root = tmp_path / "server" / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    with live_server(app) as port:
        opened = subprocess.run(
            [
                sys.executable,
                "-m",
                "arbiter_server",
                "enrollment",
                "open",
                "--state-dir",
                str(root),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert opened.returncode == 0, opened.stderr
        assert json.loads(opened.stdout)["open"] is True
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


def test_go_portable_codec_is_strict_and_public_registration_stays_v1() -> None:
    from arbiter_server.enrollment import decode_advertisement
    from arbiter_server.credentials import Identity, Role, verify_certificate

    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    result = subprocess.run(
        [
            "go",
            "test",
            "-count=1",
            "-run",
            "^TestPythonPortableCodecPeer$",
            "-v",
            "./internal/identity",
        ],
        cwd=Path(__file__).resolve().parents[3] / "client" / "go-cli",
        env={**os.environ, "ARBITER_TEST_PORTABLE_CODEC": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [
        json.loads(line.removeprefix("ARBITER_PORTABLE_CODEC="))
        for line in result.stdout.splitlines()
        if line.startswith("ARBITER_PORTABLE_CODEC=")
    ]
    assert len(rows) == 2
    for row in rows:
        public = row["public"]
        raw = json.dumps(public, separators=(",", ":")).encode()
        decoded = decode_advertisement(raw, version=2)
        assert (
            public["version"] == 2 and decoded.digest == hashlib.sha256(raw).hexdigest()
        )
        with pytest.raises(OperationError):
            decode_advertisement(raw)
        claim = decoded.portable_identity
        if public["portable_identity"] is None:
            assert claim is None
        else:
            assert claim is not None
            root = base64.b64decode(row["owner_der"])
            assert hashlib.sha256(root).hexdigest() == claim.issuer_sha256
            from datetime import datetime, timezone

            verify_certificate(
                claim.certificate_der,
                Identity(Role.PORTABLE, claim.agent_id),
                issuer_der=root,
                issuer_id=claim.owner_id,
                now=datetime.now(timezone.utc),
            )
            # Decoding claims alone grants neither registration nor issuer trust.
            for field, value in [
                ("owner_id", "invalid"),
                ("agent_id", claim.owner_id),
                ("issuer_sha256", "F" * 64),
                ("certificate_der", "eA=="),
                ("extra", "x"),
            ]:
                changed = json.loads(raw)
                changed["portable_identity"][field] = value
                with pytest.raises(OperationError):
                    decode_advertisement(json.dumps(changed).encode(), version=2)
            duplicate = raw.replace(
                b'"owner_id":', b'"owner_id":"duplicate","owner_id":'
            )
            with pytest.raises(OperationError):
                decode_advertisement(duplicate, version=2)
        for changed in [
            raw + b"x",
            raw.replace(b'"version":2', b'"version":true'),
            raw.replace(b'"portable_identity":', b'"Portable_Identity":'),
        ]:
            with pytest.raises(OperationError):
                decode_advertisement(changed, version=2)
