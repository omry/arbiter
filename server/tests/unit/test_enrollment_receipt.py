"""Explicit synthetic activation; the product still has no activation writer."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from arbiter_server import enrollment_receipt as r
from arbiter_server.authority import initialize_authority
from arbiter_server.generations import ACTIVE, _control, _database
from arbiter_server.pairing_proofs import _digest, _signature
from arbiter_server.prepared import prepare
from server.tests.unit.test_pairing_proofs import proof_setup


def synthetic_commit(root: Path, raw: bytes) -> Path:
    """Test-only row; neither registration nor control can call this writer."""
    active = _control(root / ACTIVE)
    assert active is not None
    path = _database(root, active["active"])
    ids, _ = r.result_fields(raw)
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE enrollments (enrollment_id TEXT PRIMARY KEY, "
            "prepared BLOB NOT NULL CHECK(length(prepared)<=65519), "
            "denied INTEGER NOT NULL CHECK(denied IN (0,1)), "
            "inactive_after INTEGER NOT NULL)"
        )
        db.execute(
            "INSERT INTO enrollments VALUES (?, ?, 0, ?)",
            (ids[3], raw, int(datetime.now(timezone.utc).timestamp()) + 86400),
        )
        db.commit()
    return path


def request_for(raw, exporter, key):
    ids, _ = r.result_fields(raw)
    request = (
        r.REQUEST
        + (ids[0] + ids[3]).encode()
        + hashlib.sha256(raw).digest()
        + os.urandom(32)
    )
    return request + _signature(
        key, _digest(hashlib.sha256(r.PROOF + exporter + request).digest(), b"receipt")
    )


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "wrong-key",
        "wrong-channel",
        "wrong-purpose",
        "signature",
        "truncated",
        "trailing",
        "unknown",
        "wrong-server",
        "result",
        "denied",
        "inactive",
        "missing-table",
        "stored-issuer",
    ],
)
def test_exact_result_and_denial_precedence(
    tmp_path, private_windows_test_parent, invalid
):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    _, approval, keys, _, _, now = proof_setup()
    raw = prepare(root, approval, int(now.timestamp()) + 300)
    path = synthetic_commit(root, raw)
    exporter = os.urandom(32)
    key = ec.generate_private_key(ec.SECP256R1()) if invalid == "wrong-key" else keys[0]
    request = request_for(raw, exporter, key)
    if invalid == "wrong-channel":
        exporter = os.urandom(32)
    if invalid == "wrong-purpose":
        request = b"X" + request[1:]
    if invalid == "signature":
        request = request[:-1] + bytes([request[-1] ^ 1])
    if invalid == "truncated":
        request = request[:-1]
    if invalid == "trailing":
        request += b"X"
    if invalid in {"unknown", "wrong-server", "result"}:
        offset = (
            len(r.REQUEST) + {"unknown": 36, "wrong-server": 0, "result": 72}[invalid]
        )
        request = (
            request[:offset] + bytes([request[offset] ^ 1]) + request[offset + 1 :]
        )
    with closing(sqlite3.connect(path)) as db:
        if invalid == "denied":
            db.execute("UPDATE enrollments SET denied=1")
        if invalid == "inactive":
            db.execute(
                "UPDATE enrollments SET inactive_after=?", (int(now.timestamp()),)
            )
        if invalid == "missing-table":
            db.execute("DROP TABLE enrollments")
        if invalid == "stored-issuer":
            fields = r.result_fields(raw)[1]
            changed = raw.replace(fields[0], fields[2], 1)
            db.execute("UPDATE enrollments SET prepared=?", (changed,))
        db.commit()
    before = path.read_bytes()
    if invalid:
        failures: tuple[type[Exception], ...] = (
            ValueError,
            InvalidSignature,
            sqlite3.OperationalError,
        )
        with pytest.raises(failures):
            r.retrieve(root, request, exporter, now)
    else:
        assert r.retrieve(root, request, exporter, now) == raw
        # A lost reply can be repeated on a fresh connection with a fresh proof.
        next_exporter = os.urandom(32)
        assert (
            r.retrieve(
                root, request_for(raw, next_exporter, keys[0]), next_exporter, now
            )
            == raw
        )
        # Replayed signatures fail on a new TLS connection.
        with pytest.raises(InvalidSignature):
            r.retrieve(root, request, next_exporter, now)
        # Real new interpreter has no PSK/request/approval state to inherit.
        probe = "from arbiter_server.enrollment_receipt import retrieve; from pathlib import Path; from datetime import datetime,timezone; import sys; print(__import__('hashlib').sha256(retrieve(Path(sys.argv[1]),bytes.fromhex(sys.argv[2]),bytes.fromhex(sys.argv[3]),datetime.now(timezone.utc))).hexdigest())"
        result = subprocess.run(
            [sys.executable, "-c", probe, str(root), request.hex(), exporter.hex()],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert result.stdout.strip() == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == before


@pytest.mark.parametrize("data", [b"", b"X" * 65520], ids=["empty", "oversized"])
def test_invalid_result_bounds(data):
    with pytest.raises(ValueError):
        r.result_fields(data)
