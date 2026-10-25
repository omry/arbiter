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

DATABASE = "authority.sqlite3"
_PENDING = ".authority.pending.sqlite3"
_LOCK = ".authority.lock"
SCHEMA_VERSION = 3
# Windows Modules Installer is part of the trusted host OS. This exception is
# for ancestors only; authority-root and file ACL allowlists remain unchanged.
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"


class AuthorityError(ValueError):
    """An offline authority operation could not complete safely."""


class AuthorityBusy(AuthorityError):
    """The running instance must handle this administrative operation."""


@dataclass(frozen=True)
class AuthorityRecord:
    owner_id: str
    server_id: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {"initialized": True, "schema_version": SCHEMA_VERSION, **asdict(self)}


def _check_path(
    path: Path, *, directory: bool = False, expected_links: int = 1
) -> None:
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
    else:
        _check_windows_permissions(path, directory=directory)
    if not directory and info.st_nlink != expected_links:
        count = "one link" if expected_links == 1 else f"{expected_links} links"
        raise AuthorityError(f"authority file must have exactly {count}: {path}")


def _windows_caller_sid() -> str:
    import csv
    import subprocess

    try:
        whoami = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"
        output = subprocess.check_output(
            [str(whoami), "/user", "/fo", "csv", "/nh"], text=True, timeout=5
        )
        rows = list(csv.reader(output.splitlines()))
        if len(rows) != 1 or len(rows[0]) != 2:
            raise ValueError("invalid caller SID response")
        caller_sid = rows[0][1]
        if not caller_sid.startswith("S-1-"):
            raise ValueError("invalid caller SID")
    except (KeyError, OSError, subprocess.SubprocessError, ValueError) as exc:
        raise AuthorityError("could not verify authority caller identity") from exc
    return caller_sid


def _check_windows_permissions(path: Path, *, directory: bool) -> None:
    from .file_protection.windows import (
        _WINDOWS_DIRECTORY_MUTATING_ACCESS_MASK,
        _WINDOWS_FILE_READ_WRITE_ACCESS_MASK,
        _windows_file_security,
        _windows_unallowed_access_reason,
    )

    security = _windows_file_security(path)
    owner = security.owner_sid
    if owner not in {_windows_caller_sid(), "S-1-5-18", "S-1-5-32-544"}:
        raise AuthorityError(
            f"authority path must be owned by the caller, SYSTEM or Administrators: {path}"
        )
    mask = (
        _WINDOWS_DIRECTORY_MUTATING_ACCESS_MASK
        if directory
        else _WINDOWS_FILE_READ_WRITE_ACCESS_MASK
    )
    reason = (
        "null DACL grants full access"
        if security.has_null_dacl
        else _windows_unallowed_access_reason(
            security.access_aces, owner_sid=owner, access_mask=mask
        )
    )
    if reason is not None:
        kind = "config directory" if directory else "app env file"
        raise AuthorityError(f"unsafe {kind} permissions: {path}: {reason}")


def _check_windows_parents(parent: Path) -> None:
    from .file_protection.windows import (
        _WindowsAccessAce,
        _windows_file_security,
        _windows_unallowed_access_reason,
    )

    caller_sid = _windows_caller_sid()
    trusted = {caller_sid, "S-1-5-18", "S-1-5-32-544", _TRUSTED_INSTALLER}
    # Creating unrelated siblings is safe. Replacing or changing permissions on
    # an ancestor is not. Inherit-only ACEs do not grant access to that ancestor.
    replacement_mask = 0x00000040 | 0x00010000 | 0x00040000 | 0x00080000
    replacement_mask |= 0x10000000 | 0x40000000
    for path in (parent, *parent.parents):
        if not path.exists():
            continue
        security = _windows_file_security(path)
        owner_sid = security.owner_sid
        if security.has_null_dacl or owner_sid is None or owner_sid not in trusted:
            raise AuthorityError(
                f"authority parent has untrusted ownership or ACL: {path}"
            )
        aces = [
            _WindowsAccessAce(
                sid=owner_sid if ace.sid == "S-1-3-4" else ace.sid,
                mask=ace.mask,
            )
            for ace in security.access_aces
            if not ace.flags & 0x08
        ]
        reason = _windows_unallowed_access_reason(
            [ace for ace in aces if ace.sid not in trusted],
            owner_sid=caller_sid,
            access_mask=replacement_mask,
        )
        if reason is not None:
            raise AuthorityError(
                f"authority parent can be replaced by another user: {path}: {reason}"
            )


def _check_parents(parent: Path) -> None:
    if os.name == "nt":
        _check_windows_parents(parent)
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
        # On Windows, inherit the operator-prepared parent ACL and validate it
        # below. Python's special 0o700 ACL uses Owner Rights, which the existing
        # strict runtime permission validator deliberately rejects.
        root.mkdir(
            mode=0o777 if os.name == "nt" else 0o700, parents=True, exist_ok=True
        )
        _check_parents(root.parent)
    if root.exists() or root.is_symlink():
        _check_path(root, directory=True)
    return root


def _open_private(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        _check_path(path)
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
            raise AuthorityBusy("authority state is busy; try again later") from exc
        try:
            yield
        finally:
            unlock()


def _write_database(path: Path, record: AuthorityRecord) -> None:
    from .credentials import store_setup_credentials

    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    _check_path(path)
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
        store_setup_credentials(
            db,
            record.owner_id,
            record.server_id,
            datetime.fromisoformat(record.created_at),
        )
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _publish(pending: Path, target: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("Windows durable publication is unavailable")
        move = win_dll("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        # Same-volume moves, including directories; never permit copy-and-delete.
        # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH.
        if not move(str(pending), str(target), 0x1 | 0x8):
            raise OSError(
                getattr(ctypes, "get_last_error")(), "MoveFileExW failed", str(target)
            )
    else:
        os.replace(pending, target)
    _flush_parents(target.parent)


def _flush_parents(parent: Path) -> None:
    if os.name != "nt":
        for directory in (parent, *parent.parents):
            fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def initialize_authority(
    state_dir: Path,
    *,
    recovery_file: Path | None = None,
    reuse_recovery_file: bool = False,
) -> AuthorityRecord:
    """Create one private set of IDs, refusing to replace any existing database."""
    root = _root(state_dir, create=True)
    with _locked(root):
        return _initialize_locked(root, recovery_file, reuse_recovery_file)


def _initialize_locked(
    root: Path, recovery_file: Path | None, reuse_recovery_file: bool
) -> AuthorityRecord:
    """Run only while this process owns the persistent instance lock."""
    from . import generations

    if generations.reconcile(root) is not None:
        raise AuthorityError(
            "authority database already exists; inspect authority status"
        )
    if recovery_file is None:
        raise AuthorityError("select an external private --recovery-file destination")
    from .recovery import register_and_publish, setup_file

    with setup_file(recovery_file, root, reuse=reuse_recovery_file) as (path, material):
        record = AuthorityRecord(
            material.owner_id, str(uuid4()), datetime.now(timezone.utc).isoformat()
        )
        with generations.staging(root) as staged:
            _write_database(staged / _PENDING, record)
            register_and_publish(path, record, material, staged / _PENDING)
            _publish(staged / _PENDING, staged / DATABASE)
            generations.activate(root, staged)
        return record


def authority_status(state_dir: Path) -> AuthorityRecord | None:
    """Inspect authority, reconciling publication without repairing database contents."""
    root = _root(state_dir, create=False)
    if not root.exists():
        return None
    with _locked(root):
        return _current_record(root)


def _current_record(root: Path) -> AuthorityRecord | None:
    from .generations import reconcile

    path = reconcile(root)
    if path is not None:
        # The instance owner alone may recover an interrupted live SQLite
        # transaction. Network readers remain read-only and never recover it.
        journal = path.with_name(path.name + "-journal")
        if journal.exists() or journal.is_symlink():
            _check_path(journal)
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise AuthorityError(
                    "unexpected authority SQLite sidecar; preserve state"
                )
        if journal.exists():
            try:
                with closing(
                    sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
                ) as db:
                    db.execute("PRAGMA schema_version").fetchone()
            except sqlite3.Error as exc:
                raise AuthorityError(
                    "authority journal recovery failed; preserve state"
                ) from exc
    return _read_record(path) if path is not None else None


@contextmanager
def running_instance(state_dir: Path) -> Iterator[AuthorityRecord | None]:
    """Own the same instance lock as stopped-instance setup for the full lifetime."""
    root = _root(state_dir, create=True)
    with _locked(root):
        yield _current_record(root)


def _read_record(path: Path) -> AuthorityRecord:
    from .credentials import verify_setup_credentials
    from .recovery import verify_registration

    _check_path(path)
    # Keep database verification separate from instance-lock acquisition so the
    # owning server can inspect its live state without trying to lock itself.
    try:
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise AuthorityError("unsupported authority database schema version")
            rows = db.execute(
                "SELECT singleton, owner_id, server_id, created_at FROM authority"
            ).fetchall()
            credentials = db.execute(
                "SELECT role, certificate_der, private_key_der FROM credentials"
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("expected one authority record")
            verify_registration(db, rows[0][1])
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
        # Offline inspection checks the initial setup at its recorded time.
        # Network verifiers must use the current time, independently.
        verify_setup_credentials(credentials, owner, server, when)
        return AuthorityRecord(owner, server, created)
    except (sqlite3.Error, ValueError) as exc:
        if isinstance(exc, AuthorityError):
            raise
        raise AuthorityError(
            "invalid or incomplete authority database; refusing to replace it"
        ) from exc


def authority_cli(
    action: str,
    state_dir: Path,
    *,
    as_json: bool,
    recovery_file: Path | None = None,
    reuse_recovery_file: bool = False,
) -> int:
    from .cli_errors import print_cli_error
    import json

    try:
        try:
            record = (
                initialize_authority(
                    state_dir,
                    recovery_file=recovery_file,
                    reuse_recovery_file=reuse_recovery_file,
                )
                if action == "init"
                else authority_status(state_dir)
            )
            payload = record.to_dict() if record is not None else {"initialized": False}
        except AuthorityBusy as busy:
            from .local_control import request

            try:
                payload = request(state_dir, action, recovery_file, reuse_recovery_file)
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                raise busy from exc
    except (OSError, ValueError, sqlite3.Error) as exc:
        print_cli_error(str(exc), area="authority")
        return 1
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    elif not payload["initialized"]:
        print("authority: not initialized")
    else:
        print(
            f"authority: initialized\nowner: {payload['owner_id']}\nserver: {payload['server_id']}"
        )
    return 0
