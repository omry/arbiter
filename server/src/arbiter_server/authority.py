"""Offline authority records; this does not enable caller authentication."""

from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Iterator
from uuid import UUID, uuid4

from .file_protection import ensure_runtime_config_permissions

DATABASE = "authority.sqlite3"
_PENDING = ".authority.pending.sqlite3"
_LOCK = ".authority.lock"
SCHEMA_VERSION = 1


class AuthorityError(ValueError):
    """An offline authority operation could not complete safely."""


@dataclass(frozen=True)
class AuthorityRecord:
    owner_id: str
    server_id: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {"initialized": True, "schema_version": SCHEMA_VERSION, **asdict(self)}


def _check_posix(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise AuthorityError(
            f"authority path is not a regular {'directory' if directory else 'file'}: {path}"
        )
    if os.name != "nt":
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise AuthorityError(
                f"authority path must be owned by this user and private: {path}"
            )
    if not directory and info.st_nlink != 1:
        raise AuthorityError(f"authority file must have exactly one link: {path}")


def _check_parents(parent: Path) -> None:
    if os.name == "nt":
        return
    for path in (parent, *parent.parents):
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise AuthorityError(f"authority parent is not a directory: {path}")
        if info.st_uid not in {0, os.geteuid()} or (
            info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
        ):
            raise AuthorityError(
                f"authority parent can be replaced by another user: {path}"
            )


def _root(path: Path, *, create: bool) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise AuthorityError("authority directory must not be a symlink")
    # Resolve parent aliases once, including platform aliases such as /tmp on macOS.
    root = path.parent.resolve() / path.name
    _check_parents(root.parent)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _check_parents(root.parent)
    if root.exists() or root.is_symlink():
        _check_posix(root, directory=True)
        ensure_runtime_config_permissions(config_dir=root, env_file=None)
    return root


def _check_file(path: Path) -> None:
    _check_posix(path, directory=False)
    ensure_runtime_config_permissions(config_dir=path.parent, env_file=path)


def _open_private(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        _check_file(path)
        info = os.fstat(fd)
        current = path.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AuthorityError("authority file changed while opening")
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    # Never unlink this file: every process must lock the same persistent object.
    with os.fdopen(_open_private(root / _LOCK), "r+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            def lock() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

        else:
            import fcntl

            def lock() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        try:
            lock()
        except OSError as exc:
            raise AuthorityError("authority state is busy; try again later") from exc
        try:
            yield
        finally:
            unlock()


def _remove_pending(root: Path) -> None:
    for name in (_PENDING, _PENDING + "-journal"):
        path = root / name
        if path.exists() or path.is_symlink():
            _check_file(path)
            path.unlink()


def _write_database(path: Path, record: AuthorityRecord) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    _check_file(path)
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "owner_id TEXT NOT NULL, server_id TEXT NOT NULL, created_at TEXT NOT NULL, "
            "CHECK(owner_id != server_id))"
        )
        db.execute(
            "INSERT INTO authority VALUES (1, ?, ?, ?)",
            (record.owner_id, record.server_id, record.created_at),
        )
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _publish(pending: Path, target: Path) -> None:
    os.replace(pending, target)
    if os.name != "nt":
        fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def initialize_authority(state_dir: Path) -> AuthorityRecord:
    """Create one private set of IDs, refusing to replace any existing database."""
    root = _root(state_dir, create=True)
    with _locked(root):
        target = root / DATABASE
        if target.exists() or target.is_symlink():
            raise AuthorityError(
                "authority database already exists; inspect authority status"
            )
        _remove_pending(root)
        record = AuthorityRecord(
            str(uuid4()), str(uuid4()), datetime.now(timezone.utc).isoformat()
        )
        try:
            _write_database(root / _PENDING, record)
            _publish(root / _PENDING, target)
        finally:
            # A killed process may leave only these bounded, unpublished files.
            # Never remove the target after a possibly successful publication.
            _remove_pending(root)
        return record


def authority_status(state_dir: Path) -> AuthorityRecord | None:
    """Inspect committed records without creating or repairing a database."""
    root = _root(state_dir, create=False)
    if not root.exists():
        return None
    with _locked(root):
        path = root / DATABASE
        if not path.exists() and not path.is_symlink():
            return None
        _check_file(path)
        try:
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
            ) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version != SCHEMA_VERSION:
                    raise AuthorityError(
                        "unsupported authority database schema version"
                    )
                rows = db.execute(
                    "SELECT singleton, owner_id, server_id, created_at FROM authority"
                ).fetchall()
            if len(rows) != 1:
                raise ValueError("expected one authority record")
            singleton, owner, server, created = rows[0]
            if singleton != 1:
                raise ValueError("invalid authority singleton")
            for value in (owner, server):
                if not isinstance(value, str) or str(UUID(value, version=4)) != value:
                    raise ValueError("invalid authority ID")
            if owner == server or not isinstance(created, str):
                raise ValueError("invalid authority record")
            when = datetime.fromisoformat(created)
            if when.tzinfo is None or when.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError("invalid creation timestamp")
            return AuthorityRecord(owner, server, created)
        except (sqlite3.Error, ValueError) as exc:
            if isinstance(exc, AuthorityError):
                raise
            raise AuthorityError(
                "invalid or incomplete authority database; refusing to replace it"
            ) from exc


def authority_cli(action: str, state_dir: Path, *, as_json: bool) -> int:
    from .cli_errors import print_cli_error
    import json

    try:
        record = (
            initialize_authority(state_dir)
            if action == "init"
            else authority_status(state_dir)
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print_cli_error(str(exc), area="authority")
        return 1
    payload = record.to_dict() if record is not None else {"initialized": False}
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    elif record is None:
        print("authority: not initialized")
    else:
        print(
            f"authority: initialized\nowner: {record.owner_id}\nserver: {record.server_id}"
        )
    return 0
