from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
import threading
import time
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


def test_default_total_quota_counts_retained_payload_bytes(tmp_path):
    store = ArtifactStore(root=tmp_path, base_url="https://localhost/artifacts")
    descriptor = store.create(
        plugin="imap",
        content=b"seed",
        filename=None,
        content_type="text/plain",
        source={},
    )
    # A sparse file exercises the 1 GiB default without allocating 1 GiB RAM
    # or writing 1 GiB of data. Metadata size must not hide retained file bytes.
    payload = tmp_path / "imap" / "artifacts" / descriptor.id / "payload"
    with payload.open("r+b") as handle:
        handle.truncate(1024 * 1024 * 1024)
    with pytest.raises(OperationError) as caught:
        store.create(
            plugin="smtp",
            content=b"next",
            filename=None,
            content_type="text/plain",
            source={},
        )
    assert caught.value.code == "resource_exhausted"
    assert caught.value.transient == "yes"
    assert list(tmp_path.glob("smtp/artifacts/*/payload")) == []


def _create_for_quota(store, *, plugin="imap", content=b"x" * 10000, source=None):
    return store.create(
        plugin=plugin,
        content=content,
        filename=None,
        content_type="text/plain",
        source={} if source is None else source,
    )


def _retained_bytes(root):
    return sum(path.stat().st_size for path in root.glob("*/artifacts/*/*"))


def _restore_legacy_metadata(path):
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["last_accessed_at"] = None
    metadata["last_accessed_at_iso"] = None
    metadata["access_count"] = 0
    path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")


@pytest.fixture
def full_store(tmp_path, monkeypatch):
    monkeypatch.setattr("arbiter_server.artifacts.time.time", lambda: 1700000000.0)
    store = ArtifactStore(root=tmp_path, base_url="https://localhost/artifacts")
    descriptor = _create_for_quota(store)
    cap = _retained_bytes(tmp_path)
    store = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=cap,
    )
    return store, descriptor, cap


def test_total_quota_accepts_exact_boundary_including_metadata(tmp_path, full_store):
    _store, _descriptor, cap = full_store
    root = tmp_path / "other"
    store = ArtifactStore(
        root=root,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=cap,
    )
    _create_for_quota(store)
    assert _retained_bytes(root) == cap
    assert store.max_artifact_storage_bytes == cap
    assert store.for_plugin("smtp").max_artifact_storage_bytes == cap


def test_total_quota_is_shared_across_plugins_and_store_restarts(tmp_path, full_store):
    store, descriptor, cap = full_store
    with pytest.raises(OperationError) as caught:
        _create_for_quota(store, plugin="smtp", content=b"next")
    assert caught.value.to_dict() == {
        "error": {
            "code": "resource_exhausted",
            "message": "Artifact storage exhausted; try again later.",
            "details": {"max_artifact_storage_bytes": cap},
            "retry": {"transient": "yes", "replay": "unknown"},
        }
    }
    assert list(tmp_path.glob("smtp/artifacts/*")) == []
    nonce = parse_qs(urlparse(descriptor.url).query)["nonce"][0]
    # A full store still permits metadata access and the existing download.
    store.inspect(descriptor.id, nonce)
    assert _retained_bytes(tmp_path) == cap
    assert store.open_once(descriptor.id, nonce).path.read_bytes() == b"x" * 10000
    assert _retained_bytes(tmp_path) == cap


@pytest.mark.parametrize("cap_delta", [0, -1], ids=["exact-cap", "below-usage"])
def test_legacy_metadata_access_preserves_cap_and_bookkeeping(
    tmp_path, monkeypatch, cap_delta
):
    clock = [1700000000.0]
    monkeypatch.setattr("arbiter_server.artifacts.time.time", lambda: clock[0])
    store = ArtifactStore(root=tmp_path, base_url="https://localhost/artifacts")
    descriptor = _create_for_quota(store, content=b"legacy")
    metadata_path = next(tmp_path.glob("*/artifacts/*/metadata.json"))
    _restore_legacy_metadata(metadata_path)
    retained_before = _retained_bytes(tmp_path)
    cap = retained_before + cap_delta
    capped = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        idle_ttl_seconds=10,
        max_artifact_storage_bytes=cap,
    )
    artifact_id = descriptor.id
    nonce = parse_qs(urlparse(descriptor.url).query)["nonce"][0]

    for access_count, timestamp in enumerate(
        (1700000000.0, 1700000009.0, 1700000018.0), start=1
    ):
        clock[0] = timestamp
        inspected = capped.inspect(artifact_id, nonce)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert inspected.path.read_bytes() == b"legacy"
        assert metadata["last_accessed_at"] == timestamp
        assert metadata["access_count"] == access_count
        assert "last_accessed_at_iso" not in metadata
        assert _retained_bytes(tmp_path) <= retained_before
        if cap_delta == 0:
            assert _retained_bytes(tmp_path) <= cap

    with pytest.raises(OperationError) as caught:
        _create_for_quota(capped, plugin="smtp", content=b"next")
    assert caught.value.code == "resource_exhausted"


def test_total_quota_includes_metadata_and_rejects_before_writes(tmp_path):
    store = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=1024,
    )
    with pytest.raises(OperationError) as caught:
        _create_for_quota(store, content=b"small", source={"context": "x" * 1024})
    assert caught.value.code == "resource_exhausted"
    assert list(tmp_path.glob("*/artifacts/*")) == []


def test_expiry_reclaims_capacity_before_admission(tmp_path, monkeypatch, full_store):
    store, descriptor, cap = full_store
    monkeypatch.setattr("arbiter_server.artifacts.time.time", lambda: 1700000601.0)
    replacement = _create_for_quota(store)
    assert replacement.id != descriptor.id
    assert not (tmp_path / "imap" / "artifacts" / descriptor.id).exists()
    assert _retained_bytes(tmp_path) <= cap


def test_crash_orphans_do_not_strand_capacity(tmp_path):
    orphan = tmp_path / "imap" / "artifacts" / "unfinished"
    orphan.mkdir(parents=True)
    (orphan / "payload").write_bytes(b"x" * 2000)
    store = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=1024,
    )
    _create_for_quota(store, content=b"small")
    assert not orphan.exists()
    assert _retained_bytes(tmp_path) <= 1024


def test_stale_metadata_temporary_file_does_not_strand_capacity(tmp_path):
    initial = ArtifactStore(root=tmp_path, base_url="https://localhost/artifacts")
    _create_for_quota(initial, content=b"seed")
    retained = _retained_bytes(tmp_path)
    cap = retained * 2 + 1000
    artifact_dir = next(tmp_path.glob("*/artifacts/*"))
    temporary = artifact_dir / ".metadata.tmp"
    temporary.write_bytes(b"x" * 4096)
    store = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=cap,
    )

    _create_for_quota(store, plugin="smtp", content=b"seed")

    assert not temporary.exists()
    assert _retained_bytes(tmp_path) <= cap


@pytest.mark.parametrize("failed_file", ["payload", "metadata.json"])
def test_failed_creation_rolls_back_storage(tmp_path, monkeypatch, failed_file):
    import arbiter_server.artifacts as artifacts

    store = ArtifactStore(
        root=tmp_path,
        base_url="https://localhost/artifacts",
        max_artifact_storage_bytes=1024,
    )
    original_write = artifacts._write_private_file

    def fail_write(path, content):
        original_write(path, content)
        if path.name == failed_file:
            raise OSError("simulated write failure")

    monkeypatch.setattr(artifacts, "_write_private_file", fail_write)
    with pytest.raises(OSError, match="simulated write failure"):
        _create_for_quota(store, content=b"small")
    assert list(tmp_path.glob("*/artifacts/*")) == []
    monkeypatch.setattr(artifacts, "_write_private_file", original_write)
    _create_for_quota(store, content=b"small")
    assert _retained_bytes(tmp_path) <= 1024


def test_failed_access_update_preserves_download_and_capacity(
    tmp_path, monkeypatch, full_store
):
    import arbiter_server.artifacts as artifacts

    store, descriptor, cap = full_store
    nonce = parse_qs(urlparse(descriptor.url).query)["nonce"][0]
    original_write = artifacts._write_private_file

    def fail_write(path, content):
        original_write(path, content)
        raise OSError("simulated metadata update failure")

    monkeypatch.setattr(artifacts, "_write_private_file", fail_write)
    with pytest.raises(OSError, match="simulated metadata update failure"):
        store.open_once(descriptor.id, nonce)
    artifact_dir = tmp_path / "imap" / "artifacts" / descriptor.id
    assert not (artifact_dir / "consumed").exists()
    assert not (artifact_dir / ".metadata.tmp").exists()
    assert _retained_bytes(tmp_path) == cap
    monkeypatch.setattr(artifacts, "_write_private_file", original_write)
    assert store.open_once(descriptor.id, nonce).path.read_bytes() == b"x" * 10000


def test_access_metadata_remains_within_allocation_at_large_counter(
    tmp_path, full_store
):
    store, descriptor, cap = full_store
    path = tmp_path / "imap" / "artifacts" / descriptor.id / "metadata.json"
    allocated = path.stat().st_size
    metadata = json.loads(path.read_text())
    metadata["access_count"] = 2**63 - 1
    path.write_bytes(
        json.dumps(metadata, sort_keys=True).encode().ljust(allocated, b" ")
    )
    nonce = parse_qs(urlparse(descriptor.url).query)["nonce"][0]
    store.inspect(descriptor.id, nonce)
    assert json.loads(path.read_text())["access_count"] == 2**63 - 1
    assert _retained_bytes(tmp_path) == cap


def test_concurrent_store_instances_cannot_overcommit(
    tmp_path, monkeypatch, full_store
):
    import arbiter_server.artifacts as artifacts

    _store, _descriptor, cap = full_store
    root = tmp_path / "concurrent"
    barrier = threading.Barrier(2)
    original_write = artifacts._write_private_file

    def slow_write(path, content):
        time.sleep(0.05)
        original_write(path, content)

    monkeypatch.setattr(artifacts, "_write_private_file", slow_write)

    def create(_index):
        store = ArtifactStore(
            root=root,
            base_url="https://localhost/artifacts",
            max_artifact_storage_bytes=cap,
        )
        barrier.wait(timeout=5)
        try:
            _create_for_quota(store)
        except OperationError as exc:
            assert exc.code == "resource_exhausted"
            return "full"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    assert sorted(results) == ["created", "full"]
    assert _retained_bytes(root) <= cap


def test_separate_processes_cannot_overcommit(tmp_path):
    script = """
import sys
from pathlib import Path
from arbiter_server.artifacts import ArtifactStore
from arbiter_server.errors import OperationError
store = ArtifactStore(root=Path(sys.argv[1]), base_url='https://localhost/artifacts',
                      max_artifact_storage_bytes=13000)
print('ready', flush=True)
sys.stdin.readline()
try:
    store.create(plugin='imap', content=b'x' * 10000, filename=None,
                 content_type='text/plain', source={})
except OperationError as exc:
    assert exc.code == 'resource_exhausted'
    print('full', flush=True)
else:
    print('created', flush=True)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
            results.append(stdout.strip())
        assert sorted(results) == ["created", "full"]
        assert _retained_bytes(tmp_path) <= 13000
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("limit", [0, -1, True, None])
def test_total_quota_must_be_positive_integer(tmp_path, limit):
    with pytest.raises(
        ValueError, match="max_artifact_storage_bytes must be a positive integer"
    ):
        ArtifactStore(
            root=tmp_path,
            base_url="https://localhost/artifacts",
            max_artifact_storage_bytes=limit,
        )


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
    start_barrier = threading.Barrier(2)

    def open_artifact() -> str:
        start_barrier.wait(timeout=5)
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
