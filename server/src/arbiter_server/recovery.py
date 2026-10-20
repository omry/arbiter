"""Offline recovery custody. Running servers retain only recovery public trust."""

from __future__ import annotations

import base64
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import UUID, uuid4

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from . import authority as a

WARNING = (
    "NEVER SHARE THIS. This file decrypts backups AND authorizes live identity "
    "recovery. Keep it outside server, agent, export-job and synced bundle storage."
)
MAX_FILE_BYTES = 65536


def _encode(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise a.AuthorityError("invalid recovery key storage")
    return base64.b64encode(data).decode("ascii")


def _decode(value: object, limit: int = 512) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((limit + 2) // 3):
        raise a.AuthorityError("invalid recovery key encoding")
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise a.AuthorityError("invalid recovery key encoding") from exc
    if not data or len(data) > limit or _encode(data) != value:
        raise a.AuthorityError("invalid recovery key encoding")
    return data


def _public(key: object) -> bytes:
    if not isinstance(key, (ec.EllipticCurvePublicKey, x25519.X25519PublicKey)):
        raise a.AuthorityError("unsupported recovery public key")
    return key.public_bytes(s.Encoding.DER, s.PublicFormat.SubjectPublicKeyInfo)


def _private(key: object) -> bytes:
    if not isinstance(key, (ec.EllipticCurvePrivateKey, x25519.X25519PrivateKey)):
        raise a.AuthorityError("unsupported recovery private key")
    return key.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())


def _load(value: object, *, private: bool, signing: bool) -> object:
    data = _decode(value)
    try:
        key = (
            s.load_der_private_key(data, password=None)
            if private
            else s.load_der_public_key(data)
        )
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise a.AuthorityError("invalid recovery key") from exc
    if signing:
        expected = ec.EllipticCurvePrivateKey if private else ec.EllipticCurvePublicKey
        valid = isinstance(key, expected) and isinstance(key.curve, ec.SECP256R1)
    else:
        expected = x25519.X25519PrivateKey if private else x25519.X25519PublicKey
        valid = isinstance(key, expected)
    if not valid or (_private(key) if private else _public(key)) != data:
        raise a.AuthorityError("wrong recovery key purpose or noncanonical DER")
    return key


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value, version=4)) != value:
        raise a.AuthorityError("invalid recovery identity")
    return value


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise a.AuthorityError("duplicate recovery field")
        result[key] = value
    return result


@dataclass
class RecoveryMaterial:
    owner_id: str
    signer: ec.EllipticCurvePrivateKey = field(repr=False)
    decryptor: x25519.X25519PrivateKey = field(repr=False)
    origins: dict[str, str] = field(default_factory=dict)
    original_sha256: str | None = field(default=None, repr=False)

    def document(self) -> dict[str, object]:
        return {
            "warning": WARNING,
            "schema": "arbiter-operator-recovery-v1",
            "owner_id": self.owner_id,
            "recovery_signer_pkcs8": _encode(_private(self.signer)),
            "backup_x25519_pkcs8": _encode(_private(self.decryptor)),
            "backup_origins_spki": self.origins,
        }


def _read(path: Path) -> RecoveryMaterial:
    a._check_path(path)
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        current = path.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise a.AuthorityError("recovery file changed while opening")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise a.AuthorityError("recovery file exceeds size bound")
    try:
        doc = json.loads(data, object_pairs_hook=_unique)
        if not isinstance(doc, dict) or set(doc) != {
            "warning",
            "schema",
            "owner_id",
            "recovery_signer_pkcs8",
            "backup_x25519_pkcs8",
            "backup_origins_spki",
        }:
            raise ValueError("wrong fields")
        if doc["schema"] != "arbiter-operator-recovery-v1" or doc["warning"] != WARNING:
            raise ValueError("wrong recovery schema")
        signer = _load(doc["recovery_signer_pkcs8"], private=True, signing=True)
        decryptor = _load(doc["backup_x25519_pkcs8"], private=True, signing=False)
        assert isinstance(signer, ec.EllipticCurvePrivateKey)
        assert isinstance(decryptor, x25519.X25519PrivateKey)
        origins = doc["backup_origins_spki"]
        if not isinstance(origins, dict):
            raise ValueError("invalid origins")
        for server, value in origins.items():
            _uuid(server)
            _load(value, private=False, signing=True)
        return RecoveryMaterial(
            _uuid(doc["owner_id"]),
            signer,
            decryptor,
            origins,
            hashlib.sha256(data).hexdigest(),
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise a.AuthorityError("invalid operator recovery file") from exc


@contextmanager
def setup_file(
    path: Path, state_root: Path, *, reuse: bool
) -> Iterator[tuple[Path, RecoveryMaterial]]:
    # A caller explicitly selects an existing private parent, never live state.
    path = path.expanduser().absolute()
    parent = a._root(path.parent, create=False)
    if not parent.is_dir():
        raise a.AuthorityError("prepare a private recovery directory first")
    target = parent / path.name
    if target.is_relative_to(state_root):
        raise a.AuthorityError("operator recovery file must be outside server state")
    with a._locked(parent):
        pending = target.with_name(f".{target.name}.arbiter-pending")
        if pending.exists() or pending.is_symlink():
            links = 1
            if target.exists() or target.is_symlink():
                staged, published = pending.lstat(), target.lstat()
                if (staged.st_dev, staged.st_ino) == (
                    published.st_dev,
                    published.st_ino,
                ):
                    # Only our exact no-overwrite publication pair may have two
                    # names. Both still need the complete private/native ACL check.
                    links = 2
                    a._check_path(target, expected_links=links)
            a._check_path(pending, expected_links=links)
            pending.unlink()
        exists = target.exists() or target.is_symlink()
        if exists and not reuse:
            raise a.AuthorityError("recovery file exists; explicit reuse is required")
        if reuse and not exists:
            raise a.AuthorityError("recovery file to reuse does not exist")
        material = (
            _read(target)
            if exists
            else RecoveryMaterial(
                str(uuid4()),
                ec.generate_private_key(ec.SECP256R1()),
                x25519.X25519PrivateKey.generate(),
            )
        )
        yield target, material


def register_and_publish(
    path: Path, record: a.AuthorityRecord, material: RecoveryMaterial, pending_db: Path
) -> None:
    """Public trust commits in unpublished DB; external custody publishes first."""
    origin = ec.generate_private_key(ec.SECP256R1())
    origin_public = _public(origin.public_key())
    material.origins[record.server_id] = _encode(origin_public)
    data = (json.dumps(material.document(), indent=2) + "\n").encode()
    if len(data) > MAX_FILE_BYTES:
        raise a.AuthorityError("recovery file exceeds size bound")
    with closing(sqlite3.connect(pending_db, timeout=0)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE recovery_trust (singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner_id TEXT NOT NULL, verifier BLOB NOT NULL, backup_recipient BLOB NOT NULL, origin_verifier BLOB NOT NULL, origin_private BLOB NOT NULL)"
        )
        db.execute(
            "INSERT INTO recovery_trust VALUES (1, ?, ?, ?, ?, ?)",
            (
                record.owner_id,
                _public(material.signer.public_key()),
                _public(material.decryptor.public_key()),
                origin_public,
                _private(origin),
            ),
        )
        db.commit()
    with pending_db.open("r+b") as handle:
        os.fsync(handle.fileno())
    # Private parent lock serializes explicit reuse, including separate servers.
    # Keep published recovery keys after any later failure; never unlink custody.
    staged = path.with_name(f".{path.name}.arbiter-pending")
    try:
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            a._check_path(staged)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if material.original_sha256 is None:
            # A new destination can never replace a file that appeared meanwhile.
            os.link(staged, path)
            staged.unlink()
            a._flush_parents(path.parent)
        else:
            a._check_path(path)
            with path.open("rb") as handle:
                current = handle.read(MAX_FILE_BYTES + 1)
            if hashlib.sha256(current).hexdigest() != material.original_sha256:
                raise a.AuthorityError("recovery file changed before publication")
            a._publish(staged, path)
    finally:
        if staged.exists():
            a._check_path(staged)
            staged.unlink()


def verify_registration(db: sqlite3.Connection, owner_id: str) -> None:
    rows = db.execute(
        "SELECT singleton, owner_id, verifier, backup_recipient, origin_verifier, origin_private FROM recovery_trust"
    ).fetchall()
    if len(rows) != 1 or rows[0][:2] != (1, owner_id):
        raise a.AuthorityError("missing or mismatched recovery trust")
    _, _, verifier, recipient, origin_verifier, origin_private = rows[0]
    recovery = _load(_encode(verifier), private=False, signing=True)
    _load(_encode(recipient), private=False, signing=False)
    origin_public = _load(_encode(origin_verifier), private=False, signing=True)
    origin = _load(_encode(origin_private), private=True, signing=True)
    assert isinstance(origin, ec.EllipticCurvePrivateKey)
    if _public(origin.public_key()) != _public(origin_public) or _public(
        recovery
    ) == _public(origin_public):
        raise a.AuthorityError("backup origin key mismatch or purpose reuse")
