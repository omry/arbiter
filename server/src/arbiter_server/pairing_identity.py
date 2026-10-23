"""Read existing server proof material while the authority instance is owned."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .authority import AuthorityError, _read_record
from .credentials import Identity, Role, SigningCredential, verify_certificate
from .generations import ACTIVE, _control, _database


def load_pairing_identity(root: Path) -> tuple[SigningCredential, bytes] | None:
    """Caller holds running_instance; never acquire its lock recursively."""
    # Startup/local setup owns publication recovery. A network proof only reads
    # the already-published selector; it must not finish a pending generation.
    active = _control(root / ACTIVE)
    if active is None:
        return None
    path = _database(root, active["active"])
    record = _read_record(path)
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    ) as db:
        cert, private = db.execute(
            "SELECT certificate_der, private_key_der FROM credentials WHERE role = ?",
            (Role.SERVER_TLS.value,),
        ).fetchone()
        authority = db.execute(
            "SELECT certificate_der FROM credentials WHERE role = ?",
            (Role.SERVER_AUTHORITY.value,),
        ).fetchone()[0]
    key = serialization.load_der_private_key(private, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise AuthorityError("invalid server pairing key")
    identity = Identity(Role.SERVER_TLS, record.server_id)
    verify_certificate(
        cert,
        identity,
        issuer_der=authority,
        issuer_id=record.server_id,
        now=datetime.now(timezone.utc),
    )
    return SigningCredential(identity, cert, key), authority
