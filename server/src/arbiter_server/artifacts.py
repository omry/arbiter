from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import time
from urllib.parse import quote

from .storage import ensure_private_dir, plugin_data_dir
from .errors import OperationError
from .limits import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    DEFAULT_MAX_ARTIFACT_COUNT,
    DEFAULT_MAX_ARTIFACT_STORAGE_BYTES,
)


DEFAULT_IDLE_TTL_SECONDS = 10 * 60
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CONSUMED_MARKER = "consumed"
# Allocate room for access timestamps and a bounded access counter up front.
# Reading an artifact then fits within its admitted metadata allocation.
METADATA_ACCESS_HEADROOM_BYTES = 128


class ArtifactError(RuntimeError):
    pass


class ArtifactNotFound(ArtifactError):
    pass


class ArtifactExpired(ArtifactError):
    pass


class ArtifactConsumed(ArtifactError):
    pass


class ArtifactTooLarge(OperationError):
    def __init__(self, max_artifact_bytes: int) -> None:
        super().__init__(
            "policy_denied",
            "Artifact exceeds the configured size limit.",
            details={"max_artifact_bytes": max_artifact_bytes},
            transient="no",
            # The store cannot know whether its caller has already performed
            # other operation steps; do not promise safe operation replay.
            replay="unknown",
        )


class ArtifactStorageExhausted(OperationError):
    def __init__(
        self, max_artifact_storage_bytes: int, *, max_artifact_count: int | None = None
    ) -> None:
        details = {"max_artifact_storage_bytes": max_artifact_storage_bytes}
        if max_artifact_count is not None:
            details["max_artifact_count"] = max_artifact_count
        super().__init__(
            "resource_exhausted",
            "Artifact storage exhausted; try again later.",
            details=details,
            transient="yes",
            # Artifact creation does not establish the replay safety of earlier
            # steps in the enclosing plugin operation.
            replay="unknown",
        )


@dataclass(frozen=True)
class ArtifactDescriptor:
    id: str
    url: str
    filename: str | None
    content_type: str
    size: int
    sha256: str
    created_at: str
    expires_after_idle_seconds: int
    one_time: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "expires_after_idle_seconds": self.expires_after_idle_seconds,
            "one_time": self.one_time,
        }


@dataclass(frozen=True)
class ArtifactRead:
    path: Path
    filename: str | None
    content_type: str
    size: int
    sha256: str


class PluginArtifactStore:
    def __init__(self, *, plugin: str, store: ArtifactStore) -> None:
        self._plugin = plugin
        self._store = store

    @property
    def max_artifact_bytes(self) -> int:
        return self._store.max_artifact_bytes

    @property
    def max_artifact_storage_bytes(self) -> int:
        return self._store.max_artifact_storage_bytes

    @property
    def max_artifact_count(self) -> int:
        return self._store.max_artifact_count

    def create(
        self,
        *,
        content: bytes,
        filename: str | None,
        content_type: str,
        source: dict[str, object],
    ) -> ArtifactDescriptor:
        return self._store.create(
            plugin=self._plugin,
            content=content,
            filename=filename,
            content_type=content_type,
            source=source,
        )


class ArtifactStore:
    def __init__(
        self,
        *,
        root: Path,
        base_url: str,
        idle_ttl_seconds: int = DEFAULT_IDLE_TTL_SECONDS,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        max_artifact_storage_bytes: int = DEFAULT_MAX_ARTIFACT_STORAGE_BYTES,
        max_artifact_count: int = DEFAULT_MAX_ARTIFACT_COUNT,
    ) -> None:
        if idle_ttl_seconds < 1:
            raise ValueError("artifact idle_ttl_seconds must be at least 1")
        if retention_seconds < 1:
            raise ValueError("artifact retention_seconds must be at least 1")
        if type(max_artifact_bytes) is not int or max_artifact_bytes < 1:
            raise ValueError("artifact max_artifact_bytes must be a positive integer")
        if (
            type(max_artifact_storage_bytes) is not int
            or max_artifact_storage_bytes < 1
        ):
            raise ValueError(
                "artifact max_artifact_storage_bytes must be a positive integer"
            )
        if type(max_artifact_count) is not int or max_artifact_count < 1:
            raise ValueError("artifact max_artifact_count must be a positive integer")
        self._root = root
        self._base_url = base_url.rstrip("/")
        self._idle_ttl_seconds = idle_ttl_seconds
        self._retention_seconds = retention_seconds
        self._max_artifact_bytes = max_artifact_bytes
        self._max_artifact_storage_bytes = max_artifact_storage_bytes
        self._max_artifact_count = max_artifact_count

    @property
    def max_artifact_bytes(self) -> int:
        return self._max_artifact_bytes

    @property
    def max_artifact_storage_bytes(self) -> int:
        return self._max_artifact_storage_bytes

    @property
    def max_artifact_count(self) -> int:
        return self._max_artifact_count

    @property
    def idle_ttl_seconds(self) -> int:
        return self._idle_ttl_seconds

    def for_plugin(self, plugin: str) -> PluginArtifactStore:
        return PluginArtifactStore(plugin=plugin, store=self)

    def create(
        self,
        *,
        plugin: str,
        content: bytes,
        filename: str | None,
        content_type: str,
        source: dict[str, object],
    ) -> ArtifactDescriptor:
        if len(content) > self._max_artifact_bytes:
            raise ArtifactTooLarge(self._max_artifact_bytes)
        with self._storage_lock():
            self._purge_expired()
            retained_bytes = self._storage_bytes()
            return self._create(
                plugin=plugin,
                content=content,
                filename=filename,
                content_type=content_type,
                source=source,
                retained_bytes=retained_bytes,
            )

    def _create(
        self,
        *,
        plugin: str,
        content: bytes,
        filename: str | None,
        content_type: str,
        source: dict[str, object],
        retained_bytes: int,
    ) -> ArtifactDescriptor:
        artifact_id = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(32)
        created_at = time.time()
        payload_hash = hashlib.sha256(content).hexdigest()
        artifact_dir = plugin_data_dir(self._root, plugin) / "artifacts" / artifact_id
        metadata = {
            "id": artifact_id,
            "plugin": plugin,
            "filename": filename,
            "content_type": content_type,
            "size": len(content),
            "sha256": payload_hash,
            "source": source,
            "nonce_sha256": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
            "created_at": created_at,
            "created_at_iso": _iso_timestamp(created_at),
            "last_accessed_at": None,
            "access_count": 0,
            "consumed": False,
            "one_time": True,
            "idle_ttl_seconds": self._idle_ttl_seconds,
            "retention_seconds": self._retention_seconds,
        }
        metadata_bytes = json.dumps(
            metadata, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        metadata_bytes += b" " * METADATA_ACCESS_HEADROOM_BYTES
        self._check_capacity(
            len(content) + len(metadata_bytes), retained_bytes=retained_bytes
        )
        # Creation holds the root lock and has purged expired/incomplete entries.
        # Each retained artifact occupies one slot even after it is consumed.
        if self._artifact_count() >= self._max_artifact_count:
            raise ArtifactStorageExhausted(
                self._max_artifact_storage_bytes,
                max_artifact_count=self._max_artifact_count,
            )
        ensure_private_dir(artifact_dir)
        try:
            _write_private_file(artifact_dir / "payload", content)
            _write_private_file(artifact_dir / "metadata.json", metadata_bytes)
        except BaseException:
            shutil.rmtree(artifact_dir, ignore_errors=True)
            raise
        encoded_id = quote(artifact_id, safe="")
        encoded_nonce = quote(nonce, safe="")
        return ArtifactDescriptor(
            id=artifact_id,
            url=f"{self._base_url}/{encoded_id}?nonce={encoded_nonce}",
            filename=filename,
            content_type=content_type,
            size=len(content),
            sha256=payload_hash,
            created_at=str(metadata["created_at_iso"]),
            expires_after_idle_seconds=self._idle_ttl_seconds,
            one_time=True,
        )

    def open_once(self, artifact_id: str, nonce: str) -> ArtifactRead:
        with self._storage_lock():
            self._purge_expired()
            artifact_dir, metadata = self._validated_artifact(artifact_id, nonce)
            self._claim_artifact(artifact_dir, artifact_id)
            try:
                self._record_access(artifact_dir, metadata, consume=True)
            except BaseException:
                (artifact_dir / CONSUMED_MARKER).unlink(missing_ok=True)
                raise
            return _artifact_read(artifact_dir, metadata)

    def inspect(self, artifact_id: str, nonce: str) -> ArtifactRead:
        with self._storage_lock():
            self._purge_expired()
            artifact_dir, metadata = self._validated_artifact(artifact_id, nonce)
            self._record_access(artifact_dir, metadata, consume=False)
            return _artifact_read(artifact_dir, metadata)

    def _validated_artifact(
        self,
        artifact_id: str,
        nonce: str,
    ) -> tuple[Path, dict[str, object]]:
        artifact_dir = self._artifact_dir(artifact_id)
        metadata = self._read_metadata(artifact_dir)
        if (
            metadata.get("consumed") is True
            or (artifact_dir / CONSUMED_MARKER).exists()
        ):
            raise ArtifactConsumed(f"artifact already consumed: {artifact_id}")
        if self._is_expired(metadata, time.time()):
            shutil.rmtree(artifact_dir, ignore_errors=True)
            raise ArtifactExpired(f"artifact expired: {artifact_id}")
        expected_hash = str(metadata.get("nonce_sha256", ""))
        actual_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        return artifact_dir, metadata

    def _claim_artifact(self, artifact_dir: Path, artifact_id: str) -> None:
        marker_path = artifact_dir / CONSUMED_MARKER
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            os.close(os.open(marker_path, flags, 0o600))
        except FileExistsError as exc:
            raise ArtifactConsumed(f"artifact already consumed: {artifact_id}") from exc
        if os.name != "nt":
            os.chmod(marker_path, 0o600)

    def _record_access(
        self,
        artifact_dir: Path,
        metadata: dict[str, object],
        *,
        consume: bool,
    ) -> None:
        # The numeric timestamp is authoritative. Drop the unused derived ISO
        # copy so legacy metadata can receive access bookkeeping in place.
        metadata.pop("last_accessed_at_iso", None)
        accessed_at = time.time()
        metadata["last_accessed_at"] = accessed_at
        metadata["access_count"] = min(
            _int_or_default(metadata.get("access_count"), 0) + 1, 2**63 - 1
        )
        if consume:
            metadata["consumed"] = True
            metadata["nonce_sha256"] = None
        metadata_path = artifact_dir / "metadata.json"
        allocated_bytes = metadata_path.stat().st_size
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > allocated_bytes:
            # Older artifacts may not have allocated access headroom. Keep
            # admission accounting for every metadata replacement.
            self._check_capacity(len(encoded) - allocated_bytes)
        encoded = encoded.ljust(allocated_bytes, b" ")
        temporary_path = artifact_dir / ".metadata.tmp"
        try:
            _write_private_file(temporary_path, encoded)
            temporary_path.replace(metadata_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def purge_expired(self) -> int:
        with self._storage_lock():
            return self._purge_expired()

    def _purge_expired(self) -> int:
        if not self._root.exists():
            return 0
        self._remove_stale_metadata_temporary_files()
        now = time.time()
        removed = 0
        for artifact_dir in self._root.glob("*/artifacts/*"):
            if not artifact_dir.is_dir():
                continue
            metadata_path = artifact_dir / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                shutil.rmtree(artifact_dir, ignore_errors=True)
                removed += 1
                continue
            if not isinstance(metadata, dict) or self._is_expired(metadata, now):
                shutil.rmtree(artifact_dir, ignore_errors=True)
                removed += 1
        return removed

    def _remove_stale_metadata_temporary_files(self) -> None:
        for artifact_dir in self._root.glob("*/artifacts/*"):
            if not artifact_dir.is_dir():
                continue
            (artifact_dir / ".metadata.tmp").unlink(missing_ok=True)

    def _storage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for artifact_dir in self._root.glob("*/artifacts/*")
            for path in artifact_dir.rglob("*")
            if path.is_file()
        )

    def _artifact_count(self) -> int:
        return sum(
            1
            for artifact_dir in self._root.glob("*/artifacts/*")
            if artifact_dir.is_dir()
        )

    def _check_capacity(
        self, incoming_bytes: int, *, retained_bytes: int | None = None
    ) -> None:
        if incoming_bytes > self._max_artifact_storage_bytes:
            raise OperationError(
                "policy_denied",
                "Artifact exceeds the configured storage limit.",
                details={
                    "max_artifact_storage_bytes": self._max_artifact_storage_bytes
                },
                transient="no",
                replay="unknown",
            )
        if retained_bytes is None:
            retained_bytes = self._storage_bytes()
        if retained_bytes + incoming_bytes > self._max_artifact_storage_bytes:
            raise ArtifactStorageExhausted(self._max_artifact_storage_bytes)

    @contextmanager
    def _storage_lock(self) -> Iterator[None]:
        # A persistent lock file coordinates all store instances and processes
        # using this root. Never unlink it: that would permit two lock owners.
        ensure_private_dir(self._root)
        flags = os.O_RDWR | os.O_CREAT
        with os.fdopen(
            os.open(self._root / ".artifact-storage.lock", flags, 0o600), "r+b"
        ) as handle:
            if sys.platform == "win32":
                import msvcrt

                # Windows permits locking a byte beyond EOF, so even a newly
                # created empty lock file needs no racy initialization write.
                handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError as exc:
                        # The CRT gives up after ten attempts. Keep waiting on
                        # contention, matching POSIX flock, but propagate faults.
                        if exc.errno not in (errno.EACCES, errno.EDEADLK):
                            raise
                        time.sleep(0.1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _artifact_dir(self, artifact_id: str) -> Path:
        if ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None:
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        if not self._root.exists():
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        matches = [
            plugin_dir / "artifacts" / artifact_id
            for plugin_dir in self._root.iterdir()
            if (plugin_dir / "artifacts" / artifact_id / "metadata.json").is_file()
        ]
        if len(matches) != 1:
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        return matches[0]

    def _read_metadata(self, artifact_dir: Path) -> dict[str, object]:
        try:
            raw = (artifact_dir / "metadata.json").read_text(encoding="utf-8")
            metadata = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactNotFound("artifact metadata is unavailable") from exc
        if not isinstance(metadata, dict):
            raise ArtifactNotFound("artifact metadata is invalid")
        return metadata

    def _is_expired(self, metadata: dict[str, object], now: float) -> bool:
        created_at = _float_or_zero(metadata.get("created_at"))
        last_accessed_at = metadata.get("last_accessed_at")
        reference = (
            _float_or_zero(last_accessed_at)
            if isinstance(last_accessed_at, int | float)
            else created_at
        )
        retention_seconds = _int_or_default(
            metadata.get("retention_seconds"),
            self._retention_seconds,
        )
        idle_ttl_seconds = _int_or_default(
            metadata.get("idle_ttl_seconds"),
            self._idle_ttl_seconds,
        )
        return (
            created_at <= 0
            or now - created_at > retention_seconds
            or now - reference > idle_ttl_seconds
        )


def _write_private_file(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(content)
    if os.name != "nt":
        os.chmod(path, 0o600)


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _float_or_zero(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _artifact_read(artifact_dir: Path, metadata: dict[str, object]) -> ArtifactRead:
    return ArtifactRead(
        path=artifact_dir / "payload",
        filename=_string_or_none(metadata.get("filename")),
        content_type=str(metadata.get("content_type", "application/octet-stream")),
        size=_int_or_default(metadata.get("size"), 0),
        sha256=str(metadata.get("sha256", "")),
    )


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
