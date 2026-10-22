"""Version-one Arbiter certificate profiles; parsing does not register trust."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import sqlite3
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, SignatureAlgorithmOID

MAX_CERTIFICATE_BYTES = 4096
MAX_PRIVATE_KEY_BYTES = 512
ISSUER_LIFETIME = timedelta(days=3650)
LEAF_LIFETIME = timedelta(days=365)
BACKDATE = timedelta(minutes=5)
ISSUER_RENEW_BEFORE = timedelta(days=366)
LEAF_RENEW_BEFORE = timedelta(days=30)
SUPPORTED_OPTIONAL_OIDS = {"2.5.29.14", "2.5.29.35"}
UNSUPPORTED_OPTIONAL_OIDS = {
    "1.3.36.8.3.3",
    "1.3.6.1.4.1.11129.2.4.2",
    "1.3.6.1.4.1.11129.2.4.3",
    "1.3.6.1.4.1.11129.2.4.5",
    "1.3.6.1.4.1.311.21.7",
    "1.3.6.1.5.5.7.1.1",
    "1.3.6.1.5.5.7.1.11",
    "1.3.6.1.5.5.7.1.24",
    "1.3.6.1.5.5.7.48.1.5",
    "2.5.29.9",
    "2.5.29.16",
    "2.5.29.18",
    "2.5.29.20",
    "2.5.29.27",
    "2.5.29.28",
    "2.5.29.30",
    "2.5.29.31",
    "2.5.29.32",
    "2.5.29.33",
    "2.5.29.36",
    "2.5.29.46",
    "2.5.29.54",
}


class CredentialError(ValueError):
    """Credential bytes do not satisfy the selected registered purpose."""


class Role(str, Enum):
    OWNER = "owner"
    SERVER_AUTHORITY = "server-authority"
    PORTABLE = "portable"
    ACCESS = "access"
    SERVER_TLS = "server"


@dataclass(frozen=True)
class Identity:
    role: Role
    subject_id: str
    server_id: str | None = None
    enrollment_id: str | None = None

    def __post_init__(self) -> None:
        self.uris()

    def uris(self) -> tuple[str, ...]:
        if not isinstance(self.role, Role):
            raise CredentialError("unknown credential role")
        pairs: list[tuple[str, str | None]] = [(self.role.value, self.subject_id)]
        if self.role is Role.ACCESS:
            pairs.extend(
                (("server", self.server_id), ("enrollment", self.enrollment_id))
            )
        elif self.server_id is not None or self.enrollment_id is not None:
            raise CredentialError("unexpected credential identity binding")
        ids = [value for _, value in pairs]
        for value in ids:
            try:
                valid = isinstance(value, str) and str(UUID(value, version=4)) == value
            except ValueError:
                valid = False
            if not valid:
                raise CredentialError(
                    "credential IDs must be canonical version-four UUIDs"
                )
        if len(set(ids)) != len(ids):
            raise CredentialError("duplicate credential identity")
        return tuple(f"urn:arbiter:{role}:{value}" for role, value in pairs)


@dataclass(frozen=True)
class SigningCredential:
    identity: Identity
    certificate_der: bytes
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)


def _utc(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CredentialError("credential time must be UTC")
    return value


def _ca(role: Role) -> bool:
    return role in {Role.OWNER, Role.SERVER_AUTHORITY}


def _key_usage(ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(not ca, False, False, False, False, ca, False, False, False)


def _public(key: object) -> ec.EllipticCurvePublicKey:
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise CredentialError("credential requires a P-256 public key")
    return key


def _spki(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def parse_certificate(data: bytes) -> x509.Certificate:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_CERTIFICATE_BYTES:
        raise CredentialError("certificate exceeds the DER size bound")
    try:
        cert = x509.load_der_x509_certificate(data)
        if cert.public_bytes(serialization.Encoding.DER) != data:
            raise ValueError("noncanonical or trailing DER")
        # Materialize extensions now so duplicate or malformed extensions fail.
        list(cert.extensions)
        return cert
    except (
        ValueError,
        x509.InvalidVersion,
        x509.DuplicateExtension,
        x509.UnsupportedGeneralNameType,
    ) as exc:
        raise CredentialError("invalid certificate DER") from exc


def _der_bounds(data: bytes, offset: int = 0) -> tuple[int, int]:
    if len(data) - offset < 2:
        raise CredentialError("incomplete DER element")
    high_tag = data[offset] & 0x1F == 0x1F
    offset += 1
    if high_tag:
        if offset >= len(data) or data[offset] == 0x80:
            raise CredentialError("invalid DER tag")
        tag = 0
        while True:
            if offset >= len(data):
                raise CredentialError("incomplete DER tag")
            value = data[offset]
            offset += 1
            tag = tag * 128 + (value & 0x7F)
            if value & 0x80 == 0:
                break
        if tag < 0x1F or tag > 0x7FFFFFFF:
            raise CredentialError("invalid DER tag")
    if offset >= len(data):
        raise CredentialError("missing DER length")
    length = data[offset]
    offset += 1
    if length & 0x80:
        width = length & 0x7F
        if width == 0 or width > 8 or offset + width > len(data):
            raise CredentialError("invalid DER length")
        if data[offset] == 0 or width == 1 and data[offset] < 0x80:
            raise CredentialError("nonminimal DER length")
        length = int.from_bytes(data[offset : offset + width], "big")
        offset += width
    end = offset + length
    if end > len(data):
        raise CredentialError("incomplete DER content")
    return offset, end


def _one_der(data: bytes) -> bool:
    try:
        start, end = _der_bounds(data)
        if end != len(data):
            return False
        pending = [(0, start, end)]
        while pending:
            tag, start, end = pending.pop()
            if not data[tag] & 0x20:
                continue
            offset = start
            while offset < end:
                content, next_offset = _der_bounds(data, offset)
                if next_offset > end:
                    return False
                pending.append((offset, content, next_offset))
                offset = next_offset
        return True
    except CredentialError:
        return False


def _der_sequence_parts(data: bytes) -> list[bytes]:
    offset, end = _der_bounds(data)
    if data[0] != 0x30 or end != len(data):
        raise CredentialError("expected one complete DER SEQUENCE")
    parts = []
    while offset < end:
        _, next_offset = _der_bounds(data, offset)
        parts.append(data[offset:next_offset])
        offset = next_offset
    return parts


def _signature_profile(cert: x509.Certificate) -> None:
    outer = _der_sequence_parts(cert.public_bytes(serialization.Encoding.DER))
    tbs = _der_sequence_parts(cert.tbs_certificate_bytes)
    # ECDSA-with-SHA256 AlgorithmIdentifier: OID 1.2.840.10045.4.3.2,
    # with absent parameters. Compare complete structurally selected fields.
    expected = bytes.fromhex("300a06082a8648ce3d040302")
    if len(tbs) != 8:
        raise CredentialError("unsupported optional certificate fields")
    if len(outer) != 3 or outer[1] != expected or tbs[2] != expected:
        raise CredentialError("invalid ECDSA signature AlgorithmIdentifier")


def _validate_optional_extension(extension: x509.Extension[x509.ExtensionType]) -> None:
    oid = extension.oid.dotted_string
    if oid in SUPPORTED_OPTIONAL_OIDS:
        if extension.critical:
            raise CredentialError("supported optional extension must be noncritical")
        if oid == "2.5.29.14" and (
            not isinstance(extension.value, x509.SubjectKeyIdentifier)
            or not extension.value.digest
        ):
            raise CredentialError("invalid subject key identifier extension")
        if oid == "2.5.29.35":
            value = extension.value
            if (
                not isinstance(value, x509.AuthorityKeyIdentifier)
                or not value.key_identifier
                or value.authority_cert_issuer is not None
                or value.authority_cert_serial_number is not None
            ):
                raise CredentialError("invalid authority key identifier extension")
        return
    if oid in UNSUPPORTED_OPTIONAL_OIDS:
        raise CredentialError("unsupported optional certificate extension")
    if not isinstance(extension.value, x509.UnrecognizedExtension) or not _one_der(
        extension.value.value
    ):
        raise CredentialError("unknown extension is not one complete DER value")


def _profile(cert: x509.Certificate, identity: Identity, now: datetime) -> None:
    ca = _ca(identity.role)
    expected: dict[x509.ObjectIdentifier, x509.ExtensionType] = {
        x509.ExtensionOID.BASIC_CONSTRAINTS: x509.BasicConstraints(
            ca, 0 if ca else None
        ),
        x509.ExtensionOID.KEY_USAGE: _key_usage(ca),
        x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME: x509.SubjectAlternativeName(
            [x509.UniformResourceIdentifier(uri) for uri in identity.uris()]
        ),
    }
    if not ca:
        purpose = (
            ExtendedKeyUsageOID.SERVER_AUTH
            if identity.role is Role.SERVER_TLS
            else ExtendedKeyUsageOID.CLIENT_AUTH
        )
        expected[x509.ExtensionOID.EXTENDED_KEY_USAGE] = x509.ExtendedKeyUsage(
            [purpose]
        )
    for oid, value in expected.items():
        try:
            extension = cert.extensions.get_extension_for_oid(oid)
        except x509.ExtensionNotFound as exc:
            raise CredentialError("missing credential profile extension") from exc
        if not extension.critical or extension.value != value:
            raise CredentialError("wrong credential role, identity or key usage")
    if ca and any(
        e.oid == x509.ExtensionOID.EXTENDED_KEY_USAGE for e in cert.extensions
    ):
        raise CredentialError("issuer must not have an ordinary leaf purpose")
    for extension in cert.extensions:
        if extension.oid in expected:
            continue
        if (
            extension.critical
            or ca
            and extension.oid == x509.ExtensionOID.EXTENDED_KEY_USAGE
        ):
            raise CredentialError("unknown critical certificate extension")
        _validate_optional_extension(extension)
    try:
        public_key = cert.public_key()
    except (UnsupportedAlgorithm, ValueError) as exc:
        raise CredentialError("credential requires a supported public key") from exc
    _public(public_key)
    if (
        cert.version is not x509.Version.v3
        or cert.signature_algorithm_oid != SignatureAlgorithmOID.ECDSA_WITH_SHA256
        or cert.serial_number < 0
    ):
        raise CredentialError(
            "unsupported certificate version, serial or signature algorithm"
        )
    _signature_profile(cert)
    lifetime = ISSUER_LIFETIME if ca else LEAF_LIFETIME
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise CredentialError("certificate is not currently valid")
    if (
        not timedelta(0)
        < cert.not_valid_after_utc - cert.not_valid_before_utc
        <= lifetime + BACKDATE
    ):
        raise CredentialError("certificate validity exceeds its role profile")


def verify_certificate(
    data: bytes,
    identity: Identity,
    *,
    issuer_der: bytes | None = None,
    issuer_id: str | None = None,
    now: datetime | None = None,
) -> x509.Certificate:
    """Verify a purpose against trusted caller-selected issuer bytes and IDs.

    Self-signed issuer validation checks structure only. It never registers that
    issuer. Leaf callers must obtain issuer_der from their registered trust store.
    """
    when = _utc(now)
    cert = parse_certificate(data)
    _profile(cert, identity, when)
    if _ca(identity.role):
        if issuer_der is not None or issuer_id is not None:
            raise CredentialError("Arbiter issuers must be self-signed roots")
        issuer = cert
    else:
        if issuer_der is None or issuer_id is None:
            raise CredentialError("a registered issuer and its identity are required")
        issuer_role = (
            Role.OWNER if identity.role is Role.PORTABLE else Role.SERVER_AUTHORITY
        )
        if identity.role is not Role.PORTABLE:
            server = (
                identity.server_id
                if identity.role is Role.ACCESS
                else identity.subject_id
            )
            if issuer_id != server:
                raise CredentialError("wrong server authority binding")
        issuer = verify_certificate(
            issuer_der, Identity(issuer_role, issuer_id), now=when
        )
        if (
            not issuer.not_valid_before_utc <= cert.not_valid_before_utc
            or cert.not_valid_after_utc > issuer.not_valid_after_utc
        ):
            raise CredentialError("leaf validity exceeds its issuer")
        if _spki(_public(cert.public_key())) == _spki(_public(issuer.public_key())):
            raise CredentialError("issuer and leaf keys must be separate")
    try:
        cert.verify_directly_issued_by(issuer)
    except (InvalidSignature, ValueError) as exc:
        raise CredentialError(
            "certificate was not signed by the registered issuer"
        ) from exc
    return cert


def _build(
    identity: Identity,
    key: ec.EllipticCurvePublicKey,
    signer: ec.EllipticCurvePrivateKey,
    issuer: x509.Certificate | None,
    now: datetime,
) -> bytes:
    ca = _ca(identity.role)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"Arbiter {identity.role.value}")]
    )
    start = now.replace(microsecond=0) - BACKDATE
    end = now.replace(microsecond=0) + (ISSUER_LIFETIME if ca else LEAF_LIFETIME)
    if issuer is not None and (
        start < issuer.not_valid_before_utc or end > issuer.not_valid_after_utc
    ):
        raise CredentialError("renew the issuer before issuing a full-lifetime leaf")
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer is not None else subject)
        .public_key(_public(key))
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca, 0 if ca else None), critical=True)
        .add_extension(_key_usage(ca), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(uri) for uri in identity.uris()]
            ),
            critical=True,
        )
    )
    if not ca:
        purpose = (
            ExtendedKeyUsageOID.SERVER_AUTH
            if identity.role is Role.SERVER_TLS
            else ExtendedKeyUsageOID.CLIENT_AUTH
        )
        builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=True)
    return builder.sign(signer, hashes.SHA256()).public_bytes(
        serialization.Encoding.DER
    )


def create_issuer(
    identity: Identity, *, now: datetime | None = None
) -> SigningCredential:
    if not _ca(identity.role):
        raise CredentialError("only owner/server authority roles are issuers")
    key = ec.generate_private_key(ec.SECP256R1())
    data = _build(identity, key.public_key(), key, None, _utc(now))
    verify_certificate(data, identity, now=now)
    return SigningCredential(identity, data, key)


def issue_certificate(
    identity: Identity,
    public_key: ec.EllipticCurvePublicKey,
    issuer: SigningCredential,
    *,
    now: datetime | None = None,
) -> bytes:
    if _ca(identity.role):
        raise CredentialError("issuers cannot create subordinate CAs")
    when = _utc(now)
    ca = verify_certificate(issuer.certificate_der, issuer.identity, now=when)
    if not _ca(issuer.identity.role) or _spki(_public(ca.public_key())) != _spki(
        issuer.private_key.public_key()
    ):
        raise CredentialError("issuer signing key or purpose mismatch")
    data = _build(identity, public_key, issuer.private_key, ca, when)
    verify_certificate(
        data,
        identity,
        issuer_der=issuer.certificate_der,
        issuer_id=issuer.identity.subject_id,
        now=when,
    )
    return data


def renewal_due(cert: x509.Certificate, *, now: datetime | None = None) -> bool:
    ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    return _utc(now) >= cert.not_valid_after_utc - (
        ISSUER_RENEW_BEFORE if ca else LEAF_RENEW_BEFORE
    )


def store_setup_credentials(
    db: sqlite3.Connection, owner_id: str, server_id: str, now: datetime
) -> None:
    """Called inside the unpublished authority DB transaction; no key files."""
    owner = create_issuer(Identity(Role.OWNER, owner_id), now=now)
    server = create_issuer(Identity(Role.SERVER_AUTHORITY, server_id), now=now)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    identity = Identity(Role.SERVER_TLS, server_id)
    leaf = SigningCredential(
        identity,
        issue_certificate(identity, leaf_key.public_key(), server, now=now),
        leaf_key,
    )
    db.execute(
        "CREATE TABLE credentials (role TEXT PRIMARY KEY, certificate_der BLOB NOT NULL, private_key_der BLOB NOT NULL)"
    )
    for item in (owner, server, leaf):
        private = item.private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        db.execute(
            "INSERT INTO credentials VALUES (?, ?, ?)",
            (item.identity.role.value, item.certificate_der, private),
        )


def verify_setup_credentials(
    rows: list[tuple[str, bytes, bytes]], owner_id: str, server_id: str, now: datetime
) -> None:
    material = {role: (cert, key) for role, cert, key in rows}
    if len(rows) != 3 or set(material) != {
        Role.OWNER.value,
        Role.SERVER_AUTHORITY.value,
        Role.SERVER_TLS.value,
    }:
        raise CredentialError("incomplete or duplicate setup credential roles")
    keys = set()
    for role, identifier in (
        (Role.OWNER, owner_id),
        (Role.SERVER_AUTHORITY, server_id),
        (Role.SERVER_TLS, server_id),
    ):
        cert_data, key_data = material[role.value]
        if (
            not isinstance(key_data, bytes)
            or not 0 < len(key_data) <= MAX_PRIVATE_KEY_BYTES
        ):
            raise CredentialError("invalid private credential size")
        try:
            key = serialization.load_der_private_key(key_data, password=None)
        except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
            raise CredentialError("invalid private credential encoding") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise CredentialError("private credential requires P-256")
        if (
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            != key_data
        ):
            raise CredentialError("private credential must use canonical PKCS8 DER")
        cert = verify_certificate(
            cert_data,
            Identity(role, identifier),
            now=now,
            issuer_der=None if _ca(role) else material[Role.SERVER_AUTHORITY.value][0],
            issuer_id=None if _ca(role) else server_id,
        )
        public = _spki(key.public_key())
        if public != _spki(_public(cert.public_key())) or public in keys:
            raise CredentialError("setup credential key mismatch or key reuse")
        keys.add(public)
