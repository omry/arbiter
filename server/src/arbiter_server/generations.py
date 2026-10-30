"""Private initial-state publication, always under the authority instance lock."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from . import authority as a

ACTIVE = "active-generation.json"
_INTENT = ".generation-intent.json"
_CONTROL_PENDING = ".generation-control.pending"
_STAGING = ".generation.pending"


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _base(root: Path, *, create: bool = False) -> Path:
    return a._root(root / "generations", create=create)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise a.AuthorityError("duplicate generation field")
        result[key] = value
    return result


def _control(path: Path, *, intent: bool = False) -> dict[str, str] | None:
    if not _present(path):
        return None
    a._check_path(path)
    with path.open("rb") as handle:
        data = handle.read(513)
    try:
        if len(data) > 512:
            raise ValueError("oversize control record")
        record = json.loads(data, object_pairs_hook=_unique)
        keys = {"version", "active", "sha256"} if intent else {"version", "active"}
        if not isinstance(record, dict) or set(record) != keys:
            raise ValueError("wrong fields")
        if type(record["version"]) is not int or record["version"] != 1:
            raise ValueError("unsupported generation record")
        value = record["active"]
        if not isinstance(value, str) or str(UUID(value, version=4)) != value:
            raise ValueError("invalid generation ID")
        result = {"active": value}
        if intent:
            digest = record["sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError("invalid generation digest")
            result["sha256"] = digest
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise a.AuthorityError(
            "invalid active generation or publication intent"
        ) from exc


def _write_control(root: Path, name: str, record: dict[str, object]) -> None:
    pending = root / _CONTROL_PENDING
    fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            a._check_path(pending)
            handle.write((json.dumps(record, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        a._publish(pending, root / name)
    finally:
        if _present(pending):
            a._check_path(pending)
            pending.unlink()


def _digest(path: Path) -> str:
    a._check_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_control_pending(root: Path) -> None:
    path = root / _CONTROL_PENDING
    if _present(path):
        a._check_path(path)
        path.unlink()


def _remove_staging(root: Path) -> None:
    path = _base(root) / _STAGING
    if not _present(path):
        return
    a._check_path(path, directory=True)
    allowed = {a.DATABASE, a._PENDING, a._PENDING + "-journal"}
    files = list(islice(path.iterdir(), len(allowed) + 1))
    if len(files) > len(allowed) or any(p.name not in allowed for p in files):
        raise a.AuthorityError("unexpected files in unpublished generation")
    for file in files:
        a._check_path(file)
    for file in files:
        file.unlink()
    path.rmdir()


def _database(root: Path, generation: str) -> Path:
    directory = _base(root) / generation
    a._check_path(directory, directory=True)
    path = directory / a.DATABASE
    a._check_path(path)
    return path


def _check_references(root: Path, allowed: set[str], *, selected: bool) -> None:
    base = _base(root)
    if not base.exists():
        return
    entries = list(islice(base.iterdir(), len(allowed) + 1))
    if len(entries) > len(allowed) or any(p.name not in allowed for p in entries):
        reason = (
            "unreferenced generation"
            if selected
            else "saved generation has no active selector"
        )
        raise a.AuthorityError(f"{reason}; preserve existing state")


def reconcile(root: Path) -> Path | None:
    """Finish only our durable initial publication; never choose another identity."""
    for name in (a.DATABASE, a._PENDING, a._PENDING + "-journal"):
        if _present(root / name):
            a._check_path(root / name)
            raise a.AuthorityError(
                "unsupported flat authority layout; preserve existing state"
            )
    active = _control(root / ACTIVE)
    intent = _control(root / _INTENT, intent=True)
    allowed = {_STAGING}
    for reference in (active, intent):
        if reference is not None:
            allowed.add(reference["active"])
    # Check the bounded reference set before publishing or removing anything.
    _check_references(root, allowed, selected=active is not None or intent is not None)
    _remove_control_pending(root)
    if intent is not None:
        if active is not None and active["active"] != intent["active"]:
            raise a.AuthorityError(
                "active generation conflicts with publication intent"
            )
        base = _base(root)
        staged, final = base / _STAGING, base / intent["active"]
        if active is not None and not _present(final):
            raise a.AuthorityError(
                "active generation is missing; preserve existing state"
            )
        source = final if _present(final) else staged
        if _present(final) and _present(staged):
            raise a.AuthorityError("ambiguous pending generation")
        a._check_path(source, directory=True)
        database = source / a.DATABASE
        if active is None:
            if [p.name for p in islice(source.iterdir(), 2)] != [a.DATABASE]:
                raise a.AuthorityError("unexpected files in initial generation")
            if _digest(database) != intent["sha256"]:
                raise a.AuthorityError("pending generation digest mismatch")
        # An already durable selector is authoritative. Its live DB can change
        # after activation; a stale intent must never roll those changes back.
        a._read_record(database, allow_previous=True)
        if source == staged:
            a._publish(staged, final)
        if active is None:
            active = {"active": intent["active"]}
            _write_control(root, ACTIVE, {"version": 1, **active})
        (root / _INTENT).unlink()
        a._flush_parents(root)
    _remove_staging(root)
    if active is None:
        base = _base(root)
        if base.exists() and next(base.iterdir(), None) is not None:
            raise a.AuthorityError(
                "saved generation has no active selector; preserve existing state"
            )
        return None
    return _database(root, active["active"])


@contextmanager
def staging(root: Path) -> Iterator[Path]:
    """Reserve the one unpublished directory; retain durable intent on failure."""
    directory = _base(root, create=True) / _STAGING
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    a._check_path(directory, directory=True)
    try:
        yield directory
    finally:
        if not _present(root / _INTENT):
            _remove_staging(root)


def activate(root: Path, staged: Path) -> None:
    database = staged / a.DATABASE
    a._read_record(database)
    _write_control(
        root,
        _INTENT,
        {"version": 1, "active": str(uuid4()), "sha256": _digest(database)},
    )
    reconcile(root)
