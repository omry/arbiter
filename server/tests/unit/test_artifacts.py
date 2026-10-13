from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from arbiter_server.artifacts import ArtifactConsumed, ArtifactNotFound, ArtifactStore
from arbiter_server.errors import OperationError


def _artifact_id_and_nonce(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    match = re.fullmatch(r"/_arbiter/artifacts/([^/]+)", parsed.path)
    assert match is not None
    nonce = parse_qs(parsed.query)["nonce"][0]
    return match.group(1), nonce


def test_default_artifact_size_limit_rejects_oversize_before_writing(tmp_path) -> None:
    root = tmp_path / "store"
    store = ArtifactStore(root=root, base_url="https://localhost/artifacts")
    with pytest.raises(OperationError) as caught:
        store.create(
            plugin="imap",
            content=b"x" * (50 * 1024 * 1024 + 1),
            filename="private.pdf",
            content_type="application/pdf",
            source={},
        )
    assert caught.value.code == "policy_denied"
    assert caught.value.details == {"max_artifact_bytes": 50 * 1024 * 1024}
    assert not root.exists()


@pytest.mark.parametrize("plugin", ["imap", "smtp"])
@pytest.mark.parametrize("content", [b"", b"abc", b"abcd"])
def test_core_artifact_limit_applies_at_plugin_storage_boundary(
    tmp_path, plugin, content
):
    root = tmp_path / "store"
    store = ArtifactStore(
        root=root, base_url="https://localhost/artifacts", max_artifact_bytes=3
    )
    plugin_store = store.for_plugin(plugin)
    assert plugin_store.max_artifact_bytes == 3
    if len(content) > 3:
        with pytest.raises(OperationError):
            plugin_store.create(
                content=content, filename=None, content_type="text/plain", source={}
            )
        assert not root.exists()
    else:
        descriptor = plugin_store.create(
            content=content, filename=None, content_type="text/plain", source={}
        )
        nonce = parse_qs(urlparse(descriptor.url).query)["nonce"][0]
        assert descriptor.size == len(content)
        assert store.open_once(descriptor.id, nonce).path.read_bytes() == content


def test_oversized_artifact_is_rejected_before_cleanup_or_hashing(
    tmp_path, monkeypatch
):
    store = ArtifactStore(
        root=tmp_path, base_url="https://localhost/artifacts", max_artifact_bytes=3
    )
    calls = []
    monkeypatch.setattr(store, "purge_expired", lambda: calls.append("purge"))
    monkeypatch.setattr(
        "arbiter_server.artifacts.hashlib.sha256", lambda *_: calls.append("hash")
    )
    with pytest.raises(OperationError):
        store.create(
            plugin="imap",
            content=b"private-body",
            filename="private.pdf",
            content_type="application/pdf",
            source={},
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit", [0, -1, True, None])
def test_artifact_size_limit_cannot_be_disabled_or_mistyped(tmp_path, limit):
    with pytest.raises(
        ValueError, match="max_artifact_bytes must be a positive integer"
    ):
        ArtifactStore(
            root=tmp_path,
            base_url="https://localhost/artifacts",
            max_artifact_bytes=limit,
        )


def test_artifact_store_creates_one_time_artifact(tmp_path) -> None:
    store = ArtifactStore(
        root=tmp_path,
        base_url="http://127.0.0.1:8000/_arbiter/artifacts",
    )

    descriptor = store.create(
        plugin="imap",
        content=b"PDF",
        filename="contract.pdf",
        content_type="application/pdf",
        source={"message_id": "42"},
    )
    artifact_id, nonce = _artifact_id_and_nonce(descriptor.url)

    artifact = store.open_once(artifact_id, nonce)

    assert artifact.path.read_bytes() == b"PDF"
    assert artifact.filename == "contract.pdf"
    assert artifact.content_type == "application/pdf"
    with pytest.raises(ArtifactConsumed):
        store.open_once(artifact_id, nonce)


def test_artifact_store_inspect_does_not_consume_artifact(tmp_path) -> None:
    store = ArtifactStore(
        root=tmp_path,
        base_url="http://127.0.0.1:8000/_arbiter/artifacts",
    )
    descriptor = store.create(
        plugin="imap",
        content=b"PDF",
        filename="contract.pdf",
        content_type="application/pdf",
        source={},
    )
    artifact_id, nonce = _artifact_id_and_nonce(descriptor.url)

    inspected = store.inspect(artifact_id, nonce)
    opened = store.open_once(artifact_id, nonce)

    assert inspected.filename == "contract.pdf"
    assert opened.path.read_bytes() == b"PDF"


def test_artifact_store_consumes_atomically_for_concurrent_opens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ArtifactStore(
        root=tmp_path,
        base_url="http://127.0.0.1:8000/_arbiter/artifacts",
    )
    descriptor = store.create(
        plugin="imap",
        content=b"PDF",
        filename="contract.pdf",
        content_type="application/pdf",
        source={},
    )
    artifact_id, nonce = _artifact_id_and_nonce(descriptor.url)
    original_validated_artifact = store._validated_artifact
    validation_barrier = threading.Barrier(2)

    def validated_artifact(
        artifact_id: str,
        nonce: str,
    ):
        result = original_validated_artifact(artifact_id, nonce)
        validation_barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(store, "_validated_artifact", validated_artifact)

    def open_artifact() -> str:
        try:
            artifact = store.open_once(artifact_id, nonce)
        except ArtifactConsumed:
            return "consumed"
        assert artifact.path.read_bytes() == b"PDF"
        return "opened"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: open_artifact(), range(2)))

    assert sorted(results) == ["consumed", "opened"]


def test_artifact_store_rejects_wrong_nonce(tmp_path) -> None:
    store = ArtifactStore(
        root=tmp_path,
        base_url="http://127.0.0.1:8000/_arbiter/artifacts",
    )

    descriptor = store.create(
        plugin="imap",
        content=b"PDF",
        filename=None,
        content_type="application/pdf",
        source={},
    )
    artifact_id, _nonce = _artifact_id_and_nonce(descriptor.url)

    with pytest.raises(ArtifactNotFound):
        store.open_once(artifact_id, "wrong")


def test_artifact_store_purges_expired_artifact(tmp_path) -> None:
    store = ArtifactStore(
        root=tmp_path,
        base_url="http://127.0.0.1:8000/_arbiter/artifacts",
        idle_ttl_seconds=1,
    )
    descriptor = store.create(
        plugin="imap",
        content=b"PDF",
        filename=None,
        content_type="application/pdf",
        source={},
    )
    _artifact_id, _nonce = _artifact_id_and_nonce(descriptor.url)

    metadata_path = next(tmp_path.glob("imap/artifacts/*/metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["created_at"] = 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert store.purge_expired() == 1
    assert not metadata_path.exists()
