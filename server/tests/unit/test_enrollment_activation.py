"""Activation is one durable result under the live ready/approval owner."""

import asyncio
from contextlib import closing
from dataclasses import replace
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
from starlette.applications import Starlette

from arbiter_server.authority import (
    AuthorityBusy,
    initialize_authority,
    running_instance,
)
from arbiter_server.config import AppConfig
from arbiter_server.enrollment_activation import activate
from arbiter_server.enrollment_receipt import result_fields, retrieve
from arbiter_server.generations import ACTIVE, _control, _database
from arbiter_server.main import build_server
from arbiter_server.pairing import prologue
from arbiter_server.pairing_identity import load_pairing_identity
from arbiter_server.pairing_proofs import _digest, _signature
from arbiter_server.prepared import READY_TAG, prepare, ready_context
from server.tests.unit.test_enrollment_receipt import request_for
from server.tests.unit.test_pairing import connected
from server.tests.unit.test_pairing_proofs import client_proof, proof_setup
from server.tests.unit.test_prepared import session_fixture


def database(root):
    active = _control(root / ACTIVE)
    assert active is not None
    return _database(root, active["active"])


@pytest.mark.parametrize(
    "failure", [None, "account", "check", "binding", "expired", "lock"]
)
def test_transaction_and_exact_idempotence(
    tmp_path, private_windows_test_parent, failure
):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    requests, approval, keys, _, _, now = proof_setup()
    approval = replace(approval, grants=(("imap", "personal"),))
    raw = prepare(root, approval, int(now.timestamp()) + 300)
    if failure == "binding":
        raw = raw[:-1] + b"x"
    if failure == "expired":
        raw = prepare(root, approval, int(now.timestamp()) + 1)
    path = database(root)
    blocker = sqlite3.connect(path, timeout=0)
    if failure == "lock":
        blocker.execute("BEGIN IMMEDIATE")
    checks = []

    def check():
        checks.append(True)
        if failure == "check":
            raise ValueError("synthetic deadline before commit")

    try:

        def commit():
            return activate(
                root,
                approval,
                raw,
                lambda: set() if failure == "account" else {("imap", "personal")},
                check,
                now.replace(year=now.year + 1) if failure == "expired" else now,
            )

        if failure:
            expected: tuple[type[Exception], ...] = (
                ValueError,
                sqlite3.OperationalError,
            )
            with pytest.raises(expected):
                commit()
            with closing(sqlite3.connect(path)) as db:
                assert (
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name='enrollments'"
                    ).fetchone()
                    is None
                )
        else:
            assert commit() == raw
            before = path.read_bytes()
            assert commit() == raw
            assert path.read_bytes() == before
            ids, fields = result_fields(raw)
            with closing(sqlite3.connect(path)) as db:
                row = db.execute("SELECT * FROM enrollments").fetchall()
                assert len(row) == 1 and bytes(row[0][1]) == raw
                assert row[0][3] == int(now.timestamp()) + 180 * 86400
                assert json.loads(fields[8]) == [["imap", "personal"]]
                db.execute("UPDATE enrollments SET denied=1")
                db.commit()
            with pytest.raises(ValueError):
                commit()
            with pytest.raises(ValueError):
                exporter = os.urandom(32)
                retrieve(root, request_for(raw, exporter, keys[0]), exporter, now)
    finally:
        blocker.close()
        requests.reset()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "not-ready",
        "reject",
        "reset",
        "expiry",
        "shutdown",
        "account-reset",
        "account-expiry",
        "account-shutdown",
        "account-reject",
    ],
)
def test_ready_commit_and_pending_retirement(
    tmp_path, private_windows_test_parent, failure
):
    async def run():
        root, requests, responder, lease, session, peer, keys = await session_fixture(
            tmp_path
        )
        try:
            raw = await responder.prepare(session, root)
            if failure != "not-ready":
                context = ready_context(
                    session.handshake_hash,
                    prologue(session.approval.request.advertisement),
                    raw,
                )
                await responder.ready(
                    session,
                    READY_TAG
                    + hashlib.sha256(raw).digest()
                    + _signature(keys[0], _digest(context, b"ready")),
                )
            if failure == "reject":
                requests.exhaust_approval(session.approval)
            if failure == "reset":
                requests.reset()
            if failure == "expiry":
                requests._clock = lambda: session.approval.request.deadline
            if failure == "shutdown":
                responder.close()

            def accounts():
                if failure == "account-reset":
                    requests.reset()
                if failure == "account-expiry":
                    requests._clock = lambda: session.approval.request.deadline
                if failure == "account-shutdown":
                    responder.close()
                if failure == "account-reject":
                    requests.control(
                        "reject",
                        dict(
                            selector=session.approval.request.advertisement.selector,
                            revision=session.approval.request.revision,
                            request_digest=session.approval.request.advertisement.digest,
                        ),
                        set(),
                    )
                return set()

            if failure:
                with pytest.raises(ValueError):
                    await responder.activate(session, root, accounts)
                with closing(sqlite3.connect(database(root))) as db:
                    assert (
                        db.execute(
                            "SELECT name FROM sqlite_master WHERE name='enrollments'"
                        ).fetchone()
                        is None
                    )
            else:
                assert peer.decrypt(await responder.activate(session, root, set)) == raw
                assert not requests.pending() and not responder._attempts
                assert (
                    requests.approved(session.approval.request.advertisement.selector)
                    is None
                )
                with pytest.raises(ValueError):
                    await responder.activate(session, root, set)
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("invalidate", [None, "reset", "expiry"])
def test_failed_authorized_write_restores_only_current_approval(
    tmp_path, private_windows_test_parent, monkeypatch, invalidate
):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    requests, approval, _, _, _, now = proof_setup()
    raw = prepare(root, approval, int(now.timestamp()) + 300)
    connect = sqlite3.connect

    class FailedCommit(sqlite3.Connection):
        def commit(self):
            assert not requests.approval_is_current(approval)
            if invalidate == "reset":
                requests.reset()
            if invalidate == "expiry":
                requests._clock = lambda: approval.request.deadline
            raise sqlite3.OperationalError("synthetic commit failure")

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: connect(*a, **k, factory=FailedCommit)
    )
    with pytest.raises(sqlite3.OperationalError, match="synthetic commit failure"):
        requests.complete(
            approval, lambda check: activate(root, approval, raw, set, check, now)
        )
    assert requests.approval_is_current(approval) == (invalidate is None)
    assert not requests._committing
    with closing(connect(database(root))) as db:
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='enrollments'"
            ).fetchone()
            is None
        )


def test_decided_commit_outlives_reset_without_blocking_admission(
    tmp_path, private_windows_test_parent, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    connect = sqlite3.connect

    class PausedCommit(sqlite3.Connection):
        def commit(self):
            entered.set()
            assert release.wait(4)
            super().commit()

    async def run():
        root, requests, responder, lease, session, peer, keys = await session_fixture(
            tmp_path
        )
        task = None
        try:
            raw = await responder.prepare(session, root)
            context = ready_context(
                session.handshake_hash,
                prologue(session.approval.request.advertisement),
                raw,
            )
            await responder.ready(
                session,
                READY_TAG
                + hashlib.sha256(raw).digest()
                + _signature(keys[0], _digest(context, b"ready")),
            )
            monkeypatch.setattr(
                sqlite3,
                "connect",
                lambda *a, **k: connect(*a, **k, factory=PausedCommit),
            )
            task = asyncio.create_task(responder.activate(session, root, set))
            assert await asyncio.to_thread(entered.wait, 2)
            assert not requests.approval_is_current(session.approval)
            assert requests.pending() == (session.approval.request,)
            with pytest.raises(ValueError, match="in progress"):
                requests.control(
                    "reject",
                    dict(
                        selector=session.approval.request.advertisement.selector,
                        revision=session.approval.request.revision,
                        request_digest=session.approval.request.advertisement.digest,
                    ),
                    set(),
                )
            extra = responder.reserve()
            extra.close()
            requests.reset()
            responder.close()
            assert not release.is_set() and lease._busy
            release.set()
            with pytest.raises(ValueError):
                await task
            with closing(connect(database(root))) as db:
                assert db.execute("SELECT prepared FROM enrollments").fetchall() == [
                    (raw,)
                ]
            assert not requests.pending() and not requests._committing
            assert not responder._leases
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            responder.close()

    watchdog = threading.Timer(3, release.set)
    watchdog.start()
    try:
        asyncio.run(run())
    finally:
        watchdog.cancel()


@pytest.mark.parametrize("phase", ["before-decision", "after-decision"])
def test_lifespan_holds_instance_lock_until_cancelled_writer_settles(
    tmp_path, private_windows_test_parent, phase, monkeypatch
):
    entered, release = threading.Event(), threading.Event()

    async def run(selected_phase: str):
        root = tmp_path / "authority"
        initialize_authority(root, recovery_file=tmp_path / "operator.json")
        cfg = AppConfig()
        cfg.arbiter.storage.server_data_dir = str(tmp_path)
        cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
        app = build_server(cfg, service_plugins=[], prepare_tls=False).app
        assert isinstance(app, Starlette)
        requests, responder = app.state.enrollment_requests, app.state.pairing_responder
        original_connect = sqlite3.connect
        task = shutdown = None
        entered_lifespan = False
        lifespan = app.router.lifespan_context(app)
        try:
            await lifespan.__aenter__()
            entered_lifespan = True
            _, fixture_approval, keys, _, _, now = proof_setup()
            advertisement = fixture_approval.request.advertisement
            payload = {
                "version": 1,
                "selector": advertisement.selector,
                "name": advertisement.name,
                **{
                    key: base64.b64encode(getattr(advertisement, key)).decode()
                    for key in ("access_spki", "portable_spki", "recipient_spki")
                },
            }
            requests.open_window()
            pending = requests.register(json.dumps(payload).encode())
            requests.control(
                "approve",
                {
                    "selector": pending.advertisement.selector,
                    "revision": pending.revision,
                    "request_digest": pending.advertisement.digest,
                    "psk": base64.urlsafe_b64encode(fixture_approval.secret)
                    .decode()
                    .rstrip("="),
                    "grants": [],
                },
                set(),
            )
            approval = requests.approved(pending.advertisement.selector)
            assert approval is not None
            lease, session, peer, _ = await connected(responder, approval)
            identity = load_pairing_identity(root)
            assert identity is not None
            leaf, authority = identity
            peer.decrypt(await responder.prove_server(session, leaf, authority, now))
            await responder.prove_client(
                session, peer.encrypt(client_proof(keys, session.proof_context))
            )
            raw = await responder.prepare(session, root)
            context = ready_context(
                session.handshake_hash,
                prologue(session.approval.request.advertisement),
                raw,
            )
            await responder.ready(
                session,
                READY_TAG
                + hashlib.sha256(raw).digest()
                + _signature(keys[0], _digest(context, b"ready")),
            )
            if selected_phase == "before-decision":

                def accounts():
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test watchdog expired")
                    return set()

            else:

                class PausedCommit(sqlite3.Connection):
                    def commit(self):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test watchdog expired")
                        return super().commit()

                def paused_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
                    return original_connect(*args, **kwargs, factory=PausedCommit)

                monkeypatch.setattr(sqlite3, "connect", paused_connect)
                accounts = set
            task = asyncio.create_task(responder.activate(session, root, accounts))
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert requests.approval_is_current(approval) is (
                selected_phase == "before-decision"
            )

            shutdown_started, shutdown_release = asyncio.Event(), asyncio.Event()
            from arbiter_server.local_control import LocalControl

            original_local_close = LocalControl.close

            async def paused_close(control: LocalControl) -> None:
                shutdown_started.set()
                await asyncio.wait_for(shutdown_release.wait(), 5)
                await original_local_close(control)

            monkeypatch.setattr(LocalControl, "close", paused_close)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.wait_for(shutdown_started.wait(), 2)
            shutdown.cancel()
            shutdown_release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown

            def enter_second_instance():
                with running_instance(root):
                    return True

            second = asyncio.create_task(asyncio.to_thread(enter_second_instance))
            await asyncio.sleep(0.05)
            assert second.done() and lease._busy
            with pytest.raises(AuthorityBusy):
                await second
            release.set()
            for _ in range(500):
                if not lease._busy:
                    break
                await asyncio.sleep(0.01)
            assert not lease._busy and not responder._leases
            assert await asyncio.wait_for(asyncio.to_thread(enter_second_instance), 5)
            entered_lifespan = False
            monkeypatch.setattr(sqlite3, "connect", original_connect)
            with running_instance(root):
                with closing(original_connect(database(root))) as db:
                    tables = db.execute(
                        "SELECT name FROM sqlite_master WHERE name='enrollments'"
                    ).fetchall()
                    if selected_phase == "after-decision":
                        assert db.execute(
                            "SELECT prepared FROM enrollments"
                        ).fetchall() == [(raw,)]
                    else:
                        assert not tables
        finally:
            release.set()
            monkeypatch.setattr(sqlite3, "connect", original_connect)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if shutdown is not None:
                await asyncio.gather(shutdown, return_exceptions=True)
            elif entered_lifespan:
                await lifespan.__aexit__(None, None, None)
            responder.close()

    watchdog = threading.Timer(8, release.set)
    watchdog.start()
    try:
        asyncio.run(run(phase))
    finally:
        watchdog.cancel()


def test_lifespan_reentry_rejects_closed_responder(
    tmp_path, private_windows_test_parent
):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    cfg = AppConfig()
    cfg.arbiter.storage.server_data_dir = str(tmp_path)
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
    app = build_server(cfg, service_plugins=[], prepare_tls=False).app
    assert isinstance(app, Starlette)
    first = app.router.lifespan_context(app)
    second = app.router.lifespan_context(app)

    async def run():
        await first.__aenter__()
        await first.__aexit__(None, None, None)
        with pytest.raises(RuntimeError, match="closed"):
            await second.__aenter__()
        assert not (root / "control.sock").exists()
        with running_instance(root):
            pass

    asyncio.run(run())


@pytest.mark.parametrize("shutdown", [False, True])
def test_reject_during_slow_accounts_keeps_admission_and_worker_owned(
    tmp_path, private_windows_test_parent, shutdown
):
    entered, release = threading.Event(), threading.Event()

    def accounts():
        entered.set()
        assert release.wait(4)
        return set()

    async def run():
        root, requests, responder, lease, session, peer, keys = await session_fixture(
            tmp_path
        )
        task = None
        try:
            raw = await responder.prepare(session, root)
            context = ready_context(
                session.handshake_hash,
                prologue(session.approval.request.advertisement),
                raw,
            )
            await responder.ready(
                session,
                READY_TAG
                + hashlib.sha256(raw).digest()
                + _signature(keys[0], _digest(context, b"ready")),
            )
            task = asyncio.create_task(responder.activate(session, root, accounts))
            assert await asyncio.to_thread(entered.wait, 2)
            started = time.monotonic()
            extra = responder.reserve()
            extra.close()
            assert time.monotonic() - started < 0.5 and not release.is_set()
            result = requests.control(
                "reject",
                dict(
                    selector=session.approval.request.advertisement.selector,
                    revision=session.approval.request.revision,
                    request_digest=session.approval.request.advertisement.digest,
                ),
                set(),
            )
            assert result["rejected"] == session.approval.request.advertisement.selector
            if shutdown:
                requests.reset()
                responder.close()
            assert lease in responder._leases and lease._busy
            release.set()
            with pytest.raises(ValueError):
                await task
            assert not responder._leases and not requests._committing
            with closing(sqlite3.connect(database(root))) as db:
                assert (
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name='enrollments'"
                    ).fetchone()
                    is None
                )
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            responder.close()

    watchdog = threading.Timer(3, release.set)
    watchdog.start()
    try:
        asyncio.run(run())
    finally:
        watchdog.cancel()


@pytest.mark.parametrize("phase", ["before-commit", "after-commit"])
def test_real_process_crash_and_restart(tmp_path, private_windows_test_parent, phase):
    root = tmp_path / "authority"
    initialize_authority(root, recovery_file=tmp_path / "operator.json")
    _, approval, keys, _, _, now = proof_setup()
    raw = prepare(root, approval, int(now.timestamp()) + 300)
    ad = approval.request.advertisement
    data = dict(
        root=str(root),
        phase=phase,
        raw=base64.b64encode(raw).decode(),
        advertisement=dict(
            selector=ad.selector,
            name=ad.name,
            digest=ad.digest,
            **{
                k: base64.b64encode(getattr(ad, k)).decode()
                for k in ("access_spki", "portable_spki", "recipient_spki")
            },
        ),
        revision=approval.request.revision,
    )
    # No keys or PSK in argv/env. Exit without finally/close/rollback. A one-page
    # cache forces actual dirty-page spill, not merely a zero-header journal.
    script = """
import sys,json,base64,os,time,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from arbiter_server.authority import running_instance
from arbiter_server.enrollment import Advertisement, PendingRequest, ApprovedRequest
from arbiter_server.enrollment_activation import activate
d=json.load(sys.stdin)
ad=d['advertisement']
for k in ('access_spki','portable_spki','recipient_spki'): ad[k]=base64.b64decode(ad[k])
a=ApprovedRequest(PendingRequest(Advertisement(**ad),d['revision'],time.monotonic(),time.monotonic()+300),(),b'')
original=sqlite3.connect
def connect(*args,**kwargs):
    db=original(*args,**kwargs); db.execute('PRAGMA cache_size=1'); return db
with running_instance(Path(d['root'])):
    sqlite3.connect=connect
    def check():
        if d['phase']=='before-commit': os._exit(61)
    activate(Path(d['root']),a,base64.b64decode(d['raw']),set,check,datetime.now(timezone.utc))
    os._exit(62)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    assert result.returncode == (61 if phase == "before-commit" else 62), result.stderr
    if phase == "before-commit":
        journal = database(root).with_name(database(root).name + "-journal")
        assert journal.exists() and journal.read_bytes()[:8] != bytes(8)
    with running_instance(root):
        with closing(sqlite3.connect(database(root))) as db:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE name='enrollments'"
            ).fetchall()
            assert bool(tables) == (phase == "after-commit")
            if tables:
                assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone() == (1,)
        if phase == "after-commit":
            exporter = os.urandom(32)
            assert (
                retrieve(
                    root,
                    request_for(raw, exporter, keys[0]),
                    exporter,
                    datetime.now(timezone.utc),
                )
                == raw
            )
