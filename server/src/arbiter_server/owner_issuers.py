"""Exact public issuer trust and the authority's unique agent bindings."""

from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
from pathlib import Path
import sqlite3

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from . import authority as a
from .credentials import Identity, Role, parse_certificate, verify_certificate
from .enrollment_receipt import MAX_RESULT, result_fields
from .generations import ACTIVE, _control, _database
from .enrollment import Advertisement


def portable_issuer(
    db: sqlite3.Connection, advertisement: Advertisement, now: datetime
) -> tuple[bytes, int]:
    """Verify a claim against independently registered trust, not supplied trust."""
    claim = advertisement.portable_identity
    if claim is None:
        raise ValueError("Portable identity is missing")
    row = db.execute(
        "SELECT substr(certificate_der,1,4097), revision FROM owner_issuers "
        "WHERE issuer_sha256=? AND owner_id=? AND active=1",
        (claim.issuer_sha256, claim.owner_id),
    ).fetchone()
    if row is None or hashlib.sha256(row[0]).hexdigest() != claim.issuer_sha256:
        raise ValueError("Portable issuer is not active and registered")
    issuer, revision = row
    verify_certificate(issuer, Identity(Role.OWNER, claim.owner_id), now=now)
    certificate = verify_certificate(
        claim.certificate_der,
        Identity(Role.PORTABLE, claim.agent_id),
        issuer_der=issuer,
        issuer_id=claim.owner_id,
        now=now,
    )
    if (
        certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != advertisement.portable_spki
    ):
        raise ValueError("Portable key differs from the request")
    return issuer, revision


def approve_portable(root: Path, advertisement: Advertisement) -> tuple[bytes, int]:
    active = _control(root / ACTIVE)
    if active is None:
        raise ValueError("No live enrollment authority")
    path = _database(root, active["active"])
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    ) as db:
        return portable_issuer(db, advertisement, datetime.now(timezone.utc))


def create_tables(db: sqlite3.Connection, record: a.AuthorityRecord) -> None:
    db.execute(
        "CREATE TABLE owner_issuers (issuer_sha256 TEXT PRIMARY KEY "
        "CHECK(length(issuer_sha256)=64 AND issuer_sha256 NOT GLOB '*[^0-9a-f]*'), "
        "owner_id TEXT NOT NULL, certificate_der BLOB NOT NULL "
        "CHECK(typeof(certificate_der)='blob' AND length(certificate_der) BETWEEN 1 AND 4096), "
        "active INTEGER NOT NULL CHECK(active IN (0,1)), revision INTEGER NOT NULL "
        "CHECK(typeof(revision)='integer' AND revision>0))"
    )
    db.execute(
        "CREATE TABLE agent_enrollments (agent_id TEXT PRIMARY KEY, "
        "enrollment_id TEXT NOT NULL UNIQUE)"
    )
    der = db.execute(
        "SELECT certificate_der FROM credentials WHERE role=?", (Role.OWNER.value,)
    ).fetchone()[0]
    db.execute(
        "INSERT INTO owner_issuers VALUES (?, ?, ?, 1, 1)",
        (hashlib.sha256(der).hexdigest(), record.owner_id, der),
    )


def upgrade(path: Path, record: a.AuthorityRecord) -> None:
    """Caller holds the instance owner, after publication/journal reconciliation."""
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == a.SCHEMA_VERSION:
            return
        if version != 3:
            raise a.AuthorityError("unsupported authority database schema version")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            create_tables(db, record)
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrollments'"
            ).fetchone():
                for enrollment, size, raw in db.execute(
                    "SELECT enrollment_id, length(prepared), substr(prepared,1,?) "
                    "FROM enrollments",
                    (MAX_RESULT + 1,),
                ):
                    if not isinstance(raw, bytes) or size > MAX_RESULT:
                        raise a.AuthorityError("invalid historical enrollment result")
                    ids, _ = result_fields(raw)
                    if (
                        ids[:2] != [record.server_id, record.owner_id]
                        or ids[3] != enrollment
                    ):
                        raise a.AuthorityError(
                            "historical enrollment identity mismatch"
                        )
                    db.execute(
                        "INSERT INTO agent_enrollments VALUES (?, ?)", (ids[2], ids[3])
                    )
            db.execute(f"PRAGMA user_version={a.SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise


def fingerprint(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise a.AuthorityError("select an exact lowercase issuer SHA-256 fingerprint")
    return value


def control(
    root: Path, command: str, arguments: dict[str, object]
) -> dict[str, object]:
    """Existing OS-authenticated worker owns all I/O and certificate work."""
    expected = {
        "register": {"certificate_der"},
        "remove": {"issuer_sha256"},
        "export": {"issuer_sha256"},
    }
    if (command == "list" and set(arguments) not in (set(), {"cursor"})) or (
        command != "list"
        and (command not in expected or set(arguments) != expected[command])
    ):
        raise a.AuthorityError("invalid owner-issuer control arguments")
    owner, der, selected = None, None, None
    if command == "register":
        encoded = arguments["certificate_der"]
        if not isinstance(encoded, str) or len(encoded) > 5464:
            raise a.AuthorityError(
                "owner certificate must be bounded canonical DER/base64"
            )
        der = base64.b64decode(encoded, validate=True)
        if base64.b64encode(der).decode() != encoded:
            raise a.AuthorityError("noncanonical owner certificate encoding")
        cert = parse_certificate(der)
        try:
            uris = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
        except x509.ExtensionNotFound as exc:
            raise a.AuthorityError("owner certificate has no identity") from exc
        prefix = "urn:arbiter:owner:"
        if len(uris) != 1 or not uris[0].startswith(prefix):
            raise a.AuthorityError("owner certificate has the wrong identity")
        owner = uris[0][len(prefix) :]
        verify_certificate(
            der, Identity(Role.OWNER, owner), now=datetime.now(timezone.utc)
        )
        selected = hashlib.sha256(der).hexdigest()
    elif command in {"remove", "export"}:
        selected = fingerprint(arguments["issuer_sha256"])
    cursor = fingerprint(arguments["cursor"]) if "cursor" in arguments else ""
    if a._current_record(root) is None:
        raise a.AuthorityError("initialize server authority before managing issuers")
    active = _control(root / ACTIVE)
    assert active is not None
    path = _database(root, active["active"])
    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None
        )
    ) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE" if command in {"register", "remove"} else "BEGIN")
        if command == "register":
            row = db.execute(
                "SELECT owner_id, certificate_der FROM owner_issuers WHERE issuer_sha256=?",
                (selected,),
            ).fetchone()
            if row is not None and row != (owner, der):
                raise a.AuthorityError(
                    "registered issuer identity mismatch; preserve state"
                )
            db.execute(
                "INSERT INTO owner_issuers VALUES (?, ?, ?, 1, 1) "
                "ON CONFLICT(issuer_sha256) DO UPDATE SET active=1, revision=revision+1 WHERE active=0",
                (selected, owner, der),
            )
        elif command == "remove":
            db.execute(
                "UPDATE owner_issuers SET active=0, revision=revision+1 WHERE issuer_sha256=? AND active=1",
                (selected,),
            )
        if command == "list":
            rows = db.execute(
                "SELECT issuer_sha256, owner_id, active, revision FROM owner_issuers WHERE issuer_sha256>? ORDER BY issuer_sha256 LIMIT 33",
                (cursor,),
            ).fetchall()
            result: dict[str, object] = {
                "issuers": [
                    dict(zip(("issuer_sha256", "owner_id", "active", "revision"), row))
                    for row in rows[:32]
                ],
                "next_cursor": rows[31][0] if len(rows) > 32 else None,
            }
        else:
            row = db.execute(
                "SELECT owner_id, active, revision, substr(certificate_der,1,4097) FROM owner_issuers WHERE issuer_sha256=?",
                (selected,),
            ).fetchone()
            if row is None:
                raise a.AuthorityError("owner issuer is not registered")
            result = {
                "issuer_sha256": selected,
                "owner_id": row[0],
                "active": row[1],
                "revision": row[2],
            }
            if command == "export":
                if (
                    not isinstance(row[3], bytes)
                    or len(row[3]) > 4096
                    or hashlib.sha256(row[3]).hexdigest() != selected
                ):
                    raise a.AuthorityError("registered issuer bytes mismatch")
                result["certificate_der"] = base64.b64encode(row[3]).decode()
        db.commit()
    return {"initialized": True, **result}
