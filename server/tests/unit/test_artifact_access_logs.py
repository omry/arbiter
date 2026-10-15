from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest
import uvicorn
from omegaconf import OmegaConf

from arbiter_server.artifacts import ArtifactStore
from arbiter_server.config import AppConfig
from arbiter_server.main import (
    NativeHTTPServer,
    _ArtifactAccessLogFilter,
    _run_server,
    build_server,
)


def test_live_artifact_tokens_are_absent_from_native_access_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "artifact-data"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = AppConfig()
    config.arbiter.server.bind.port = port
    config.arbiter.storage.plugin_data_dir = str(data_dir)
    config.arbiter.storage.server_data_dir = str(tmp_path / "server-data")
    server = build_server(OmegaConf.structured(config), service_plugins=[])
    running: list[uvicorn.Server] = []

    def serve(app: Any, **kwargs: Any) -> None:
        actual = uvicorn.Server(uvicorn.Config(app, **kwargs))
        running.append(actual)
        actual.run(sockets=[listener])

    monkeypatch.setattr(uvicorn, "run", serve)
    access_logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access_logger, "filters", [])
    monkeypatch.setattr(access_logger, "handlers", [])
    monkeypatch.setattr(access_logger, "propagate", True)
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    thread = threading.Thread(target=_run_server, args=(server, "https"), daemon=True)
    thread.start()
    base_url = f"https://127.0.0.1:{port}"
    artifact = ArtifactStore(
        root=data_dir, base_url=f"{base_url}/api/v1/artifacts"
    ).create(
        plugin="test",
        content=b"test artifact content",
        filename="message.txt",
        content_type="text/plain",
        source={"operation": "test"},
    )
    parsed = urlparse(artifact.url)
    metadata_url = artifact.url + "&extra=synthetic-private-query"
    content_url = f"{base_url}{parsed.path}/content?{parsed.query}"
    nonce = parsed.query.split("=", 1)[1]
    tls = ssl._create_unverified_context()

    def request(method: str, url: str) -> tuple[int, bytes]:
        try:
            response = urlopen(Request(url, method=method), context=tls, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.status
            assert isinstance(status, int)
            return status, response.read()

    try:
        deadline = time.monotonic() + 5
        while not running or not running[0].started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        assert request("GET", metadata_url)[0] == 200
        assert request("HEAD", content_url) == (200, b"")
        assert request("GET", f"{base_url}{parsed.path}?nonce=invalid-token")[0] == 404
        missing = f"{base_url}/api/v1/artifacts/missing/content?{parsed.query}"
        assert request("HEAD", missing)[0] == 404
        # Neither logging nor the metadata/HEAD requests consume the bearer.
        assert request("GET", content_url) == (200, b"test artifact content")
        assert request("GET", content_url)[0] == 410
    finally:
        if running:
            running[0].should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()

    records = [r for r in caplog.records if r.name == "uvicorn.access"]
    logs = "\n".join(record.getMessage() for record in records)
    assert nonce not in logs
    assert "invalid-token" not in logs
    assert "synthetic-private-query" not in logs
    assert "?nonce=" not in logs
    assert all(nonce not in repr(record.__dict__) for record in records)
    for method, path, status in (
        ("GET", parsed.path, 200),
        ("HEAD", parsed.path + "/content", 200),
        ("GET", parsed.path, 404),
        ("HEAD", "/api/v1/artifacts/missing/content", 404),
        ("GET", parsed.path + "/content", 200),
        ("GET", parsed.path + "/content", 410),
    ):
        assert f'"{method} {path} HTTP/1.1" {status}' in logs


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/api/v1/artifacts/a?nonce=private", "/api/v1/artifacts/a"),
        ("/api/v1/artifacts?nonce=private", "/api/v1/artifacts"),
        (
            "/mounted/api/v1/artifacts/a/content?nonce=private&other=secret",
            "/mounted/api/v1/artifacts/a/content",
        ),
        ("/api/v1/artifacts/a/content", "/api/v1/artifacts/a/content"),
        ("/api/v1/artifacts-extra?key=visible", "/api/v1/artifacts-extra?key=visible"),
        (
            "/api/v1/plugins?next=/api/v1/artifacts/a",
            "/api/v1/plugins?next=/api/v1/artifacts/a",
        ),
    ],
)
def test_launcher_redacts_before_handlers_and_preserves_other_access_fields(
    target: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(logger, "filters", [])
    caplog.set_level(logging.INFO, logger="uvicorn.access")

    def serve(_app: Any, **_kwargs: Any) -> None:
        logger.info('%s - "%s %s HTTP/%s" %d', "client", "HEAD", target, "1.1", 404)
        logger.info("access logging remains enabled")

    monkeypatch.setattr(uvicorn, "run", serve)
    server = NativeHTTPServer(
        app=None, host="127.0.0.1", port=0, ssl_certfile="test", ssl_keyfile="test"
    )
    for _ in range(2):
        _run_server(server, "https")
    assert sum(isinstance(f, _ArtifactAccessLogFilter) for f in logger.filters) == 1
    records = [r for r in caplog.records if r.name == "uvicorn.access"]
    assert [r.args for r in records[::2]] == [
        ("client", "HEAD", expected, "1.1", 404),
        ("client", "HEAD", expected, "1.1", 404),
    ]
    assert all(
        r.getMessage() == "access logging remains enabled" for r in records[1::2]
    )
