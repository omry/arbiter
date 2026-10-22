from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, ObjectIdentifier

from arbiter_server import authority, credentials as c

pytestmark = pytest.mark.usefixtures("private_windows_test_parent")


def dbpath(root):
    selector = root / "active-generation.json"
    name = (
        json.loads(selector.read_text())["active"]
        if selector.exists()
        else ".generation.pending"
    )
    return root / "generations" / name / authority.DATABASE


def initialize(root):
    parent = next(
        p
        for p in root.parents
        if p.is_dir() and (os.name == "nt" or not p.stat().st_mode & 0o077)
    )
    recovery = parent / (root.name + ".recovery.json")
    return authority.initialize_authority(
        root, recovery_file=recovery, reuse_recovery_file=recovery.exists()
    )


NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
OWNER = "00000000-0000-4000-8000-000000000001"
SERVER = "00000000-0000-4000-8000-000000000002"
AGENT = "00000000-0000-4000-8000-000000000003"
ENROLLMENT = "00000000-0000-4000-8000-000000000004"
P256_CURVE_OID = bytes.fromhex("06082a8648ce3d030107")
UNSUPPORTED_CURVE_OID = bytes.fromhex("06082a8648ce3d03017f")


@pytest.fixture
def issuers():
    return (
        c.create_issuer(c.Identity(c.Role.OWNER, OWNER), now=NOW),
        c.create_issuer(c.Identity(c.Role.SERVER_AUTHORITY, SERVER), now=NOW),
    )


def leaf(issuers, role=c.Role.ACCESS):
    issuer = issuers[0] if role is c.Role.PORTABLE else issuers[1]
    identity = c.Identity(
        role,
        SERVER if role is c.Role.SERVER_TLS else AGENT,
        SERVER if role is c.Role.ACCESS else None,
        ENROLLMENT if role is c.Role.ACCESS else None,
    )
    key = ec.generate_private_key(ec.SECP256R1())
    data = c.issue_certificate(identity, key.public_key(), issuer, now=NOW)
    return identity, data, issuer


def verify(data, identity, issuer, now=NOW):
    return c.verify_certificate(
        data,
        identity,
        issuer_der=issuer.certificate_der,
        issuer_id=issuer.identity.subject_id,
        now=now,
    )


def unsupported_curve(data):
    assert data.count(P256_CURVE_OID) == 1
    return data.replace(P256_CURVE_OID, UNSUPPORTED_CURVE_OID, 1)


@pytest.mark.parametrize("role", [c.Role.PORTABLE, c.Role.ACCESS, c.Role.SERVER_TLS])
def test_positive_role_and_exact_boundaries(issuers, role):
    identity, data, issuer = leaf(issuers, role)
    cert = verify(data, identity, issuer)
    assert cert.not_valid_after_utc == NOW + timedelta(days=365)
    for when in (cert.not_valid_before_utc, cert.not_valid_after_utc):
        assert (
            verify(data, identity, issuer, when).public_bytes(
                serialization.Encoding.DER
            )
            == data
        )
    for when in (
        cert.not_valid_before_utc - timedelta(microseconds=1),
        cert.not_valid_after_utc + timedelta(microseconds=1),
    ):
        with pytest.raises(c.CredentialError, match="currently valid"):
            verify(data, identity, issuer, when)
    due = cert.not_valid_after_utc - timedelta(days=30)
    assert not c.renewal_due(cert, now=due - timedelta(microseconds=1))
    assert c.renewal_due(cert, now=due)


def test_issuer_profile_and_renewal(issuers):
    for issuer in issuers:
        cert = c.verify_certificate(issuer.certificate_der, issuer.identity, now=NOW)
        assert cert.not_valid_after_utc == NOW + timedelta(days=3650)
        assert (
            cert.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value.path_length
            == 0
        )
        assert not cert.extensions.get_extension_for_class(
            x509.KeyUsage
        ).value.digital_signature
        due = cert.not_valid_after_utc - timedelta(days=366)
        assert not c.renewal_due(cert, now=due - timedelta(microseconds=1))
        assert c.renewal_due(cert, now=due)
    identity, _, issuer = leaf(issuers)
    with pytest.raises(c.CredentialError, match="renew the issuer"):
        c.issue_certificate(
            identity,
            ec.generate_private_key(ec.SECP256R1()).public_key(),
            issuer,
            now=NOW + timedelta(days=3286),
        )


@pytest.mark.parametrize("role", [c.Role.PORTABLE, c.Role.SERVER_TLS])
def test_cross_role_is_not_identity_or_native_authority(issuers, role):
    identity, data, issuer = leaf(issuers, role)
    access = c.Identity(c.Role.ACCESS, AGENT, SERVER, ENROLLMENT)
    with pytest.raises(c.CredentialError):
        verify(data, access, issuer)
    with pytest.raises(c.CredentialError, match="registered issuer"):
        c.verify_certificate(data, identity, now=NOW)


def test_registered_issuer_and_server_binding_are_required(issuers):
    identity, data, issuer = leaf(issuers)
    impostor = c.create_issuer(issuer.identity, now=NOW)
    with pytest.raises(c.CredentialError, match="not signed"):
        verify(data, identity, impostor)
    with pytest.raises(c.CredentialError):
        verify(data, identity, issuers[0])
    with pytest.raises(c.CredentialError, match="server authority binding"):
        c.verify_certificate(
            data, identity, issuer_der=issuer.certificate_der, issuer_id=OWNER, now=NOW
        )
    with pytest.raises(c.CredentialError):
        c.issue_certificate(
            c.Identity(c.Role.OWNER, OWNER),
            issuer.private_key.public_key(),
            issuer,
            now=NOW,
        )
    with pytest.raises(c.CredentialError, match="separate"):
        c.issue_certificate(identity, issuer.private_key.public_key(), issuer, now=NOW)


def test_unsupported_certificate_key_and_version_are_credential_errors(issuers):
    identity, data, issuer = leaf(issuers, c.Role.SERVER_TLS)
    with pytest.raises(c.CredentialError):
        verify(unsupported_curve(data), identity, issuer)
    invalid_version = data.replace(
        bytes.fromhex("a003020102"), bytes.fromhex("a003020103"), 1
    )
    with pytest.raises(c.CredentialError):
        c.parse_certificate(invalid_version)


@pytest.mark.parametrize(
    "identity",
    [
        ("owner", OWNER, None, None),
        (c.Role.OWNER, "bad", None, None),
        (c.Role.OWNER, OWNER, SERVER, None),
        (c.Role.ACCESS, AGENT, SERVER, None),
        (c.Role.ACCESS, AGENT, SERVER, AGENT),
    ],
)
def test_invalid_or_duplicate_identity_is_rejected(identity):
    with pytest.raises(c.CredentialError):
        c.Identity(*identity)


def changed_leaf(issuers, change, optional=None):
    identity, data, issuer = leaf(issuers)
    original = c.parse_certificate(data)
    key = original.public_key()
    if change == "curve":
        key = ec.generate_private_key(ec.SECP384R1()).public_key()
    start = original.not_valid_before_utc
    end = original.not_valid_after_utc
    if change == "lifetime":
        end += timedelta(days=1)
    if change == "issuer-window":
        start -= timedelta(minutes=1)
        end -= timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(original.subject)
        .issuer_name(original.issuer)
        .public_key(key)
        .serial_number(123)
        .not_valid_before(start)
        .not_valid_after(end)
    )
    for ext in original.extensions:
        value = ext.value
        if change == "missing-usage" and isinstance(value, x509.KeyUsage):
            continue
        if change == "eku" and isinstance(value, x509.ExtendedKeyUsage):
            value = x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])
        if change == "ca" and isinstance(value, x509.BasicConstraints):
            value = x509.BasicConstraints(True, 1)
        if change in {"duplicate-san", "dns-san"} and isinstance(
            value, x509.SubjectAlternativeName
        ):
            names = list(value)
            names.append(
                names[0] if change == "duplicate-san" else x509.DNSName("example.test")
            )
            value = x509.SubjectAlternativeName(names)
        builder = builder.add_extension(value, ext.critical)
    if optional is not None:
        builder = builder.add_extension(optional[0], optional[1])
    if change == "critical":
        builder = builder.add_extension(
            x509.UnrecognizedExtension(ObjectIdentifier("1.2.3.4"), b"\x05\x00"), True
        )
    digest = hashes.SHA384() if change == "signature" else hashes.SHA256()
    return (
        identity,
        builder.sign(issuer.private_key, digest).public_bytes(
            serialization.Encoding.DER
        ),
        issuer,
    )


@pytest.mark.parametrize(
    "change",
    [
        "curve",
        "lifetime",
        "issuer-window",
        "missing-usage",
        "eku",
        "ca",
        "duplicate-san",
        "dns-san",
        "critical",
        "signature",
    ],
)
def test_validly_signed_malformed_profiles_are_rejected(issuers, change):
    identity, data, issuer = changed_leaf(issuers, change)
    with pytest.raises(c.CredentialError):
        verify(data, identity, issuer)


def _der_content(data):
    return data[2 + (data[1] & 127) if data[1] & 128 else 2 :]


def _der_wrap(tag, content):
    size = len(content)
    width = (size.bit_length() + 7) // 8
    length = (
        bytes([size])
        if size < 128
        else bytes([128 + width]) + size.to_bytes(width, "big")
    )
    return bytes([tag]) + length + content


def _sequence_fields(data):
    assert data[0] == 0x30
    content = _der_content(data)
    fields = []
    while content:
        width = content[1] & 127 if content[1] & 128 else 0
        size = int.from_bytes(content[2 : 2 + width], "big") if width else content[1]
        end = 2 + width + size
        assert end <= len(content)
        fields.append(content[:end])
        content = content[end:]
    return fields


def _signed_profile_variant(data, signer, change):
    outer = _sequence_fields(data)
    tbs = _sequence_fields(outer[0])
    if change in {"inner-null", "both-null"}:
        tbs[2] = _der_wrap(0x30, _der_content(tbs[2]) + b"\x05\x00")
    if change in {"outer-null", "both-null"}:
        outer[1] = _der_wrap(0x30, _der_content(outer[1]) + b"\x05\x00")
    if change in {"negative-serial", "zero-serial"}:
        tbs[1] = b"\x02\x01" + (b"\xff" if change == "negative-serial" else b"\x00")
    outer[0] = _der_wrap(0x30, b"".join(tbs))
    signature = signer.private_key.sign(outer[0], ec.ECDSA(hashes.SHA256()))
    outer[2] = _der_wrap(0x03, b"\x00" + signature)
    return _der_wrap(0x30, b"".join(outer))


@pytest.mark.parametrize("role", list(c.Role))
@pytest.mark.parametrize(
    "change",
    [
        "control",
        "inner-null",
        "outer-null",
        "both-null",
        "negative-serial",
        "zero-serial",
    ],
)
@pytest.mark.filterwarnings("ignore::cryptography.utils.CryptographyDeprecationWarning")
def test_signed_algorithm_and_serial_profile(issuers, role, change):
    if role in {c.Role.OWNER, c.Role.SERVER_AUTHORITY}:
        signer = issuers[0] if role is c.Role.OWNER else issuers[1]
        identity, data, issuer = signer.identity, signer.certificate_der, None
    else:
        identity, data, issuer = leaf(issuers, role)
        signer = issuer
    altered = _signed_profile_variant(data, signer, change)
    parsed = x509.load_der_x509_certificate(altered)
    list(parsed.extensions)
    signer.private_key.public_key().verify(
        parsed.signature, parsed.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256())
    )
    kwargs = (
        {}
        if issuer is None
        else {
            "issuer_der": issuer.certificate_der,
            "issuer_id": issuer.identity.subject_id,
        }
    )
    if change in {"control", "zero-serial"}:
        c.verify_certificate(altered, identity, now=NOW, **kwargs)
    else:
        with pytest.raises(c.CredentialError):
            c.verify_certificate(altered, identity, now=NOW, **kwargs)


@pytest.mark.parametrize(
    "oid,raw,critical,valid",
    [
        ("2.5.29.14", b"\x04\x01\xaa", False, True),
        ("2.5.29.14", b"\x04\x00", False, False),
        ("2.5.29.14", b"\x04\x01\xaa\x05\x00", False, False),
        ("2.5.29.35", b"\x30\x03\x80\x01\xaa", False, True),
        ("2.5.29.35", b"\x30\x02\x80\x00", False, False),
        ("2.5.29.35", b"\x30\x05\x80\x01\xaa\x05\x00", False, False),
        ("2.5.29.35", b"\x30\x03\x80\x01\xaa\x05\x00", False, False),
        ("1.3.36.8.3.3", b"\x30\x02\x30\x00", False, False),
        ("1.2.3.4", b"\x05\x00", False, True),
        ("1.2.3.4", b"\x05\x00", True, False),
        ("1.2.3.4", b"\x05\x00\x05\x00", False, False),
        ("1.2.3.4", bytes.fromhex("9f888080800000"), False, False),
        ("1.2.3.4", bytes.fromhex("9f87ffffff7f00"), False, True),
    ],
)
def test_signed_optional_extension_profile(issuers, oid, raw, critical, valid):
    extension = x509.UnrecognizedExtension(ObjectIdentifier(oid), raw)
    identity, data, issuer = changed_leaf(issuers, "optional", (extension, critical))
    x509.load_der_x509_certificate(data)
    if valid:
        verify(data, identity, issuer)
    else:
        with pytest.raises(c.CredentialError):
            verify(data, identity, issuer)


@pytest.mark.parametrize(
    "change", ["empty", "oversize", "trailing", "signature", "garbage"]
)
def test_der_boundaries_and_signature(issuers, change):
    identity, data, issuer = leaf(issuers)
    malformed = {
        "empty": b"",
        "oversize": b"x" * (c.MAX_CERTIFICATE_BYTES + 1),
        "trailing": data + b"x",
        "signature": data[:-1] + bytes([data[-1] ^ 1]),
        "garbage": b"not DER",
    }[change]
    with pytest.raises(c.CredentialError):
        verify(malformed, identity, issuer)


def test_setup_is_real_atomic_private_and_restartable(tmp_path, capsys):
    root = tmp_path / "state"
    assert (
        authority.authority_cli(
            "init", root, as_json=True, recovery_file=tmp_path / "cli.recovery.json"
        )
        == 0
    )
    output = capsys.readouterr().out
    record = authority.authority_status(root)
    assert (
        record is not None and record.owner_id in output and record.server_id in output
    )
    with closing(sqlite3.connect(dbpath(root))) as db:
        rows = db.execute(
            "SELECT role, certificate_der, private_key_der FROM credentials"
        ).fetchall()
    assert len(rows) == 3
    c.verify_setup_credentials(
        rows,
        record.owner_id,
        record.server_id,
        datetime.fromisoformat(record.created_at),
    )
    for _, _, private in rows:
        assert base64.b64encode(private).decode() not in output
    before = (dbpath(root)).read_bytes()
    with pytest.raises(authority.AuthorityError, match="already exists"):
        initialize(root)
    assert authority.authority_status(root) == record
    assert (dbpath(root)).read_bytes() == before
    assert {p.name for p in root.iterdir()} == {
        "generations",
        "active-generation.json",
        authority._LOCK,
    }


@pytest.mark.parametrize(
    "damage",
    ["missing", "key", "oversize", "cert", "reuse", "old-schema", "unsupported-key"],
)
def test_damaged_setup_never_repairs_or_replaces_keys(tmp_path, damage, capsys):
    root = tmp_path / "state"
    initialize(root)
    with closing(sqlite3.connect(dbpath(root))) as db:
        if damage == "old-schema":
            db.execute("PRAGMA user_version=1")
        elif damage == "missing":
            db.execute("DELETE FROM credentials WHERE role='owner'")
        else:
            column = "certificate_der" if damage == "cert" else "private_key_der"
            value = b"x" * 513 if damage == "oversize" else b"invalid"
            if damage == "reuse":
                value = db.execute(
                    "SELECT private_key_der FROM credentials WHERE role='server-authority'"
                ).fetchone()[0]
            if damage == "unsupported-key":
                value = unsupported_curve(
                    db.execute(
                        "SELECT private_key_der FROM credentials WHERE role='owner'"
                    ).fetchone()[0]
                )
            db.execute(
                f"UPDATE credentials SET {column}=? WHERE role='owner'", (value,)
            )
        db.commit()
    before = (dbpath(root)).read_bytes()
    with pytest.raises(authority.AuthorityError):
        authority.authority_status(root)
    assert (dbpath(root)).read_bytes() == before
    if damage == "unsupported-key":
        assert authority.authority_cli("status", root, as_json=True) == 1
        captured = capsys.readouterr()
        assert not captured.out
        assert "invalid or incomplete authority database" in captured.err


def test_crypto_failure_cannot_publish_partial_setup(tmp_path, monkeypatch):
    root = tmp_path / "state"
    real = c.store_setup_credentials

    def fail(db, owner, server, now):
        real(db, owner, server, now)
        raise OSError("injected credential transaction failure")

    monkeypatch.setattr(c, "store_setup_credentials", fail)
    with pytest.raises(OSError, match="injected"):
        initialize(root)
    assert {p.name for p in root.iterdir()} == {authority._LOCK, "generations"}
    monkeypatch.setattr(c, "store_setup_credentials", real)
    assert initialize(root) == authority.authority_status(root)


def test_shared_profile_fixtures():
    path = (
        Path(__file__).resolve().parents[3]
        / "tests/fixtures/authentication/credential-profiles-v1.json"
    )
    data = json.loads(path.read_text())
    assert (
        data["schema"] == "arbiter-credential-profiles-v1" and data["test_only"] is True
    )
    now = datetime.fromisoformat(data["now"])
    for case in data["cases"]:
        identity = c.Identity(c.Role(case["role"]), *case["ids"])
        cert = base64.b64decode(
            data["certificates"][case["certificate"]], validate=True
        )
        issuer = (
            base64.b64decode(data["certificates"][case["issuer"]], validate=True)
            if case.get("issuer")
            else None
        )
        if case["valid"]:
            c.verify_certificate(
                cert,
                identity,
                issuer_der=issuer,
                issuer_id=case.get("issuer_id"),
                now=now,
            )
        else:
            with pytest.raises(c.CredentialError):
                c.verify_certificate(
                    cert,
                    identity,
                    issuer_der=issuer,
                    issuer_id=case.get("issuer_id"),
                    now=now,
                )
