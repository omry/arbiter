"""Read one committed result using its registered access key, never a pairing PSK.

No table or activation is created here. Until the activation slice installs its
writer, only explicit test fixtures can supply committed enrollment rows.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from .authority import _read_record
from .credentials import Identity, Role, verify_certificate
from .generations import ACTIVE, _control, _database
from .pairing_proofs import _digest
from .prepared import PREPARED_TAG

REQUEST = b"arbiter-enrollment-receipt-request-v1\0"
PROOF = b"arbiter-enrollment-receipt-possession-v1\0"
MAX_RESULT = 65519


def result_fields(raw: bytes) -> tuple[list[str], list[bytes]]:
    """Parse the bounded stored result, retaining exact bytes for retrieval."""
    fixed = len(PREPARED_TAG) + 144 + 22 + 32 + 32 + 8
    if not fixed <= len(raw) <= MAX_RESULT or not raw.startswith(PREPARED_TAG):
        raise ValueError("Invalid enrollment result")
    data = raw[len(PREPARED_TAG) :]
    ids = [data[i : i + 36].decode("ascii") for i in range(0, 144, 36)]
    if len(set(ids)) != 4 or any(str(UUID(x, version=4)) != x for x in ids):
        raise ValueError("Invalid enrollment result")
    data, fields = raw[fixed:], []
    for _ in range(9):
        size = int.from_bytes(data[:2], "big")
        if size == 0 or len(data) < size + 2:
            raise ValueError("Invalid enrollment result")
        fields.append(data[2 : size + 2])
        data = data[size + 2 :]
    if data:
        raise ValueError("Invalid enrollment result")
    return ids, fields


def retrieve(root: Path, request: bytes, exporter: bytes, now: datetime) -> bytes:
    """Caller owns the instance and bounded worker; no event-loop disk/crypto.

    TLS authenticates the server before this fixed message is sent. Its fresh
    exporter prevents replay on another connection. The client nonce binds the
    proof to this invocation; it is not a reusable bearer credential.
    """
    if len(exporter) != 32 or len(request) != len(REQUEST) + 200:
        raise ValueError("Control unavailable")
    if not request.startswith(REQUEST):
        raise ValueError("Control unavailable")
    body = request[len(REQUEST) :]
    server, enrollment = body[:36].decode("ascii"), body[36:72].decode("ascii")
    if any(str(UUID(x, version=4)) != x for x in (server, enrollment)):
        raise ValueError("Control unavailable")
    active = _control(root / ACTIVE)
    if active is None:
        raise ValueError("Control unavailable")
    path = _database(root, active["active"])
    authority = _read_record(path)
    if server != authority.server_id:
        raise ValueError("Control unavailable")
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    ) as db:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT prepared, denied, inactive_after FROM enrollments "
            "WHERE enrollment_id=?",
            (enrollment,),
        ).fetchone()
        if row is None or row[1] != 0 or row[2] <= now.timestamp():
            raise ValueError("Control unavailable")
        raw = bytes(row[0])
        if hashlib.sha256(raw).digest() != body[72:104]:
            raise ValueError("Control unavailable")
        ids, fields = result_fields(raw)
        issuer = db.execute(
            "SELECT certificate_der FROM credentials WHERE role=?",
            (Role.SERVER_AUTHORITY.value,),
        ).fetchone()[0]
        if ids[0] != server or ids[3] != enrollment or fields[0] != issuer:
            raise ValueError("Control unavailable")
        cert = verify_certificate(
            fields[4],
            Identity(Role.ACCESS, ids[2], server, enrollment),
            issuer_der=issuer,
            issuer_id=server,
            now=now,
        )
        key = cert.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise ValueError("Control unavailable")
        signature = body[136:]
        key.verify(
            utils.encode_dss_signature(
                int.from_bytes(signature[:32], "big"),
                int.from_bytes(signature[32:], "big"),
            ),
            _digest(
                hashlib.sha256(PROOF + exporter + request[:-64]).digest(), b"receipt"
            ),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
        # Read-only snapshot is the linearization point against live denial.
        return raw


async def dispatch(channel: object, root: Path) -> None:
    from .control_tls import ControlTLS

    assert isinstance(channel, ControlTLS)
    request = await channel.receive()
    exporter = channel.exporter()
    carrier = channel.carrier
    assert carrier.lease is not None
    result = await carrier.responder._run(
        carrier.lease,
        lambda: retrieve(root, request, exporter, datetime.now(timezone.utc)),
    )
    await channel.send(result)
