"""Prepared results stay bounded, approval-bound and uncommitted."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import threading
from typing import Any, cast

import pytest
from cryptography.exceptions import InvalidSignature

from arbiter_server.authority import initialize_authority
from arbiter_server.generations import ACTIVE, _control, _database
from arbiter_server.pairing import PairingResponder, prologue
from arbiter_server.pairing_identity import load_pairing_identity
from arbiter_server.pairing_proofs import _digest, _signature
from arbiter_server.prepared import PREPARED_TAG, READY_TAG, ready_context
from server.tests.unit.test_pairing import connected
from server.tests.unit.test_pairing_proofs import client_proof, proof_setup


async def session_fixture(tmp_path):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    requests, approval, keys, _, _, now = proof_setup()
    responder = PairingResponder(requests)
    lease, session, peer, _ = await connected(responder, approval)
    identity = load_pairing_identity(root)
    assert identity is not None
    leaf, authority = identity
    peer.decrypt(await responder.prove_server(session, leaf, authority, now))
    await responder.prove_client(
        session, peer.encrypt(client_proof(keys, session.proof_context))
    )
    return root, requests, responder, lease, session, peer, keys


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "signature",
        "result",
        "context",
        "role",
        "replay",
        "reject",
        "reset",
        "expiry",
    ],
)
def test_prepare_and_ready_never_activate(
    tmp_path, private_windows_test_parent, invalid
):
    async def run():
        root, requests, responder, lease, session, peer, keys = await session_fixture(
            tmp_path
        )
        active = _control(root / ACTIVE)
        assert active is not None
        path = _database(root, active["active"])
        before = path.read_bytes()
        try:
            raw = await responder.prepare(session, root)
            assert raw.startswith(PREPARED_TAG)
            assert await responder.prepare(session, root) == raw
            assert len(responder._attempts) == 1 and len(raw) < 65519
            context = ready_context(
                session.handshake_hash,
                prologue(session.approval.request.advertisement),
                raw,
            )
            if invalid == "context":
                context = bytes(32)
            signature = _signature(keys[0], _digest(context, b"ready"))
            proof = READY_TAG + hashlib.sha256(raw).digest() + signature
            if invalid == "signature":
                proof = proof[:-1] + bytes([proof[-1] ^ 1])
            if invalid == "result":
                proof = READY_TAG + bytes(32) + signature
            if invalid == "role":
                proof = b"X" + proof[1:]
            if invalid in {"reject", "reset", "expiry"}:
                if invalid == "reject":
                    requests.exhaust_approval(session.approval)
                elif invalid == "reset":
                    requests.reset()
                else:
                    lease.deadline = 0
            if invalid == "replay":
                await responder.ready(session, proof)
            if invalid:
                expected_errors: tuple[type[Exception], ...] = (
                    ValueError,
                    InvalidSignature,
                )
                with pytest.raises(expected_errors):
                    await responder.ready(session, proof)
                assert lease._closed
            else:
                await responder.ready(session, proof)
                assert session.ready_complete
                assert requests.approval_is_current(session.approval)
            # Even a valid ready proof has no activation table, accepted key or
            # grant write. The complete durable authority database is identical.
            assert path.read_bytes() == before
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT COUNT(*) FROM credentials").fetchone() == (3,)
                assert (
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name='enrollments'"
                    ).fetchone()
                    is None
                )
        finally:
            responder.close()

    asyncio.run(run())


def test_cancelled_preparation_retains_worker_and_drops_result(
    tmp_path, private_windows_test_parent, monkeypatch
):
    from arbiter_server import prepared

    entered, finish = threading.Event(), threading.Event()
    original = prepared.prepare
    worker_finished = threading.Event()

    def slow(*args):
        entered.set()
        assert finish.wait(10)
        try:
            return original(*args)
        finally:
            worker_finished.set()

    monkeypatch.setattr(prepared, "prepare", slow)

    async def run():
        root, requests, responder, lease, session, _, _ = await session_fixture(
            tmp_path
        )
        task = asyncio.create_task(responder.prepare(session, root))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lease._busy and lease in responder._leases
            assert not responder._attempts[
                session.approval.request.advertisement.selector
            ].prepared
            requests.reset()
            finish.set()
            assert await asyncio.to_thread(worker_finished.wait, 5)
            for _ in range(100):
                if lease not in responder._leases:
                    break
                await asyncio.sleep(0.01)
            assert lease not in responder._leases
            assert not responder._attempts or all(
                not s.prepared for s in responder._attempts.values()
            )
        finally:
            finish.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            responder.close()

    asyncio.run(run())


def test_preparation_keeps_original_deadline(
    tmp_path, private_windows_test_parent, monkeypatch
):
    from arbiter_server import pairing, prepared

    async def run():
        root, requests, responder, lease, session, _, keys = await session_fixture(
            tmp_path
        )
        elapsed = [0.0]
        wall_start = datetime.now(timezone.utc)

        class Clock:
            @staticmethod
            def now(tz):
                return wall_start + timedelta(seconds=elapsed[0])

        requests._clock = lambda: session.approval.request.created_at + elapsed[0]
        monkeypatch.setattr(pairing, "datetime", Clock)
        monkeypatch.setattr(prepared, "datetime", Clock)
        read = prepared._read_record

        def delayed_read(path):
            elapsed[0] += 9.75
            return read(path)

        monkeypatch.setattr(prepared, "_read_record", delayed_read)
        try:
            raw = await responder.prepare(session, root)
            offset = len(PREPARED_TAG) + 144 + 22 + 32 + 32
            assert int.from_bytes(raw[offset : offset + 8], "big") == int(
                wall_start.timestamp() + 600
            )
            assert await responder.prepare(session, root) == raw
            context = ready_context(
                session.handshake_hash,
                prologue(session.approval.request.advertisement),
                raw,
            )
            proof = READY_TAG + hashlib.sha256(raw).digest()
            proof += _signature(keys[0], _digest(context, b"ready"))
            elapsed[0] = 600
            with pytest.raises(ValueError):
                await responder.ready(session, proof)
            assert lease._closed and not session.ready_complete
        finally:
            responder.close()

    asyncio.run(run())


def portable_approval(tmp_path):
    """Real fresh-v1 activation at A, then an independently approved B request."""
    import base64
    import json
    from arbiter_server.enrollment import EnrollmentRequests
    from arbiter_server.enrollment_activation import activate
    from arbiter_server.enrollment_receipt import result_fields
    from arbiter_server.owner_issuers import control
    from arbiter_server.prepared import prepare
    from server.tests.unit.test_enrollment_requests import advertisement

    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        initialize_authority(
            root,
            recovery_file=tmp_path / "operator.json",
            reuse_recovery_file=root == b,
        )
    _, fresh, old_keys, _, _, now = proof_setup()
    raw = prepare(a, fresh, int(now.timestamp()) + 300)
    activate(a, fresh, raw, set, lambda: None, now)
    ids, fields = result_fields(raw)
    fingerprint = hashlib.sha256(fields[2]).hexdigest()
    control(b, "register", {"certificate_der": base64.b64encode(fields[2]).decode()})
    data = advertisement()
    _, fresh_b, keys, _, _, _ = proof_setup()
    keys[1] = old_keys[1]
    data.update(
        version=2,
        portable_identity=dict(
            owner_id=ids[1],
            agent_id=ids[2],
            issuer_sha256=fingerprint,
            certificate_der=base64.b64encode(fields[3]).decode(),
        ),
    )
    from cryptography.hazmat.primitives import serialization

    for name, key in zip(("access_spki", "portable_spki"), keys):
        data[name] = base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()
    requests = EnrollmentRequests()
    requests.open_window()
    pending = requests.register(json.dumps(data).encode(), version=2)
    arguments: dict[str, object] = dict(
        selector=pending.advertisement.selector,
        revision=pending.revision,
        request_digest=pending.advertisement.digest,
        psk=base64.urlsafe_b64encode(fresh_b.secret).decode().rstrip("="),
        grants=[],
    )
    requests.control("approve", arguments, set(), root=b)
    approval = requests.approved(pending.advertisement.selector)
    assert approval is not None
    return a, b, requests, approval, keys, now, raw, arguments


@pytest.mark.parametrize(
    "failure",
    [None, "unknown", "inactive", "re-added", "wrong-key", "expired", "binding"],
)
def test_portable_prepare_rechecks_exact_trust(
    tmp_path, private_windows_test_parent, failure, monkeypatch
):
    from dataclasses import replace
    from arbiter_server.enrollment_receipt import result_fields
    from arbiter_server.owner_issuers import control
    from arbiter_server.prepared import prepare
    from server.tests.unit.test_enrollment_activation import database

    _, root, requests, approval, _, now, source, arguments = portable_approval(tmp_path)
    claim = approval.request.advertisement.portable_identity
    assert claim is not None and approval.portable_issuer is not None
    if failure in {"unknown", "inactive", "re-added"}:
        with sqlite3.connect(database(root)) as db:
            if failure == "unknown":
                db.execute(
                    "DELETE FROM owner_issuers WHERE issuer_sha256=?",
                    (claim.issuer_sha256,),
                )
            else:
                db.execute(
                    "UPDATE owner_issuers SET active=0,revision=revision+1 WHERE issuer_sha256=?",
                    (claim.issuer_sha256,),
                )
            db.commit()
        if failure == "re-added":
            import base64

            control(
                root,
                "register",
                {
                    "certificate_der": base64.b64encode(
                        approval.portable_issuer[0]
                    ).decode()
                },
            )
    if failure == "wrong-key":
        approval = replace(
            approval,
            request=replace(
                approval.request,
                advertisement=replace(
                    approval.request.advertisement,
                    portable_spki=approval.request.advertisement.access_spki,
                ),
            ),
        )
    if failure == "expired":
        from arbiter_server import prepared

        class Clock:
            @staticmethod
            def now(tz):
                return now + timedelta(days=366)

        monkeypatch.setattr(prepared, "datetime", Clock)
    if failure == "binding":
        with sqlite3.connect(database(root)) as db:
            db.execute(
                "INSERT INTO agent_enrollments VALUES (?, ?)",
                (claim.agent_id, "occupied"),
            )
            db.commit()
    if failure:
        with pytest.raises(ValueError):
            prepare(root, approval, int(now.timestamp()) + 366 * 86400 + 300)
        return
    raw = prepare(root, approval, int(now.timestamp()) + 300)
    ids, fields = result_fields(raw)
    old_ids, old_fields = result_fields(source)
    assert ids[1:3] == old_ids[1:3] and ids[0] != old_ids[0] and ids[3] != old_ids[3]
    assert fields[2:4] == old_fields[2:4] and fields[4] != old_fields[4]
    assert cast(Any, requests.control("list", {}, set())["requests"])[0][
        "portable_identity"
    ] == dict(
        owner_id=ids[1],
        agent_id=ids[2],
        issuer_sha256=claim.issuer_sha256,
        certificate_validated=True,
        possession_proved=False,
    )
    assert (
        cast(Any, requests.control("approve", arguments, set(), root=root)["request"])[
            "status"
        ]
        == "approved"
    )
    with pytest.raises(ValueError):
        requests.control("approve", arguments, set())


def test_portable_approval_reset_during_issuer_validation(
    tmp_path, private_windows_test_parent, monkeypatch
):
    from arbiter_server import owner_issuers

    _, root, requests, _, _, _, _, arguments = portable_approval(tmp_path)
    original = owner_issuers.approve_portable

    def reset(*args):
        issuer = original(*args)
        requests.reset()
        return issuer

    monkeypatch.setattr(owner_issuers, "approve_portable", reset)
    with pytest.raises(ValueError, match="missing or expired"):
        requests.control("approve", arguments, set(), root=root)
    assert not requests.pending()
