"""Uncommitted enrollment results. Issuing a certificate never activates access."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .authority import _read_record
from .credentials import Identity, Role, SigningCredential, issue_certificate
from .enrollment import ApprovedRequest
from .generations import ACTIVE, _control, _database
from .pairing_proofs import _digest

PREPARED_TAG = b"arbiter-enrollment-prepared-v1\0"
READY_TAG = b"arbiter-enrollment-ready-v1\0"
PREPARED_COMPLETE = b"arbiter-enrollment-prepared-not-active-v1\0"


def prepare(root: Path, approval: ApprovedRequest, expires_at: int) -> bytes:
    """Caller owns running_instance and a bounded worker, never publication recovery."""
    active = _control(root / ACTIVE)
    if active is None or expires_at <= datetime.now(timezone.utc).timestamp():
        raise ValueError("No live enrollment authority")
    path = _database(root, active["active"])
    record = _read_record(path)
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    ) as db:
        rows = dict(
            (role, (cert, key))
            for role, cert, key in db.execute(
                "SELECT role, certificate_der, private_key_der FROM credentials"
            )
        )
        recovery, origin = db.execute(
            "SELECT verifier, origin_verifier FROM recovery_trust WHERE singleton=1"
        ).fetchone()
    now = datetime.now(timezone.utc)
    agent, enrollment = str(uuid4()), str(uuid4())
    certificates = []
    ad = approval.request.advertisement
    for role, identity, spki in (
        (Role.OWNER, Identity(Role.PORTABLE, agent), ad.portable_spki),
        (
            Role.SERVER_AUTHORITY,
            Identity(Role.ACCESS, agent, record.server_id, enrollment),
            ad.access_spki,
        ),
    ):
        certificate, private = rows[role.value]
        key = serialization.load_der_private_key(private, password=None)
        public = serialization.load_der_public_key(spki)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            public, ec.EllipticCurvePublicKey
        ):
            raise ValueError("Invalid enrollment signing material")
        issuer = SigningCredential(
            Identity(role, record.owner_id if role is Role.OWNER else record.server_id),
            certificate,
            key,
        )
        certificates.append(issue_certificate(identity, public, issuer, now=now))
    # Fixed ASCII IDs/request binding/deadline, then nine uint16-length fields.
    # Certificates/public keys use their existing DER profiles; grants are only
    # JSON arrays of strings, never an extensible object or executable content.
    result = (
        PREPARED_TAG
        + (record.server_id + record.owner_id + agent + enrollment).encode("ascii")
        + ad.selector.encode("ascii")
        + bytes.fromhex(ad.digest)
        + approval.request.revision.encode("ascii")
        + expires_at.to_bytes(8, "big")
    )
    for value in (
        rows[Role.SERVER_AUTHORITY.value][0],
        rows[Role.SERVER_TLS.value][0],
        rows[Role.OWNER.value][0],
        *certificates,
        ad.recipient_spki,
        recovery,
        origin,
        json.dumps(approval.grants, ensure_ascii=False, separators=(",", ":")).encode(),
    ):
        if not 0 < len(value) <= 65535:
            raise ValueError("Prepared field exceeds bound")
        result += len(value).to_bytes(2, "big") + value
    if len(result) > 65519:
        raise ValueError("Prepared result exceeds Noise frame bound")
    return result


def ready_context(handshake: bytes, prologue: bytes, prepared: bytes) -> bytes:
    return hashlib.sha256(
        READY_TAG
        + handshake
        + hashlib.sha256(prologue).digest()
        + hashlib.sha256(prepared).digest()
    ).digest()


def verify_ready(
    data: bytes, prepared: bytes, context: bytes, approval: ApprovedRequest
) -> None:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import utils

    expected = READY_TAG + hashlib.sha256(prepared).digest()
    if len(data) != len(expected) + 64 or not data.startswith(expected):
        raise ValueError("Invalid prepared ready proof")
    key = serialization.load_der_public_key(approval.request.advertisement.access_spki)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("Invalid ready signing key")
    signature = data[len(expected) :]
    key.verify(
        utils.encode_dss_signature(
            int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
        ),
        _digest(context, b"ready"),
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
