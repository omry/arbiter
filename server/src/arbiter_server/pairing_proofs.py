"""Versioned signing-key proofs carried only inside the approved Noise channel.

The server header contains a 36-byte ID and two uint16-length DER certificates.
Signatures are standard ES256 raw r||s. Each signature binds a distinct role,
the Noise hash, exact advertisement prologue and complete server header.
Verification neither registers trust nor activates an enrollment.
"""

from datetime import datetime
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from .credentials import Role, SigningCredential, verify_certificate
from .enrollment import Advertisement

SERVER_TAG = b"arbiter-pairing-server-v1\0"
CLIENT_TAG = b"arbiter-pairing-client-v1\0"


def _digest(context: bytes, role: bytes) -> bytes:
    return hashlib.sha256(context + b"\0" + role).digest()


def _signature(key: ec.EllipticCurvePrivateKey, digest: bytes) -> bytes:
    r, s = utils.decode_dss_signature(
        key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    )
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def server_proof(
    handshake_hash: bytes,
    prologue: bytes,
    credential: SigningCredential,
    authority_der: bytes,
    now: datetime,
) -> tuple[bytes, bytes]:
    identity = credential.identity
    if identity.role is not Role.SERVER_TLS or len(handshake_hash) != 32:
        raise ValueError("invalid pairing proof context")
    cert = verify_certificate(
        credential.certificate_der,
        identity,
        issuer_der=authority_der,
        issuer_id=identity.subject_id,
        now=now,
    )
    key = credential.private_key
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("invalid pairing signing key")
    public = cert.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey) or (
        key.public_key().public_numbers() != public.public_numbers()
    ):
        raise ValueError("pairing certificate and signing key differ")
    header = (
        SERVER_TAG
        + identity.subject_id.encode("ascii")
        + len(authority_der).to_bytes(2, "big")
        + authority_der
        + len(credential.certificate_der).to_bytes(2, "big")
        + credential.certificate_der
    )
    context = hashlib.sha256(
        b"arbiter-enrollment-possession-v1\0"
        + handshake_hash
        + hashlib.sha256(prologue).digest()
        + hashlib.sha256(header).digest()
    ).digest()
    return header + _signature(key, _digest(context, b"server")), context


def verify_client_proof(
    data: bytes, context: bytes, advertisement: Advertisement
) -> None:
    if len(context) != 32 or len(data) != len(CLIENT_TAG) + 128:
        raise ValueError("invalid client possession proof")
    if not data.startswith(CLIENT_TAG):
        raise ValueError("invalid client possession role")
    signatures = data[len(CLIENT_TAG) :]
    for offset, role, spki in (
        (0, b"access", advertisement.access_spki),
        (64, b"portable", advertisement.portable_spki),
    ):
        key = serialization.load_der_public_key(spki)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("invalid proposed signing key")
        signature = signatures[offset : offset + 64]
        der = utils.encode_dss_signature(
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"),
        )
        try:
            key.verify(
                der, _digest(context, role), ec.ECDSA(utils.Prehashed(hashes.SHA256()))
            )
        except InvalidSignature:
            raise ValueError("invalid client possession proof") from None
