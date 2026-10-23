"""Possession proofs use the live bounded Noise owner and exact approved keys."""

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import threading
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
import pytest

from arbiter_server import pairing as p
from arbiter_server.credentials import (
    Identity,
    Role,
    SigningCredential,
    create_issuer,
    issue_certificate,
)
from arbiter_server.enrollment import EnrollmentRequests
from arbiter_server.pairing_proofs import CLIENT_TAG
from server.tests.unit.test_enrollment_requests import advertisement
from server.tests.unit.test_pairing import connected


def proof_setup():
    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(2)]
    data = advertisement()
    for name, key in zip(("access_spki", "portable_spki"), keys):
        data[name] = base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()
    requests = EnrollmentRequests()
    requests.open_window()
    pending = requests.register(json.dumps(data).encode())
    requests.control(
        "approve",
        dict(
            selector=pending.advertisement.selector,
            revision=pending.revision,
            request_digest=pending.advertisement.digest,
            psk=base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("="),
            grants=[],
        ),
        set(),
    )
    context = requests.approved(pending.advertisement.selector)
    assert context is not None
    now = datetime.now(timezone.utc)
    root = create_issuer(Identity(Role.SERVER_AUTHORITY, str(uuid4())), now=now)
    key = ec.generate_private_key(ec.SECP256R1())
    identity = Identity(Role.SERVER_TLS, root.identity.subject_id)
    leaf = SigningCredential(
        identity, issue_certificate(identity, key.public_key(), root, now=now), key
    )
    return requests, context, keys, root, leaf, now


def client_proof(keys, context):
    result = CLIENT_TAG
    for key, role in zip(keys, (b"access", b"portable")):
        digest = hashlib.sha256(context + b"\0" + role).digest()
        r, s = utils.decode_dss_signature(
            key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        )
        result += r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return result


@pytest.mark.parametrize(
    "invalid",
    [None, "key", "swap", "context", "role", "signature", "extra", "truncated"],
)
def test_live_mutual_proof_acceptance_and_terminal_failure(invalid, monkeypatch):
    requests, context, keys, root, leaf, now = proof_setup()
    real_server, real_client = p.server_proof, p.verify_client_proof
    threads = []

    def server(*args):
        threads.append(threading.get_ident())
        return real_server(*args)

    def client(*args):
        threads.append(threading.get_ident())
        return real_client(*args)

    monkeypatch.setattr(p, "server_proof", server)
    monkeypatch.setattr(p, "verify_client_proof", client)

    async def run():
        responder = p.PairingResponder(requests)
        try:
            lease, session, peer, _ = await connected(responder, context)
            wire = await responder.prove_server(
                session, leaf, root.certificate_der, now
            )
            assert peer.decrypt(wire).startswith(b"arbiter-pairing-server-v1\0")
            assert not session.proof_complete
            proof_context = session.proof_context
            signing = list(keys)
            if invalid == "key":
                signing[0] = ec.generate_private_key(ec.SECP256R1())
            if invalid == "swap":
                signing.reverse()
            if invalid == "context":
                proof_context = bytes(32)
            proof = client_proof(signing, proof_context)
            if invalid == "role":
                proof = b"X" + proof[1:]
            if invalid == "signature":
                proof = proof[:-1] + bytes([proof[-1] ^ 1])
            if invalid == "extra":
                proof += b"x"
            if invalid == "truncated":
                proof = proof[:-1]
            if invalid:
                with pytest.raises(ValueError):
                    await responder.prove_client(session, peer.encrypt(proof))
                assert not session.proof_complete and lease._closed
                with pytest.raises(ValueError):
                    await responder.message(session, b"later", encrypt=True)
            else:
                await responder.prove_client(session, peer.encrypt(proof))
                assert session.proof_complete
                with pytest.raises(ValueError):
                    await responder.prove_client(session, peer.encrypt(proof))
                assert lease._closed
            assert len(threads) == 2 and all(
                t != threading.get_ident() for t in threads
            )
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid", ["early-client", "server-repeat", "wrong-key", "wrong-role", "expired"]
)
def test_proof_order_credentials_and_expiry_fail_closed(invalid):
    requests, context, keys, root, leaf, now = proof_setup()

    async def run():
        responder = p.PairingResponder(requests)
        try:
            lease, session, peer, _ = await connected(responder, context)
            if invalid == "early-client":
                with pytest.raises(ValueError):
                    await responder.prove_client(session, peer.encrypt(b"not approved"))
            else:
                credential = leaf
                if invalid == "wrong-key":
                    credential = SigningCredential(
                        leaf.identity, leaf.certificate_der, keys[0]
                    )
                if invalid == "wrong-role":
                    credential = root
                if invalid == "expired":
                    lease.deadline = 0
                if invalid == "server-repeat":
                    await responder.prove_server(
                        session, leaf, root.certificate_der, now
                    )
                with pytest.raises(ValueError):
                    await responder.prove_server(
                        session, credential, root.certificate_der, now
                    )
            assert lease._closed and not session.proof_complete
        finally:
            responder.close()

    asyncio.run(run())
