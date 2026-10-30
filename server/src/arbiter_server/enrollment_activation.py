"""One durable enrollment result, committed only after private client readiness."""

from collections.abc import Callable
from contextlib import closing
from datetime import datetime
import json
import hashlib
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives import serialization

from .authority import _read_record
from .credentials import Identity, Role, verify_certificate
from .enrollment import ApprovedRequest
from .enrollment_receipt import result_fields
from .generations import ACTIVE, _control, _database
from .prepared import PREPARED_TAG


def activate(
    root: Path,
    approval: ApprovedRequest,
    raw: bytes,
    accounts: Callable[[], set[tuple[str, str]]],
    check: Callable[[], None],
    now: datetime,
) -> bytes:
    """Instance, request and responder ownership span this bounded worker call.

    The exact result contains the identity, all certificates, recipient key and
    approved grants. No second representation of those authorities is published.
    """
    ids, fields = result_fields(raw)
    ad = approval.request.advertisement
    offset = len(PREPARED_TAG) + 144
    if (
        raw[offset : offset + 22] != ad.selector.encode()
        or raw[offset + 22 : offset + 54] != bytes.fromhex(ad.digest)
        or raw[offset + 54 : offset + 86] != approval.request.revision.encode()
        or int.from_bytes(raw[offset + 86 : offset + 94], "big") <= now.timestamp()
        or fields[5] != ad.recipient_spki
        or tuple(tuple(pair) for pair in json.loads(fields[8])) != approval.grants
    ):
        raise ValueError("Enrollment activation unavailable")
    active = _control(root / ACTIVE)
    if active is None:
        raise ValueError("Enrollment activation unavailable")
    path = _database(root, active["active"])
    authority = _read_record(path)
    if ids[:2] != [authority.server_id, authority.owner_id]:
        raise ValueError("Enrollment activation unavailable")
    for index, identity, issuer, issuer_id, public in (
        (3, Identity(Role.PORTABLE, ids[2]), fields[2], ids[1], ad.portable_spki),
        (
            4,
            Identity(Role.ACCESS, ids[2], ids[0], ids[3]),
            fields[0],
            ids[0],
            ad.access_spki,
        ),
    ):
        cert = verify_certificate(
            fields[index], identity, issuer_der=issuer, issuer_id=issuer_id, now=now
        )
        if (
            cert.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            != public
        ):
            raise ValueError("Enrollment activation unavailable")
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            stored = dict(db.execute("SELECT role, certificate_der FROM credentials"))
            if (
                fields[0] != stored[Role.SERVER_AUTHORITY.value]
                or fields[2] != stored[Role.OWNER.value]
            ):
                raise ValueError("Enrollment activation unavailable")
            db.execute(
                "CREATE TABLE IF NOT EXISTS enrollments (enrollment_id TEXT PRIMARY KEY, "
                "prepared BLOB NOT NULL CHECK(length(prepared)<=65519), "
                "denied INTEGER NOT NULL CHECK(denied IN (0,1)), "
                "inactive_after INTEGER NOT NULL, selector TEXT NOT NULL UNIQUE, "
                "revision TEXT NOT NULL)"
            )
            existing = db.execute(
                "SELECT prepared, denied, inactive_after FROM enrollments "
                "WHERE enrollment_id=? OR selector=?",
                (ids[3], ad.selector),
            ).fetchone()
            if existing is not None:
                if (
                    bytes(existing[0]) != raw
                    or existing[1]
                    or existing[2] <= now.timestamp()
                ):
                    raise ValueError("Enrollment activation unavailable")
                if db.execute(
                    "SELECT enrollment_id FROM agent_enrollments WHERE agent_id=?",
                    (ids[2],),
                ).fetchone() != (ids[3],):
                    raise ValueError("Enrollment binding unavailable")
            else:
                if db.execute(
                    "SELECT active FROM owner_issuers WHERE issuer_sha256=? AND owner_id=? AND certificate_der=?",
                    (hashlib.sha256(fields[2]).hexdigest(), ids[1], fields[2]),
                ).fetchone() != (1,):
                    raise ValueError("Enrollment issuer unavailable")
                db.execute(
                    "INSERT INTO agent_enrollments VALUES (?, ?)", (ids[2], ids[3])
                )
                db.execute(
                    "INSERT INTO enrollments VALUES (?, ?, 0, ?, ?, ?)",
                    (
                        ids[3],
                        raw,
                        int(now.timestamp()) + 180 * 86400,
                        ad.selector,
                        approval.request.revision,
                    ),
                )
            if not set(approval.grants) <= accounts():
                raise ValueError("An approved account is no longer configured")
            # Account evaluation can invalidate or outlive the original request.
            # Consume its authorization only after that work, under the live
            # caller's attempt/approval locks. No such lock spans SQLite I/O.
            check()
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return raw
