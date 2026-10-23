"""Prepared results stay bounded, approval-bound and uncommitted."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import threading

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
