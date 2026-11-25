from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from arbiter_server.authority import _check_path, _open_private, _root
from arbiter_server.storage import PluginStorage

from .transport import failure


_V1_SCHEMA = {
    "store": "CREATE TABLE store (singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_uuid TEXT NOT NULL)",
    "agents": "CREATE TABLE agents (provider_id TEXT PRIMARY KEY, normal_offset INTEGER)",
    "sources": (
        "CREATE TABLE sources (provider_id TEXT, message_id TEXT, participant TEXT NOT NULL, timestamp TEXT NOT NULL, message_type TEXT NOT NULL, PRIMARY KEY(provider_id,message_id), FOREIGN KEY(provider_id) REFERENCES agents(provider_id))"
    ),
    "replies": (
        "CREATE TABLE replies (provider_id TEXT, message_id TEXT, participant TEXT NOT NULL, payload_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','accepted','refused','unknown')), result TEXT, PRIMARY KEY(provider_id,message_id), FOREIGN KEY(provider_id,message_id) REFERENCES sources(provider_id,message_id))"
    ),
}


def _validate_v1_schema(db: sqlite3.Connection) -> None:
    tables = dict(
        db.execute(
            "SELECT name, sql FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    if set(tables) != set(_V1_SCHEMA) or not all(
        isinstance(statement, str) for statement in tables.values()
    ):
        raise ValueError("invalid state tables")
    for table, statement in _V1_SCHEMA.items():
        actual = "".join(tables[table].split()).lower()
        expected = "".join(statement.split()).lower()
        if actual != expected:
            raise ValueError("invalid state schema")


def check_private(path: Path, *, directory: bool = False) -> None:
    _check_path(path, directory=directory)
    if os.name != "nt" and path.lstat().st_mode & 0o777 != (
        0o700 if directory else 0o600
    ):
        raise ValueError("private state requires exact owner-only modes")


def reference_key(reference: str) -> str:
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


class StateStore:
    def __init__(self, storage: PluginStorage):
        self.path = storage.path().absolute()

    def root(self, *, create: bool = False) -> Path:
        try:
            root = _root(self.path, create=create)
            if not root.exists():
                raise FileNotFoundError
            check_private(root, directory=True)
            return root
        except (OSError, ValueError):
            raise failure("plugin_error", "private_state_unavailable") from None

    def directory(self, name: str, *, create: bool = False) -> Path:
        try:
            path = _root(self.root(create=create) / name, create=create)
            if not path.exists():
                raise FileNotFoundError
            check_private(path, directory=True)
            return path
        except (OSError, ValueError):
            raise failure("plugin_error", "private_state_unavailable") from None

    @contextmanager
    def lock(self, name: str, *, create: bool = False):
        path = self.directory("locks", create=create) / name
        try:
            fd = _open_private(path)
            try:
                check_private(path)
            except BaseException:
                os.close(fd)
                raise
        except (OSError, ValueError):
            raise failure("plugin_error", "private_state_unavailable") from None
        with os.fdopen(fd, "r+b") as handle:
            if sys.platform == "win32":
                import msvcrt

                def acquire():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

                def release():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

            else:
                import fcntl

                def acquire():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                def release():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            try:
                acquire()
            except OSError:
                raise failure("conflict", "enrollment_in_progress") from None
            try:
                yield
            finally:
                release()

    @contextmanager
    def database(self, *, initialize: bool = False):
        root = self.root(create=initialize)
        path = root / "state.sqlite"
        db = None
        try:
            if not path.exists():
                if not initialize or any(
                    self.directory("bindings", create=True).iterdir()
                ):
                    raise ValueError("missing state with existing bindings")
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                fresh = True
            else:
                fresh = False
            check_private(path)
            before = path.lstat()
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = root / ("state.sqlite" + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    check_private(sidecar)
                    if suffix != "-journal":
                        raise ValueError("unexpected journal mode")
            db = sqlite3.connect(path, timeout=5, isolation_level=None)
            check_private(path)
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("state replaced")
            db.execute("PRAGMA foreign_keys=ON")
            if not fresh:
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("unsupported state version")
                _validate_v1_schema(db)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=5000")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={256 * 1024 * 1024 // page_size}")
            if fresh:
                db.execute("BEGIN IMMEDIATE")
                for statement in _V1_SCHEMA.values():
                    db.execute(statement)
                db.execute("INSERT INTO store VALUES(1,?)", (str(uuid4()),))
                db.execute("PRAGMA user_version=1")
                db.commit()
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("unsupported state version")
            if fresh:
                _validate_v1_schema(db)
            rows = db.execute(
                "SELECT store_uuid FROM store WHERE singleton=1"
            ).fetchall()
            if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
                raise ValueError("invalid store identity")
            yield db
        except (OSError, ValueError, sqlite3.Error):
            raise failure("plugin_error", "private_state_unavailable") from None
        finally:
            if db is not None:
                db.close()
