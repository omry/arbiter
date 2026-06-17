from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse

from omegaconf import OmegaConf

from arbiter_server.artifacts import PluginArtifactStore
from arbiter_server.config import AppConfig
from arbiter_server.main import build_server
from arbiter_server.services import (
    CapabilityDescriptor,
    OperationDescriptor,
    ServicePlugin,
    ServicePluginContext,
    ServiceRuntimeContext,
)

ASGIMessage = dict[str, Any]
ASGIApp = Callable[
    [
        Mapping[str, Any],
        Callable[[], Awaitable[ASGIMessage]],
        Callable[[ASGIMessage], Awaitable[None]],
    ],
    Awaitable[None],
]


@dataclass(frozen=True)
class RepeatInput:
    text: str = field(metadata={"description": "Text to repeat."})
    times: int = field(default=1, metadata={"description": "Repeat count."})


@dataclass(frozen=True)
class ArtifactInput:
    content: str = field(metadata={"description": "Artifact content."})
    filename: str = field(
        default="message.txt",
        metadata={"description": "Artifact filename."},
    )


class FakeRuntime:
    def __init__(self, artifact_store: PluginArtifactStore | None) -> None:
        self.artifact_store = artifact_store


class FakePlugin:
    name = "echo"
    version = "0.9.2.dev1"
    server_api_version = "0.9"

    def register_configs(self, _config_store: Any) -> None:
        return None

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
        return None

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> FakeRuntime:
        artifact_store = context.dependencies.get("artifact_store")
        return FakeRuntime(
            artifact_store if isinstance(artifact_store, PluginArtifactStore) else None
        )

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name="echo",
            description="Echo text and create test artifacts.",
        )

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> Sequence[OperationDescriptor]:
        return (
            OperationDescriptor(
                name="make_artifact",
                description="Create a streamed text artifact.",
                input_schema=ArtifactInput,
            ),
            OperationDescriptor(
                name="repeat",
                description="Repeat text.",
                input_schema=RepeatInput,
            ),
        )

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        context: ServicePluginContext,
    ) -> object:
        if operation == "repeat":
            return {
                "text": str(arguments["text"]) * int(arguments["times"]),
            }
        if operation == "make_artifact":
            runtime = context.runtimes.require("echo", FakeRuntime)
            if runtime.artifact_store is None:
                raise RuntimeError("artifact store unavailable")
            artifact = runtime.artifact_store.create(
                content=str(arguments["content"]).encode("utf-8"),
                filename=str(arguments["filename"]),
                content_type="text/plain",
                source={"operation": "echo:make_artifact"},
            )
            return {"artifact": artifact.to_dict()}
        raise ValueError(f"unknown operation: echo:{operation}")


@dataclass(frozen=True)
class ASGIResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class ASGIClient:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    def get(self, target: str) -> ASGIResponse:
        return asyncio.run(
            asyncio.wait_for(_asgi_request(self._app, "GET", target), timeout=3)
        )

    def post(self, target: str, *, json: object) -> ASGIResponse:
        return asyncio.run(
            asyncio.wait_for(
                _asgi_request(self._app, "POST", target, json_body=json),
                timeout=3,
            )
        )


async def _asgi_request(
    app: ASGIApp,
    method: str,
    target: str,
    *,
    json_body: object | None = None,
) -> ASGIResponse:
    parsed = urlparse(target)
    body = b""
    headers: list[tuple[bytes, bytes]] = []
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers.append((b"content-type", b"application/json"))
    messages: list[ASGIMessage] = []
    received = False

    async def receive() -> ASGIMessage:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: ASGIMessage) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in cast(Sequence[tuple[bytes, bytes]], start["headers"])
    }
    return ASGIResponse(
        status_code=cast(int, start["status"]),
        headers=response_headers,
        content=response_body,
    )


def _client(tmp_path: Path) -> ASGIClient:
    cfg = AppConfig()
    cfg.arbiter.account = {
        "echo": {
            "primary": {
                "policy": "primary_policy",
                "description": "Primary echo account.",
                "guidance": "Use for smoke tests.",
                "password": "${oc.env:ARB_TEST_PASSWORD_DOES_NOT_EXIST}",
            }
        }
    }
    cfg.arbiter.policy = {
        "echo": {
            "primary_policy": {
                "rules": {
                    "repeat": "allow",
                    "idempotency": {"cache_dir": "/tmp/arbiter-cache"},
                },
                "api_key": "${oc.env:ARB_TEST_POLICY_API_KEY_DOES_NOT_EXIST}",
            }
        }
    }
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path)
    server = build_server(
        OmegaConf.structured(cfg),
        service_plugins=[cast(ServicePlugin, FakePlugin())],
    )
    return ASGIClient(cast(ASGIApp, server.app))


def test_native_http_protocol_exposes_health_info_and_progressive_discovery(
    tmp_path,
) -> None:
    client = _client(tmp_path)

    assert client.get("/_health_").json() == {"status": "ok"}

    info = client.get("/api/v1/info").json()
    assert info["name"] == "arbiter"
    assert info["deployment_scope"] == "unknown"
    assert set(info["source"]) == {"commit", "dirty", "build_time"}

    plugins = client.get("/api/v1/plugins").json()
    assert plugins == {
        "plugins": [
            {
                "id": "echo",
                "summary": "Echo text and create test artifacts.",
            }
        ]
    }

    plugin = client.get("/api/v1/plugins/echo").json()
    assert plugin == {
        "id": "echo",
        "summary": "Echo text and create test artifacts.",
    }

    accounts = client.get("/api/v1/plugins/echo/accounts").json()
    assert accounts == {
        "plugin": "echo",
        "accounts": [
            {
                "plugin": "echo",
                "account": "primary",
                "description": "Primary echo account.",
                "guidance": "Use for smoke tests.",
                "policy": "primary_policy",
            }
        ],
    }

    account = client.get("/api/v1/plugins/echo/accounts/primary").json()
    assert account["kind"] == "account"
    assert account["plugin"] == "echo"
    assert account["account"] == "primary"
    assert account["description"] == "Primary echo account."
    assert account["guidance"] == "Use for smoke tests."
    assert account["config"]["password"] == "<redacted>"
    assert account["policy"] == {
        "kind": "policy",
        "plugin": "echo",
        "policy": "primary_policy",
        "rules": {
            "rules": {
                "repeat": "allow",
                "idempotency": {"cache_dir": "<redacted>"},
            },
            "api_key": "<redacted>",
        },
    }

    policy = client.get("/api/v1/plugins/echo/policies/primary_policy").json()
    assert policy == account["policy"]

    operations = client.get("/api/v1/plugins/echo/operations").json()
    assert operations == {
        "plugin": "echo",
        "operations": [
            {
                "id": "echo:make_artifact",
                "summary": "Create a streamed text artifact.",
                "when_to_use": "Create a streamed text artifact.",
            },
            {
                "id": "echo:repeat",
                "summary": "Repeat text.",
                "when_to_use": "Repeat text.",
            },
        ],
    }

    details = client.get("/api/v1/operations/echo:repeat").json()
    assert details["id"] == "echo:repeat"
    assert details["plugin"] == "echo"
    assert details["input_schema"]["required"] == ["text"]
    assert details["artifact_policy"] == {
        "inline_max_bytes": 5120,
        "supports_uploads": False,
    }


def test_native_http_protocol_invokes_operations_and_returns_error_envelopes(
    tmp_path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/v1/operations/echo:repeat",
        json={"args": {"text": "ha", "times": 3}},
    )
    assert response.status_code == 200
    assert response.json() == {
        "result": {"text": "hahaha"},
        "artifacts": [],
        "warnings": [],
    }

    error = client.post("/api/v1/operations/echo:repeat", json={"args": {}})
    assert error.status_code == 422
    assert error.json()["error"]["code"] == "validation_error"

    malformed_request = client.post("/api/v1/operations/echo:repeat", json=[])
    assert malformed_request.status_code == 400
    assert malformed_request.json()["error"]["code"] == "validation_error"

    missing = client.get("/api/v1/operations/echo:missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_native_http_protocol_returns_artifact_metadata_and_streamed_content(
    tmp_path,
) -> None:
    client = _client(tmp_path)

    operation = client.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "hello artifact"}},
    ).json()
    artifact_url = operation["result"]["artifact"]["url"]
    parsed = urlparse(artifact_url)
    assert operation["artifacts"] == [
        {
            "id": operation["result"]["artifact"]["id"],
            "name": "message.txt",
            "mime_type": "text/plain",
            "size": len("hello artifact"),
            "sha256": operation["result"]["artifact"]["sha256"],
            "content_url": (
                f"/api/v1/artifacts/{operation['result']['artifact']['id']}"
                f"/content?{parsed.query}"
            ),
            "inline": {
                "encoding": "utf-8",
                "data": "hello artifact",
            },
        }
    ]

    metadata = client.get(f"{parsed.path}?{parsed.query}")
    assert metadata.status_code == 200
    metadata_payload = metadata.json()
    assert metadata_payload["name"] == "message.txt"
    assert metadata_payload["mime_type"] == "text/plain"
    assert metadata_payload["size"] == len("hello artifact")
    assert metadata_payload["sha256"] == operation["result"]["artifact"]["sha256"]

    content = client.get(metadata_payload["content_url"])
    assert content.status_code == 200
    assert content.content == b"hello artifact"
    assert content.headers["x-arbiter-artifact-sha256"] == metadata_payload["sha256"]


def test_native_http_protocol_escapes_artifact_content_disposition_filename(
    tmp_path,
) -> None:
    client = _client(tmp_path)
    filename = 'weird "name"\\ \r\nsnowman ☃.txt'

    operation = client.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "hello artifact", "filename": filename}},
    ).json()
    content = client.get(operation["artifacts"][0]["content_url"])

    assert content.status_code == 200
    content_disposition = content.headers["content-disposition"]
    assert "\r" not in content_disposition
    assert "\n" not in content_disposition
    assert 'filename="weird \\"name\\"\\\\ __snowman _.txt"' in (
        content_disposition
    )
    assert f"filename*=UTF-8''{quote(filename, safe='')}" in content_disposition
