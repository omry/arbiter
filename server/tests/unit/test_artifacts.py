from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from arbiter_server.artifacts import ArtifactConsumed, ArtifactNotFound, ArtifactStore


def _artifact_id_and_nonce(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    match = re.fullmatch(r"/_arbiter/artifacts/([^/]+)", parsed.path)
    assert match is not None
    nonce = parse_qs(parsed.query)["nonce"][0]
    return match.group(1), nonce


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
