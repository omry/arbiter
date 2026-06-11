from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from urllib.parse import quote

from .storage import ensure_private_dir, plugin_data_dir


DEFAULT_IDLE_TTL_SECONDS = 10 * 60
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CONSUMED_MARKER = "consumed"


class ArtifactError(RuntimeError):
    pass


class ArtifactNotFound(ArtifactError):
    pass


class ArtifactExpired(ArtifactError):
    pass


class ArtifactConsumed(ArtifactError):
    pass


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
    ) -> None:
        if idle_ttl_seconds < 1:
            raise ValueError("artifact idle_ttl_seconds must be at least 1")
        if retention_seconds < 1:
            raise ValueError("artifact retention_seconds must be at least 1")
        self._root = root
        self._base_url = base_url.rstrip("/")
        self._idle_ttl_seconds = idle_ttl_seconds
        self._retention_seconds = retention_seconds

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
        self.purge_expired()
        artifact_id = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(32)
        created_at = time.time()
        payload_hash = hashlib.sha256(content).hexdigest()
        artifact_dir = plugin_data_dir(self._root, plugin) / "artifacts" / artifact_id
        ensure_private_dir(artifact_dir)
        payload_path = artifact_dir / "payload"
        metadata_path = artifact_dir / "metadata.json"
        _write_private_file(payload_path, content)
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
            "last_accessed_at_iso": None,
            "access_count": 0,
            "consumed": False,
            "one_time": True,
            "idle_ttl_seconds": self._idle_ttl_seconds,
            "retention_seconds": self._retention_seconds,
        }
        _write_private_file(
            metadata_path,
            json.dumps(metadata, sort_keys=True).encode("utf-8"),
        )
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
        self.purge_expired()
        artifact_dir, metadata = self._validated_artifact(artifact_id, nonce)
        self._claim_artifact(artifact_dir, artifact_id)
        self._record_access(artifact_dir, metadata, consume=True)
        return _artifact_read(artifact_dir, metadata)

    def inspect(self, artifact_id: str, nonce: str) -> ArtifactRead:
        self.purge_expired()
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
            with os.fdopen(os.open(marker_path, flags, 0o600), "wb") as handle:
                handle.write(_iso_timestamp(time.time()).encode("utf-8"))
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
        accessed_at = time.time()
        metadata["last_accessed_at"] = accessed_at
        metadata["last_accessed_at_iso"] = _iso_timestamp(accessed_at)
        metadata["access_count"] = _int_or_default(metadata.get("access_count"), 0) + 1
        if consume:
            metadata["consumed"] = True
            metadata["nonce_sha256"] = None
        _write_private_file(
            artifact_dir / "metadata.json",
            json.dumps(metadata, sort_keys=True).encode("utf-8"),
        )

    def purge_expired(self) -> int:
        if not self._root.exists():
            return 0
        now = time.time()
        removed = 0
        for metadata_path in self._root.glob("*/artifacts/*/metadata.json"):
            artifact_dir = metadata_path.parent
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                shutil.rmtree(artifact_dir, ignore_errors=True)
                removed += 1
                continue
            if self._is_expired(metadata, now):
                shutil.rmtree(artifact_dir, ignore_errors=True)
                removed += 1
        return removed

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
