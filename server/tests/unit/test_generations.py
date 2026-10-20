from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, cast
from uuid import uuid4

import pytest

from arbiter_server import authority as a, generations as g
from arbiter_server.config import AppConfig
from arbiter_server.main import build_server
from server.tests.unit.test_native_http_protocol import _asgi_request

pytestmark = pytest.mark.usefixtures("private_windows_test_parent")


def initialize(tmp_path):
    return a.initialize_authority(
        tmp_path / "state", recovery_file=tmp_path / "operator.json"
    )


def test_generation_restart_preserves_complete_identity_and_trust(tmp_path):
    record = initialize(tmp_path)
    root = tmp_path / "state"
    active = root / g.ACTIVE
    selection = active.read_bytes()
    generation = json.loads(selection)["active"]
    directory = root / "generations" / generation
    assert set(p.name for p in directory.iterdir()) == {a.DATABASE}
    database = directory / a.DATABASE
    original = database.read_bytes()
    assert a.authority_status(root) == record
    with a.running_instance(root) as current:
        assert current == record
        with pytest.raises(a.AuthorityError, match="busy"):
            a.authority_status(root)
    assert database.read_bytes() == original and active.read_bytes() == selection
    database.unlink()
    with pytest.raises(OSError):
        a.authority_status(root)
    with pytest.raises(OSError):
        initialize(tmp_path)
    assert active.read_bytes() == selection


def test_stale_intent_cannot_roll_back_live_database_changes(tmp_path, monkeypatch):
    real = a._publish
    saved = []

    def retain_intent(source, target):
        real(source, target)
        if target.name == g._INTENT:
            saved.append(target.read_bytes())

    monkeypatch.setattr(a, "_publish", retain_intent)
    record = initialize(tmp_path)
    root = tmp_path / "state"
    database = (
        root
        / "generations"
        / json.loads((root / g.ACTIVE).read_bytes())["active"]
        / a.DATABASE
    )
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE synthetic_runtime(value TEXT)")
        db.execute("INSERT INTO synthetic_runtime VALUES ('preserve')")
        db.commit()
    before = database.read_bytes()
    intent = root / g._INTENT
    intent.write_bytes(saved[0])
    intent.chmod(0o600)
    assert a.authority_status(root) == record
    assert database.read_bytes() == before and not intent.exists()


@pytest.mark.parametrize("phase", ["intent", "generation", "active"])
def test_real_kill_reconciles_exact_durable_publication(tmp_path, phase):
    root, ready = tmp_path / "state", tmp_path / "ready"
    script = """
import sys, time
from pathlib import Path
from arbiter_server import authority as a, generations as g
root, ready, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
real = a._publish
def publish(source, target):
    real(source, target)
    if ((phase == "intent" and target.name == g._INTENT)
        or (phase == "generation" and source.name == g._STAGING)
        or (phase == "active" and target.name == g.ACTIVE)):
        ready.write_text("ready")
        time.sleep(60)
a._publish = publish
a.initialize_authority(root, recovery_file=root.parent / "operator.json")
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(ready), phase],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists(), "initializer did not reach durable publication boundary"
        paths = list((root / "generations").rglob(a.DATABASE))
        assert len(paths) == 1
        expected = a._read_record(paths[0])
        custody = (tmp_path / "operator.json").read_bytes()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
    assert a.authority_status(root) == expected
    assert a.authority_status(root) == expected
    assert (tmp_path / "operator.json").read_bytes() == custody
    assert not (root / g._INTENT).exists()
    assert not (root / g._CONTROL_PENDING).exists()
    assert len(list((root / "generations").iterdir())) == 1
    with pytest.raises(a.AuthorityError, match="already exists"):
        initialize(tmp_path)


@pytest.mark.parametrize(
    "damage", ["digest", "conflict", "unknown-staging", "extra-with-intent"]
)
def test_corrupt_publication_never_selects_or_deletes_unproved_state(
    tmp_path, monkeypatch, damage
):
    real = a._publish

    def stop_after_intent(source, target):
        real(source, target)
        if target.name == g._INTENT:
            raise OSError("injected after intent")

    with monkeypatch.context() as patch:
        patch.setattr(a, "_publish", stop_after_intent)
        with pytest.raises(OSError, match="injected"):
            initialize(tmp_path)
    root = tmp_path / "state"
    intent = root / g._INTENT
    staged = root / "generations" / g._STAGING
    before = (staged / a.DATABASE).read_bytes()
    if damage == "digest":
        record = json.loads(intent.read_bytes())
        record["sha256"] = "0" * 64
        intent.write_text(json.dumps(record))
    elif damage == "conflict":
        (root / g.ACTIVE).write_text(json.dumps({"version": 1, "active": str(uuid4())}))
        (root / g.ACTIVE).chmod(0o600)
    else:
        if damage == "unknown-staging":
            intent.unlink()
        (staged / "unexpected").write_bytes(b"keep")
    selection = (root / g.ACTIVE).read_bytes() if (root / g.ACTIVE).exists() else None
    with pytest.raises(a.AuthorityError):
        a.authority_status(root)
    assert (staged / a.DATABASE).read_bytes() == before
    assert (
        (root / g.ACTIVE).read_bytes() if (root / g.ACTIVE).exists() else None
    ) == selection


@pytest.mark.parametrize(
    "record",
    [
        b'{"version":1,"active":"../outside"}',
        b'{"version":true,"active":"00000000-0000-4000-8000-000000000001"}',
        b'{"version":1,"active":"x","active":"y"}',
        b'{"version":1,"active":null}',
        b"[" * 600,
    ],
)
def test_malformed_selector_is_not_repaired(tmp_path, record):
    initialize(tmp_path)
    path = tmp_path / "state" / g.ACTIVE
    path.write_bytes(record)
    with pytest.raises(a.AuthorityError):
        a.authority_status(path.parent)
    assert path.read_bytes() == record


def test_missing_selector_never_creates_a_replacement_identity(tmp_path):
    initialize(tmp_path)
    root = tmp_path / "state"
    selection = (root / g.ACTIVE).read_bytes()
    (root / g.ACTIVE).unlink()
    custody = (tmp_path / "operator.json").read_bytes()
    with pytest.raises(a.AuthorityError, match="no active selector"):
        a.authority_status(root)
    with pytest.raises(a.AuthorityError, match="no active selector"):
        initialize(tmp_path)
    assert len(list((root / "generations").iterdir())) == 1
    assert (tmp_path / "operator.json").read_bytes() == custody
    (root / g.ACTIVE).write_bytes(selection)
    (root / g.ACTIVE).chmod(0o600)
    assert a.authority_status(root) is not None


@pytest.mark.parametrize("pending", [False, True])
def test_unreferenced_generation_blocks_status_init_and_server_start(
    tmp_path, monkeypatch, pending
):
    root = tmp_path / "server" / "authority"
    recovery = tmp_path / "operator.json"
    real = a._publish

    def stop_after_intent(source, target):
        real(source, target)
        if target.name == g._INTENT:
            raise OSError("injected after intent")

    with monkeypatch.context() as patch:
        if pending:
            patch.setattr(a, "_publish", stop_after_intent)
            with pytest.raises(OSError, match="injected"):
                a.initialize_authority(root, recovery_file=recovery)
        else:
            a.initialize_authority(root, recovery_file=recovery)
    base = root / "generations"
    source = next(base.iterdir())
    shutil.copytree(source, base / str(uuid4()))
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    custody = recovery.read_bytes()
    with pytest.raises(a.AuthorityError, match="unreferenced generation"):
        a.authority_status(root)
    with pytest.raises(a.AuthorityError, match="unreferenced generation"):
        a.initialize_authority(root, recovery_file=recovery)
    cfg = AppConfig()
    cfg.arbiter.storage.server_data_dir = str(root.parent)
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
    app = cast(Any, build_server(cfg, service_plugins=[], prepare_tls=False).app)

    async def start():
        with pytest.raises(a.AuthorityError, match="unreferenced generation"):
            async with app.router.lifespan_context(app):
                pytest.fail("server accepted an unreferenced generation")

    asyncio.run(start())
    assert {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before
    assert recovery.read_bytes() == custody


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and unprivileged links")
@pytest.mark.parametrize(
    "damage", ["selector-mode", "selector-link", "generation-link"]
)
def test_selector_and_generation_permissions_and_aliases_fail(tmp_path, damage):
    initialize(tmp_path)
    root = tmp_path / "state"
    selector = root / g.ACTIVE
    original = selector.read_bytes()
    if damage == "selector-mode":
        selector.chmod(0o644)
    elif damage == "selector-link":
        os.link(selector, tmp_path / "alias")
    else:
        directory = root / "generations" / json.loads(original)["active"]
        other = root / "generations" / "original"
        directory.rename(other)
        directory.symlink_to(other, target_is_directory=True)
    with pytest.raises(a.AuthorityError):
        a.authority_status(root)
    assert selector.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="native Windows generation ACLs")
@pytest.mark.parametrize("target", ["selector", "generation"])
def test_windows_generation_acl_is_not_repaired(tmp_path, target):
    initialize(tmp_path)
    root = tmp_path / "state"
    selector = root / g.ACTIVE
    path = (
        selector
        if target == "selector"
        else root / "generations" / json.loads(selector.read_bytes())["active"]
    )
    subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-5-32-545:M"],
        check=True,
        capture_output=True,
        text=True,
    )
    before = subprocess.check_output(["icacls", str(path)], text=True)
    with pytest.raises(a.AuthorityError):
        a.authority_status(root)
    assert subprocess.check_output(["icacls", str(path)], text=True) == before


@pytest.mark.parametrize("initialized", [False, True])
def test_live_instance_process_excludes_setup_and_second_start(tmp_path, initialized):
    root = tmp_path / "state"
    record = initialize(tmp_path) if initialized else None
    ready = tmp_path / "ready"
    script = """
import sys, time
from pathlib import Path
from arbiter_server.authority import running_instance
with running_instance(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text("ready")
    time.sleep(60)
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(root), str(ready)])
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(a.AuthorityError, match="busy"):
            initialize(tmp_path)
        with pytest.raises(a.AuthorityError, match="busy"):
            with a.running_instance(root):
                pytest.fail("second writer entered")
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
    assert a.authority_status(root) == record


def test_actual_asgi_lifetime_owns_instance_lock_and_releases_it(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
    server = build_server(cfg, service_plugins=[], prepare_tls=False)
    app = cast(Any, server.app)
    root = tmp_path / "server" / "authority"
    from arbiter_server.http_work import HTTPWork

    real_close = HTTPWork.close
    closed = []

    def close_under_lock(work):
        with pytest.raises(a.AuthorityError, match="busy"):
            with a.running_instance(root):
                pytest.fail("instance lock released before HTTP cleanup")
        closed.append(True)
        real_close(work)

    monkeypatch.setattr(HTTPWork, "close", close_under_lock)

    async def exercise():
        async with app.router.lifespan_context(app):
            with pytest.raises(a.AuthorityError, match="busy"):
                a.initialize_authority(root, recovery_file=tmp_path / "operator.json")
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200

    asyncio.run(exercise())
    assert closed == [True]
    assert a.initialize_authority(
        root, recovery_file=tmp_path / "operator.json"
    ) == a.authority_status(root)
