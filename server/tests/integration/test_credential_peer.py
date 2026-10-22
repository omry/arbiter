"""The Go verifier consumes freshly issued Python credentials, not mock crypto."""

from __future__ import annotations

import base64
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ObjectIdentifier

from arbiter_server import credentials as c


def _split_der(data: bytes) -> list[bytes]:
    parts: list[bytes] = []
    offset = 0
    while offset < len(data):
        size, header = data[offset + 1], 2
        if size & 128:
            length_bytes = size & 127
            size = int.from_bytes(data[offset + 2 : offset + 2 + length_bytes], "big")
            header += length_bytes
        parts.append(data[offset : offset + header + size])
        offset += header + size
    assert offset == len(data)
    return parts


def _der_content(data: bytes) -> bytes:
    return data[2 + (data[1] & 127) if data[1] & 128 else 2 :]


def _der_wrap(tag: int, value: bytes) -> bytes:
    size = len(value)
    if size < 128:
        length = bytes([size])
    else:
        width = (size.bit_length() + 7) // 8
        length = bytes([128 + width]) + size.to_bytes(width, "big")
    return bytes([tag]) + length + value


def _der_sequence(parts: list[bytes]) -> bytes:
    return _der_wrap(0x30, b"".join(parts))


def _with_extension(
    data: bytes,
    issuer: c.SigningCredential,
    key: ec.EllipticCurvePrivateKey,
    value: x509.ExtensionType,
    critical: bool = False,
) -> bytes:
    cert = c.parse_certificate(data)
    builder = (
        x509.CertificateBuilder()
        .subject_name(cert.subject)
        .issuer_name(cert.issuer)
        .public_key(key.public_key())
        .serial_number(cert.serial_number)
        .not_valid_before(cert.not_valid_before_utc)
        .not_valid_after(cert.not_valid_after_utc)
    )
    for extension in cert.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    return (
        builder.add_extension(value, critical)
        .sign(issuer.private_key, hashes.SHA256())
        .public_bytes(serialization.Encoding.DER)
    )


def _replace_last_extension_value(
    data: bytes, issuer: c.SigningCredential, value: bytes
) -> bytes:
    def replace(tbs):
        wrapper = _split_der(_der_content(tbs[7]))
        extensions = _split_der(_der_content(wrapper[0]))
        parts = _split_der(_der_content(extensions[-1]))
        parts[-1] = _der_wrap(0x04, value)
        extensions[-1] = _der_sequence(parts)
        tbs[7] = _der_wrap(0xA3, _der_sequence(extensions))

    der = _mutate_tbs(data, issuer, replace)
    x509.load_der_x509_certificate(der)
    return der


def _mutate_tbs(data: bytes, issuer: c.SigningCredential, mutate) -> bytes:
    outer = _split_der(data)
    certificate = _split_der(_der_content(outer[0]))
    tbs = _split_der(_der_content(certificate[0]))
    mutate(tbs)
    return _resign(issuer, certificate, _der_sequence(tbs))


def _set_san(tbs, critical: bool | None) -> None:
    wrapper = _split_der(_der_content(tbs[7]))
    extensions = _split_der(_der_content(wrapper[0]))
    if critical is None:
        del extensions[2]
    else:
        parts = _split_der(_der_content(extensions[2]))
        if critical:
            parts[1] = _der_wrap(0x01, b"\xff")
        else:
            del parts[1]
        extensions[2] = _der_sequence(parts)
    tbs[7] = _der_wrap(0xA3, _der_sequence(extensions))


def _set_zero_lifetime(tbs) -> None:
    validity = _split_der(_der_content(tbs[4]))
    tbs[4] = _der_sequence([validity[0], validity[0]])


def _resign(root: c.SigningCredential, parts: list[bytes], tbs: bytes) -> bytes:
    signature = root.private_key.sign(tbs, ec.ECDSA(hashes.SHA256()))
    bits = b"\x03" + bytes([len(signature) + 1]) + b"\x00" + signature
    return _der_sequence([tbs, parts[1], bits])


def _raw_extension(oid: str, value: bytes) -> x509.UnrecognizedExtension:
    return x509.UnrecognizedExtension(ObjectIdentifier(oid), value)


def _profile_variant(data, issuer, change):
    parts = _split_der(_der_content(data))
    tbs = _split_der(_der_content(parts[0]))
    if change in {"inner-null", "both-null"}:
        tbs[2] = _der_sequence([*_split_der(_der_content(tbs[2])), b"\x05\x00"])
    if change in {"outer-null", "both-null"}:
        parts[1] = _der_sequence([*_split_der(_der_content(parts[1])), b"\x05\x00"])
    if change in {"negative-serial", "zero-serial"}:
        tbs[1] = b"\x02\x01" + (b"\xff" if change == "negative-serial" else b"\x00")
    if change in {"issuer-unique-id", "subject-unique-id"}:
        tag = 0x81 if change == "issuer-unique-id" else 0x82
        tbs.insert(7, bytes([tag, 2, 0, 128]))
    der = _resign(issuer, parts, _der_sequence(tbs))
    parsed = x509.load_der_x509_certificate(der)
    list(parsed.extensions)
    issuer.private_key.public_key().verify(
        parsed.signature, parsed.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256())
    )
    return der


@pytest.mark.filterwarnings("ignore::cryptography.utils.CryptographyDeprecationWarning")
def test_fresh_python_credentials_in_go_peer(tmp_path: Path) -> None:
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    owner_id, server_id, agent_id, enrollment_id = (str(uuid4()) for _ in range(4))
    owner = c.create_issuer(c.Identity(c.Role.OWNER, owner_id), now=now)
    server = c.create_issuer(c.Identity(c.Role.SERVER_AUTHORITY, server_id), now=now)
    certs = {"owner": owner.certificate_der, "server-authority": server.certificate_der}
    keys = {}
    cases = []
    for credential, identity, issuer in (
        ("owner", owner.identity, None),
        ("server-authority", server.identity, None),
        ("portable", c.Identity(c.Role.PORTABLE, agent_id), owner),
        (
            "access",
            c.Identity(c.Role.ACCESS, agent_id, server_id, enrollment_id),
            server,
        ),
        ("server", c.Identity(c.Role.SERVER_TLS, server_id), server),
    ):
        if issuer is not None:
            key = ec.generate_private_key(ec.SECP256R1())
            keys[credential] = key
            certs[credential] = c.issue_certificate(
                identity,
                key.public_key(),
                issuer,
                now=now,
            )
        ids = [uri.rsplit(":", 1)[1] for uri in identity.uris()]
        case = {
            "name": credential,
            "certificate": credential,
            "role": identity.role.value,
            "ids": ids,
            "valid": True,
        }
        if issuer is not None:
            case.update(
                issuer=issuer.identity.role.value, issuer_id=issuer.identity.subject_id
            )
        cases.append(case)
    access = next(case for case in cases if case["name"] == "access")
    for original in cases[:5]:
        base = original["name"]
        signer = owner if base in {"owner", "portable"} else server
        for change in (
            "inner-null",
            "outer-null",
            "both-null",
            "negative-serial",
            "zero-serial",
            "issuer-unique-id",
            "subject-unique-id",
        ):
            name = f"{base}-{change}"
            certs[name] = _profile_variant(certs[base], signer, change)
            cases.append(
                {
                    **original,
                    "name": name,
                    "certificate": name,
                    "valid": change == "zero-serial",
                }
            )

    def add_case(name, certificate):
        valid = name.startswith("valid-")
        certs[name] = certificate
        cases.append({**access, "name": name, "certificate": name, "valid": valid})

    for name, extension in (
        ("valid-ski", x509.SubjectKeyIdentifier(b"\xaa")),
        ("empty-ski", x509.SubjectKeyIdentifier(b"")),
        ("valid-aki", x509.AuthorityKeyIdentifier(b"\xaa", None, None)),
        ("unsupported-admissions", _raw_extension("1.3.36.8.3.3", b"\x30\x02\x30\x00")),
        (
            "high-tag-unknown",
            _raw_extension("1.2.3.4", bytes.fromhex("9f888080800000")),
        ),
        ("valid-max-tag", _raw_extension("1.2.3.4", bytes.fromhex("9f87ffffff7f00"))),
        ("valid-unknown", _raw_extension("1.2.3.4", b"\x05\x00")),
        (
            "unsupported-issuer-alt-name",
            x509.IssuerAlternativeName([x509.DNSName("optional.example")]),
        ),
        ("trailing-unknown", _raw_extension("1.2.3.4", b"\x05\x00\x05\x00")),
        ("malformed-constructed", _raw_extension("1.2.3.4", bytes.fromhex("3001ff"))),
        ("nested-constructed", _raw_extension("1.2.3.4", bytes.fromhex("30033001ff"))),
        ("truncated-child", _raw_extension("1.2.3.4", bytes.fromhex("30023002"))),
        (
            "parent-overrun",
            _raw_extension("1.2.3.4", bytes.fromhex("3006300204020500")),
        ),
        ("trailing-child", _raw_extension("1.2.3.4", bytes.fromhex("30030500ff"))),
        ("valid-nested", _raw_extension("1.2.3.4", bytes.fromhex("30023000"))),
    ):
        add_case(
            name, _with_extension(certs["access"], server, keys["access"], extension)
        )
    deep = b"\x05\x00"
    for _ in range(500):
        deep = _der_wrap(0x30, deep)
    add_case(
        "valid-deep",
        _with_extension(
            certs["access"], server, keys["access"], _raw_extension("1.2.3.4", deep)
        ),
    )
    add_case(
        "critical-unknown",
        _with_extension(
            certs["access"],
            server,
            keys["access"],
            _raw_extension("1.2.3.5", b"\x05\x00"),
            True,
        ),
    )
    for name, base, value in (
        ("valid-ski-wrapper", "valid-ski", b"\x04\x01\xaa"),
        ("valid-aki-wrapper", "valid-aki", b"\x30\x03\x80\x01\xaa"),
        ("trailing-ski", "valid-ski", b"\x04\x01\xaa\x05\x00"),
        ("nested-trailing-aki", "valid-aki", b"\x30\x05\x80\x01\xaa\x05\x00"),
        ("outer-trailing-aki", "valid-aki", b"\x30\x03\x80\x01\xaa\x05\x00"),
    ):
        add_case(name, _replace_last_extension_value(certs[base], server, value))
    for name, mutate in (
        ("valid-san-wrapper", lambda tbs: _set_san(tbs, True)),
        ("noncritical-san", lambda tbs: _set_san(tbs, False)),
        ("missing-san", lambda tbs: _set_san(tbs, None)),
        ("zero-lifetime", _set_zero_lifetime),
        ("wrong-issuer-name", lambda tbs: tbs.__setitem__(3, tbs[5])),
    ):
        add_case(name, _mutate_tbs(certs["access"], server, mutate))
        if name in {"noncritical-san", "missing-san"}:
            list(x509.load_der_x509_certificate(certs[name]).extensions)
    access_identity = c.Identity(c.Role.ACCESS, agent_id, server_id, enrollment_id)
    same = c._build(
        access_identity,
        server.private_key.public_key(),
        server.private_key,
        c.parse_certificate(server.certificate_der),
        now,
    )

    def trailing_spki(tbs):
        spki = _split_der(_der_content(tbs[6]))
        spki[0] = _der_sequence([*_split_der(_der_content(spki[0])), b"\x05\x00"])
        tbs[6] = _der_sequence(spki)

    malformed_same = _mutate_tbs(same, server, trailing_spki)
    malformed_tbs = _mutate_tbs(
        certs["access"], server, lambda tbs: tbs.append(b"\x05\x00")
    )
    certs.update(
        {
            "same-key": same,
            "same-key-trailing-SPKI-AI": malformed_same,
            "trailing-TBSCertificate-field": malformed_tbs,
        }
    )
    cases.extend(
        {
            **access,
            "name": certificate,
            "certificate": certificate,
            "valid": False,
        }
        for certificate in (
            "same-key",
            "same-key-trailing-SPKI-AI",
            "trailing-TBSCertificate-field",
        )
    )
    cases.extend(
        {
            **access,
            "name": f"reject-{credential}-as-access",
            "certificate": credential,
            "valid": False,
        }
        for credential in ("owner", "server-authority", "portable", "server")
    )
    cases.extend(
        [
            {
                **access,
                "name": "reject-owner-as-server-issuer",
                "issuer": "owner",
                "valid": False,
            },
            {
                **access,
                "name": "reject-missing-trust",
                "issuer": "",
                "valid": False,
            },
            {
                **access,
                "name": "reject-wrong-enrollment",
                "ids": [agent_id, server_id, str(uuid4())],
                "valid": False,
            },
        ]
    )
    for case in cases:
        ids = case["ids"]
        assert isinstance(ids, list)
        identity = c.Identity(c.Role(str(case["role"])), str(ids[0]), *ids[1:])
        issuer_der = certs[str(case["issuer"])] if case.get("issuer") else None
        issuer_id = str(case["issuer_id"]) if case.get("issuer") else None
        with nullcontext() if case["valid"] else pytest.raises(c.CredentialError):
            c.verify_certificate(
                certs[str(case["certificate"])],
                identity,
                now=now,
                issuer_der=issuer_der,
                issuer_id=issuer_id,
            )
    peer = tmp_path / "public-credential-cases.json"
    peer.write_text(
        json.dumps(
            {
                "now": now.isoformat(),
                "certificates": {
                    k: base64.b64encode(v).decode("ascii") for k, v in certs.items()
                },
                "cases": cases,
            }
        )
    )
    result = subprocess.run(
        [
            "go",
            "test",
            "-count=1",
            "-run",
            "^TestPythonCredentialPeer$",
            "-v",
            "./internal/identity",
        ],
        cwd=Path(__file__).resolve().parents[3] / "client" / "go-cli",
        env={
            **os.environ,
            "ARBITER_TEST_CREDENTIAL_PEER": str(peer),
            "GODEBUG": "x509negativeserial=1",
        },
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"verified {len(cases)} live Python credential cases" in result.stdout
