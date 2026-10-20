from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import UUID

import pytest

from arbiter_server import authority
from arbiter_server.main import main


@pytest.fixture(autouse=True)
def private_windows_test_parent(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    from arbiter_server.file_protection import ensure_runtime_config_permissions
    from arbiter_server.file_protection.windows import _windows_file_security

    security = _windows_file_security(tmp_path)
    owner_sid = security.owner_sid
    assert owner_sid is not None
    allowed = {owner_sid, "S-1-5-18", "S-1-5-32-544"}
    # Use real, inheritable ACLs. Python/pytest's special 0o700 Owner Rights ACE
    # is not an operator-prepared private parent under Arbiter's allowlist.
    subprocess.run(
        [
            "icacls",
            str(tmp_path),
            "/inheritance:r",
            "/grant:r",
            f"*{owner_sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for sid in sorted({ace.sid for ace in security.access_aces} - allowed):
        subprocess.run(
            ["icacls", str(tmp_path), "/remove:g", f"*{sid}"],
            check=True,
            capture_output=True,
            text=True,
        )
    ensure_runtime_config_permissions(config_dir=tmp_path, env_file=None)


def test_initialize_reopen_and_independent_roots(tmp_path: Path) -> None:
    root = tmp_path / "one"
    record = authority.initialize_authority(root)
    assert authority.authority_status(root) == record
    assert UUID(record.owner_id).version == UUID(record.server_id).version == 4
    assert record.owner_id != record.server_id
    other = authority.initialize_authority(tmp_path / "two")
    assert {record.owner_id, record.server_id}.isdisjoint(
        {other.owner_id, other.server_id}
    )
    with closing(sqlite3.connect(root / authority.DATABASE)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
    if os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / authority.DATABASE).stat().st_mode & 0o777 == 0o600


def test_database_flush_has_write_access(tmp_path: Path, monkeypatch) -> None:
    real_fsync = os.fsync

    def writable_flush(fd):
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            # FlushFileBuffers on Windows requires write access. A zero-byte
            # write tests that capability on POSIX too, without changing data.
            os.write(fd, b"")
        real_fsync(fd)

    monkeypatch.setattr(authority.os, "fsync", writable_flush)
    record = authority.initialize_authority(tmp_path / "state")
    assert authority.authority_status(tmp_path / "state") == record


def test_status_missing_is_not_initialization(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    assert authority.authority_status(root) is None
    assert not root.exists()
    root.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    assert authority.authority_status(root) is None
    assert not (root / authority.DATABASE).exists()


def test_second_initialization_preserves_database(tmp_path: Path) -> None:
    record = authority.initialize_authority(tmp_path / "state")
    path = tmp_path / "state" / authority.DATABASE
    original = path.read_bytes()
    with pytest.raises(authority.AuthorityError, match="already exists"):
        authority.initialize_authority(path.parent)
    assert path.read_bytes() == original
    assert authority.authority_status(path.parent) == record
    assert path.read_bytes() == original


def test_cli_is_offline_and_reports_errors(tmp_path: Path, capsys, monkeypatch) -> None:
    # An absent config must never prevent stopped-instance init or status.
    def no_composition(*args, **kwargs):
        pytest.fail("offline authority command composed runtime configuration")

    monkeypatch.setattr("arbiter_server.main.compose_config", no_composition)
    args = ["--config-dir", str(tmp_path / "no-config"), "authority"]
    state = ["--state-dir", str(tmp_path / "state"), "--json"]
    assert main([*args, "status", *state]) == 0
    assert json.loads(capsys.readouterr().out) == {"initialized": False}
    assert main([*args, "init", *state]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["initialized"] is True
    assert output["schema_version"] == 1
    assert main([*args, "status", *state]) == 0
    assert json.loads(capsys.readouterr().out) == output
    assert main([*args, "init", *state]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "already exists" in captured.err


@pytest.mark.parametrize(
    "damage", ["garbage", "version", "missing", "duplicate", "uuid", "time"]
)
def test_corrupt_state_never_reinitialized(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "state"
    authority.initialize_authority(root)
    path = root / authority.DATABASE
    if damage == "garbage":
        path.write_bytes(b"not a database")
    else:
        with closing(sqlite3.connect(path)) as db:
            if damage == "version":
                db.execute("PRAGMA user_version=999")
            elif damage == "missing":
                db.execute("DROP TABLE authority")
            elif damage == "duplicate":
                db.execute("ALTER TABLE authority RENAME TO original")
                db.execute("CREATE TABLE authority AS SELECT * FROM original")
                db.execute("INSERT INTO authority SELECT * FROM original")
            elif damage == "uuid":
                db.execute("UPDATE authority SET owner_id='display label'")
            else:
                db.execute("UPDATE authority SET created_at='2026-01-01T00:00:00'")
            db.commit()
    original = path.read_bytes()
    with pytest.raises(authority.AuthorityError):
        authority.authority_status(root)
    with pytest.raises(authority.AuthorityError, match="already exists"):
        authority.initialize_authority(root)
    assert path.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and link semantics")
@pytest.mark.parametrize("target", ["directory", "database", "lock"])
def test_broad_permissions_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "state"
    authority.initialize_authority(root)
    path = {
        "directory": root,
        "database": root / authority.DATABASE,
        "lock": root / ".authority.lock",
    }[target]
    path.chmod(0o755 if target == "directory" else 0o644)
    with pytest.raises(authority.AuthorityError, match="private"):
        authority.authority_status(root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink creation needs no privilege")
@pytest.mark.parametrize(
    "name", [authority.DATABASE, ".authority.lock", ".authority.pending.sqlite3"]
)
def test_symlink_files_rejected_without_touching_destination(
    tmp_path: Path, name: str
) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    outside.chmod(0o600)
    (root / name).symlink_to(outside)
    operation = (
        authority.initialize_authority
        if name.endswith("pending.sqlite3")
        else authority.authority_status
    )
    expected: tuple[type[Exception], ...] = (ValueError, OSError)
    with pytest.raises(expected):
        operation(root)
    assert outside.read_bytes() == b"keep"
    assert (root / name).is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hardlink semantics")
def test_directory_alias_and_hardlinked_database_rejected(tmp_path: Path) -> None:
    root = tmp_path / "state"
    authority.initialize_authority(root)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(authority.AuthorityError, match="symlink"):
        authority.authority_status(alias)
    os.link(root / authority.DATABASE, tmp_path / "linked-db")
    with pytest.raises(authority.AuthorityError, match="exactly one link"):
        authority.authority_status(root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX unsafe parent permissions")
def test_writable_parent_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    with pytest.raises(authority.AuthorityError, match="another user"):
        authority.initialize_authority(parent / "state")
    assert not (parent / "state").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unsafe parent permissions")
@pytest.mark.parametrize("umask", [0o000, 0o002])
def test_nested_missing_parent_rejected_after_creation(
    tmp_path: Path, umask: int
) -> None:
    root = tmp_path / "missing-parent" / "state"
    previous_umask = os.umask(umask)
    try:
        with pytest.raises(authority.AuthorityError, match="another user"):
            authority.initialize_authority(root)
    finally:
        os.umask(previous_umask)
    assert root.exists()
    assert root.stat().st_mode & 0o777 == 0o700
    assert root.parent.stat().st_mode & 0o022
    assert not (root / authority.DATABASE).exists()
    assert not (root / authority._LOCK).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent permissions")
def test_nested_missing_parent_with_safe_umask_reopens(tmp_path, monkeypatch) -> None:
    root = tmp_path / "nested" / "missing-parent" / "state"
    flushed = []
    real_fsync = os.fsync

    def track(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            flushed.append((info.st_dev, info.st_ino))
        real_fsync(fd)

    monkeypatch.setattr(authority.os, "fsync", track)
    previous_umask = os.umask(0o022)
    try:
        record = authority.initialize_authority(root)
    finally:
        os.umask(previous_umask)
    assert authority.authority_status(root) == record
    assert root.parent.stat().st_mode & 0o777 == 0o755
    assert flushed == [
        (p.stat().st_dev, p.stat().st_ino) for p in (root, *root.parents)
    ]


@pytest.mark.parametrize("failure", ["write", "before_publish", "after_publish"])
def test_failure_leaves_only_complete_published_database(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    root = tmp_path / "state"
    real_write = authority._write_database
    real_publish = authority._publish

    def fail_write(path, record):
        path.write_bytes(b"partial")
        path.chmod(0o600)
        raise OSError("injected write failure")

    def fail_publish(pending, target):
        if failure == "after_publish":
            real_publish(pending, target)
        raise OSError("injected publication failure")

    monkeypatch.setattr(
        authority, "_write_database", fail_write if failure == "write" else real_write
    )
    monkeypatch.setattr(authority, "_publish", fail_publish)
    with pytest.raises(OSError, match="injected"):
        authority.initialize_authority(root)
    assert not (root / ".authority.pending.sqlite3").exists()
    assert not (root / ".authority.pending.sqlite3-journal").exists()
    monkeypatch.setattr(authority, "_write_database", real_write)
    monkeypatch.setattr(authority, "_publish", real_publish)
    if failure == "after_publish":
        assert authority.authority_status(root) is not None
        with pytest.raises(authority.AuthorityError, match="already exists"):
            authority.initialize_authority(root)
    else:
        assert authority.authority_status(root) is None
        assert authority.initialize_authority(root) == authority.authority_status(root)


def _wait_for_file(path: Path, child: subprocess.Popen) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            return
        if child.poll() is not None:
            pytest.fail(f"authority child exited early: {child.communicate()}")
        time.sleep(0.02)
    pytest.fail("authority child did not reach checkpoint")


@pytest.mark.parametrize("phase", ["transaction", "publication"])
def test_real_process_lock_and_killed_initialization_recover(
    tmp_path: Path, phase: str
) -> None:
    root = tmp_path / "state"
    ready = tmp_path / "ready"
    script = """
from pathlib import Path
import sys, time, sqlite3
from arbiter_server import authority

def wait(pending, target):
    Path(sys.argv[2]).write_text("ready")
    time.sleep(60)

if sys.argv[3] == "transaction":
    def partial(path, record):
        fd = authority.os.open(path, authority.os.O_CREAT | authority.os.O_EXCL | authority.os.O_RDWR, 0o600)
        authority.os.close(fd)
        db = sqlite3.connect(path)
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE interrupted(value TEXT)")
        wait(path, None)
    authority._write_database = partial
else:
    authority._publish = wait
authority.initialize_authority(Path(sys.argv[1]))
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(ready), phase],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(ready, child)
        assert not (root / authority.DATABASE).exists()
        for operation in (authority.initialize_authority, authority.authority_status):
            with pytest.raises(authority.AuthorityError, match="busy"):
                operation(root)
    finally:
        child.kill()
        child.communicate(timeout=15)
    assert (root / ".authority.pending.sqlite3").exists()
    record = authority.initialize_authority(root)
    assert authority.authority_status(root) == record
    assert not (root / ".authority.pending.sqlite3").exists()
    assert {p.name for p in root.iterdir()} == {authority.DATABASE, ".authority.lock"}


def test_concurrent_process_initializers_publish_one_identity(tmp_path: Path) -> None:
    root = tmp_path / "state"
    script = """
import json, sys
from pathlib import Path
from arbiter_server.authority import initialize_authority, AuthorityError
try:
    print(json.dumps(initialize_authority(Path(sys.argv[1])).to_dict()))
except AuthorityError as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    try:
        results = [child.communicate(timeout=20) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=15)
    winners = [
        json.loads(out)
        for child, (out, _) in zip(children, results)
        if child.returncode == 0
    ]
    assert len(winners) == 1
    record = authority.authority_status(root)
    assert record is not None
    assert record.to_dict() == winners[0]
    assert all(child.returncode in {0, 1} for child in children)


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership")
def test_different_uid_is_not_authorized(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "state"
    authority.initialize_authority(root)
    current = os.geteuid()
    monkeypatch.setattr(authority.os, "geteuid", lambda: current + 1)
    with pytest.raises(authority.AuthorityError):
        authority.authority_status(root)


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACL boundary")
def test_windows_non_inheritable_parent_acl_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    subprocess.run(
        ["icacls", str(parent), "/grant", "*S-1-5-32-545:M"],
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(authority.AuthorityError, match="another user"):
        authority.initialize_authority(parent / "state")
    assert not (parent / "state").exists()


@pytest.mark.parametrize(
    "owner, sid, mask, flags, null_dacl, rejected",
    [
        ("caller", "other", 0x00000040, 0, False, True),
        ("caller", "other", 0x00040000, 0, False, True),
        ("other", "other", 0x10000000, 0, False, True),
        ("caller", "caller", 0, 0, True, True),
        ("caller", "other", 0x00000004, 0, False, False),
        ("caller", "other", 0x10000000, 0x08, False, False),
        ("caller", "S-1-3-4", 0x10000000, 0, False, False),
        ("S-1-5-18", "S-1-5-18", 0x10000000, 0, False, False),
        (
            authority._TRUSTED_INSTALLER,
            authority._TRUSTED_INSTALLER,
            0x10000000,
            0,
            False,
            False,
        ),
        (authority._TRUSTED_INSTALLER, "S-1-3-4", 0x10000000, 0, False, False),
        ("S-1-5-80-12345", "S-1-5-80-12345", 0x10000000, 0, False, True),
    ],
)
def test_windows_parent_replacement_policy(
    tmp_path: Path, monkeypatch, owner, sid, mask, flags, null_dacl, rejected
) -> None:
    from arbiter_server.file_protection import windows

    caller = "S-1-5-21-1-2-3-1001"
    other = "S-1-5-21-1-2-3-1002"
    sids = {"caller": caller, "other": other}
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **k: f'"operator","{caller}"\n'
    )
    security = windows._WindowsFileSecurity(
        owner_sid=sids.get(owner, owner),
        access_aces=(windows._WindowsAccessAce(sids.get(sid, sid), mask, flags),),
        has_null_dacl=null_dacl,
    )
    monkeypatch.setattr(windows, "_windows_file_security", lambda path: security)
    if rejected:
        with pytest.raises(authority.AuthorityError):
            authority._check_windows_parents(tmp_path)
    else:
        authority._check_windows_parents(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACL boundary")
@pytest.mark.parametrize(
    "name", ["root", authority.DATABASE, authority._LOCK, authority._PENDING]
)
def test_windows_foreign_state_owner_rejected(tmp_path: Path, name: str) -> None:
    from arbiter_server.file_protection.windows import _windows_file_security

    root = tmp_path / "state"
    root.mkdir()
    path = root if name == "root" else root / name
    if path != root:
        path.write_bytes(b"keep")
    foreign = "S-1-5-32-545"
    subprocess.run(
        ["icacls", str(path), "/setowner", f"*{foreign}"],
        check=True,
        capture_output=True,
        text=True,
    )
    operation = (
        authority.authority_status
        if name == authority.DATABASE
        else authority.initialize_authority
    )
    with pytest.raises(authority.AuthorityError, match="owned by the caller"):
        operation(root)
    assert _windows_file_security(path).owner_sid == foreign
    if path == root:
        assert not list(root.iterdir())
    else:
        assert path.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "owner, permitted",
    [
        ("S-1-5-21-1-2-3-1001", True),
        ("S-1-5-18", True),
        ("S-1-5-32-544", True),
        ("S-1-5-21-1-2-3-1002", False),
        (authority._TRUSTED_INSTALLER, False),
        (None, False),
    ],
)
def test_windows_state_owner_policy(
    tmp_path: Path, monkeypatch, owner, permitted
) -> None:
    from arbiter_server.file_protection import windows

    monkeypatch.setattr(authority, "_windows_caller_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(
        windows,
        "_windows_file_security",
        lambda path: windows._WindowsFileSecurity(owner, (), False),
    )
    if permitted:
        authority._check_windows_permissions(tmp_path, directory=True)
    else:
        with pytest.raises(authority.AuthorityError, match="owned by the caller"):
            authority._check_windows_permissions(tmp_path, directory=True)


@pytest.mark.parametrize("directory", [False, True])
def test_windows_owner_and_acl_use_same_snapshot(tmp_path, monkeypatch, directory):
    from arbiter_server.file_protection import windows

    caller, foreign = "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002"
    monkeypatch.setattr(authority, "_windows_caller_sid", lambda: caller)
    ace = windows._WindowsAccessAce(foreign, 0x10000000)
    snapshots = iter(
        windows._WindowsFileSecurity(owner, (ace,), False)
        for owner in (caller, foreign)
    )
    reads = []

    def read_security(path):
        reads.append(path)
        return next(snapshots)

    monkeypatch.setattr(windows, "_windows_file_security", read_security)
    with pytest.raises(authority.AuthorityError, match="outside the allowlist"):
        authority._check_windows_permissions(tmp_path, directory=directory)
    assert reads == [tmp_path]


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACL boundary")
def test_windows_broad_directory_acl_rejected_without_repair(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    subprocess.run(
        ["icacls", str(root), "/grant", "*S-1-5-32-545:(OI)(CI)M"],
        check=True,
        capture_output=True,
        text=True,
    )
    before = subprocess.check_output(["icacls", str(root)], text=True)
    with pytest.raises(ValueError, match="unsafe config directory permissions"):
        authority.initialize_authority(root)
    assert not list(root.iterdir())
    assert subprocess.check_output(["icacls", str(root)], text=True) == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows ACL boundary")
def test_windows_broad_database_acl_rejected(tmp_path: Path) -> None:
    root = tmp_path / "state"
    authority.initialize_authority(root)
    path = root / authority.DATABASE
    subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-5-32-545:R"],
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(ValueError, match="unsafe app env file permissions"):
        authority.authority_status(root)
