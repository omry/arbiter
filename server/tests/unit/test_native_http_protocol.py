from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
import pytest
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse

from omegaconf import OmegaConf

from arbiter_server.artifacts import PluginArtifactStore
from arbiter_server.config import AppConfig
from arbiter_server.main import build_server
from arbiter_server.errors import OperationError, OperationValidationError
from arbiter_server.services import (
    CapabilityDescriptor,
    OperationDescriptor,
    OperationCatalog,
    RuntimeRegistry,
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
class ConstructorValidatedInput:
    text: str

    def __post_init__(self) -> None:
        raise ValueError(f"Invalid submitted text: {self.text}")


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

    def head(self, target: str) -> ASGIResponse:
        return asyncio.run(
            asyncio.wait_for(_asgi_request(self._app, "HEAD", target), timeout=3)
        )


async def _asgi_request(
    app: ASGIApp,
    method: str,
    target: str,
    *,
    json_body: object | None = None,
    body_chunks: Sequence[bytes] | None = None,
    extra_headers: Sequence[tuple[bytes, bytes]] = (),
    on_receive: Callable[[], None] | None = None,
) -> ASGIResponse:
    parsed = urlparse(target)
    body = b""
    headers: list[tuple[bytes, bytes]] = []
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers.append((b"content-type", b"application/json"))
    headers.extend(extra_headers)
    chunks = list(body_chunks) if body_chunks is not None else [body]
    messages: list[ASGIMessage] = []
    received = 0

    async def receive() -> ASGIMessage:
        nonlocal received
        if on_receive is not None:
            on_receive()
        if received >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[received]
        received += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": received < len(chunks),
        }

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
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
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


def _client(
    tmp_path: Path,
    *,
    private_value: object = None,
    plugin: FakePlugin | None = None,
    max_request_body_bytes: int | None = None,
    max_artifact_bytes: int | None = None,
    max_artifact_storage_bytes: int | None = None,
    max_artifact_count: int | None = None,
) -> ASGIClient:
    cfg = AppConfig()
    if max_request_body_bytes is not None:
        cfg.arbiter.limits.max_request_body_bytes = max_request_body_bytes
    if max_artifact_bytes is not None:
        cfg.arbiter.limits.max_artifact_bytes = max_artifact_bytes
    if max_artifact_storage_bytes is not None:
        cfg.arbiter.limits.max_artifact_storage_bytes = max_artifact_storage_bytes
    if max_artifact_count is not None:
        cfg.arbiter.limits.max_artifact_count = max_artifact_count
    cfg.arbiter.account = {
        "echo": {
            "primary": {
                "policy": "primary_policy",
                "description": "Primary echo account.",
                "guidance": "Use for smoke tests.",
                "password": "${oc.env:ARB_TEST_PASSWORD_DOES_NOT_EXIST}",
                "access": private_value,
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
                "access": private_value,
            }
        }
    }
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path)
    server = build_server(
        OmegaConf.structured(cfg),
        service_plugins=[cast(ServicePlugin, plugin or FakePlugin())],
    )
    return ASGIClient(cast(ASGIApp, server.app))


@pytest.mark.parametrize("container", ["scalar", "mapping", "list"])
@pytest.mark.parametrize("source", ["literal", "oc.env", "secret_file"])
def test_discovery_never_serializes_unknown_private_fields(
    tmp_path, monkeypatch, container, source
):
    secret = "dbb-private-credential-value"
    if source == "oc.env":
        monkeypatch.setenv("DBB_TEST_ACCESS", secret)
        value = "${oc.env:DBB_TEST_ACCESS}"
    elif source == "secret_file":
        secret_path = tmp_path / "private-value"
        secret_path.write_text(secret)
        value = "${secret_file:" + str(secret_path) + "}"
    else:
        value = secret
    private_value = (
        {"value": value}
        if container == "mapping"
        else [value] if container == "list" else value
    )
    client = _client(tmp_path, private_value=private_value)

    for path in (
        "/api/v1/plugins/echo/accounts",
        "/api/v1/plugins/echo/accounts/primary",
        "/api/v1/plugins/echo/policies/primary_policy",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert secret.encode() not in response.content
        assert b"access" not in response.content
        assert b"${" not in response.content


def test_discovery_does_not_evaluate_private_resolvers(tmp_path):
    calls = []

    def resolve() -> str:
        calls.append(True)
        return "dbb-private-resolved-value"

    OmegaConf.register_new_resolver("dbb_private", resolve, replace=True)
    try:
        client = _client(tmp_path, private_value="${dbb_private:}")
        assert client.get("/api/v1/plugins/echo/accounts/primary").status_code == 200
        assert (
            client.get("/api/v1/plugins/echo/policies/primary_policy").status_code
            == 200
        )
        assert calls == []
    finally:
        OmegaConf.clear_resolver("dbb_private")


def test_discovery_keeps_only_explicit_plugin_projections(tmp_path, monkeypatch):
    class PublicPlugin(FakePlugin):
        def public_account_config(self, config: object) -> Mapping[str, object]:
            return {"purpose": "smoke testing"}

        def public_policy_config(self, config: object) -> Mapping[str, object]:
            return {"repeat": "allow"}

    monkeypatch.setenv("DBB_TEST_ACCESS", "dbb-private-credential-value")
    client = _client(
        tmp_path, private_value="${oc.env:DBB_TEST_ACCESS}", plugin=PublicPlugin()
    )
    account = client.get("/api/v1/plugins/echo/accounts/primary").json()
    policy = client.get("/api/v1/plugins/echo/policies/primary_policy").json()
    assert account["config"] == {"purpose": "smoke testing"}
    assert account["policy"] == policy
    assert policy["rules"] == {"repeat": "allow"}


def test_discovery_rejects_raw_config_as_a_public_projection(tmp_path):
    class UnsafePlugin(FakePlugin):
        def public_account_config(self, config: object) -> object:
            return config

    response = _client(tmp_path, plugin=UnsafePlugin()).get(
        "/api/v1/plugins/echo/accounts/primary"
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert b"ARB_TEST_PASSWORD" not in response.content


@pytest.mark.parametrize("kind", ["omegaconf", "dataclass"])
@pytest.mark.parametrize("container", ["mapping", "list", "tuple"])
@pytest.mark.parametrize("detail", ["account", "policy"])
def test_discovery_rejects_nested_raw_config_projections(
    tmp_path, monkeypatch, kind, container, detail
):
    from dataclasses import dataclass

    secret = "nested-projection-private-credential"
    monkeypatch.setenv("ARB_TEST_PASSWORD_DOES_NOT_EXIST", secret)
    monkeypatch.setenv("ARB_TEST_POLICY_API_KEY_DOES_NOT_EXIST", secret)

    @dataclass
    class PrivateConfig:
        credential: str = secret

    def project(config):
        raw = config if kind == "omegaconf" else PrivateConfig()
        nested = (
            {"raw": raw}
            if container == "mapping"
            else [raw] if container == "list" else (raw,)
        )
        return {"public": nested}

    class UnsafePlugin(FakePlugin):
        public_account_config = staticmethod(project)
        public_policy_config = staticmethod(project)

    path = (
        "/api/v1/plugins/echo/accounts/primary"
        if detail == "account"
        else "/api/v1/plugins/echo/policies/primary_policy"
    )
    response = _client(tmp_path, plugin=UnsafePlugin()).get(path)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert secret.encode() not in response.content


def test_discovery_rejects_nested_omegaconf_without_resolving_it(tmp_path):
    calls = []

    def resolve() -> str:
        calls.append(True)
        return "nested-private-resolved-value"

    class UnsafePlugin(FakePlugin):
        def public_account_config(self, config: object) -> Mapping[str, object]:
            return {"public": OmegaConf.create([{"access": "${dbb_nested:}"}])}

    OmegaConf.register_new_resolver("dbb_nested", resolve, replace=True)
    try:
        response = _client(tmp_path, plugin=UnsafePlugin()).get(
            "/api/v1/plugins/echo/accounts/primary"
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert calls == []
        assert b"nested-private-resolved-value" not in response.content
    finally:
        OmegaConf.clear_resolver("dbb_nested")


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
    assert account["config"] == {}
    assert account["policy"] == {
        "kind": "policy",
        "plugin": "echo",
        "policy": "primary_policy",
        "rules": {},
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
        "max_artifact_bytes": 50 * 1024 * 1024,
        "max_artifact_storage_bytes": 1024 * 1024 * 1024,
        "max_artifact_count": 2000,
        "supports_uploads": False,
    }


@pytest.mark.parametrize("declared_length", [None, "0", "1", "1000000"])
def test_operation_body_limit_rejects_oversize_before_json_parsing_or_plugin(
    tmp_path, monkeypatch, declared_length
):
    calls = []

    class RecordingPlugin(FakePlugin):
        def invoke_operation(self, operation, arguments, context):
            calls.append(operation)
            return super().invoke_operation(operation, arguments, context)

    client = _client(tmp_path, plugin=RecordingPlugin(), max_request_body_bytes=64)
    parse_calls = []
    original_loads = json.loads

    def record_loads(*args, **kwargs):
        parse_calls.append(True)
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(json, "loads", record_loads)
    headers = (
        []
        if declared_length is None
        else [(b"content-length", declared_length.encode())]
    )
    body = b'{"args":{"text":"' + b"x" * 100 + b'"}}'
    response = asyncio.run(
        _asgi_request(
            client._app,
            "POST",
            "/api/v1/operations/echo:repeat",
            body_chunks=[body[:32], body[32:]],
            extra_headers=headers,
        )
    )
    assert response.status_code == 413
    assert calls == []
    assert parse_calls == []
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Operation request body exceeds the configured size limit.",
            "details": {"max_request_body_bytes": 64},
        }
    }


@pytest.mark.parametrize("delta,expected_status", [(-1, 413), (0, 200), (1, 200)])
def test_operation_body_limit_counts_encoded_bytes_and_accepts_boundary(
    tmp_path, delta, expected_status
):
    body = json.dumps({"args": {"text": "caf\u00e9"}}, ensure_ascii=False).encode(
        "utf-8"
    )
    client = _client(tmp_path, max_request_body_bytes=len(body) + delta)
    response = asyncio.run(
        _asgi_request(
            client._app,
            "POST",
            "/api/v1/operations/echo:repeat",
            body_chunks=[body[:10], body[10:]],
        )
    )
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["result"]["text"] == "caf\u00e9"


def test_operation_body_limit_does_not_read_past_oversized_chunk(tmp_path):
    client = _client(tmp_path, max_request_body_bytes=64)
    reads = []
    response = asyncio.run(
        _asgi_request(
            client._app,
            "POST",
            "/api/v1/operations/echo:repeat",
            body_chunks=[b"x" * 65, b"must not be read"],
            on_receive=lambda: reads.append(True),
        )
    )
    assert response.status_code == 413
    assert len(reads) == 1


@pytest.mark.parametrize("declared_length", ["65", "9" * 5000])
def test_operation_body_limit_rejects_oversized_length_without_reading(
    tmp_path, declared_length
):
    client = _client(tmp_path, max_request_body_bytes=64)
    reads = []
    response = asyncio.run(
        _asgi_request(
            client._app,
            "POST",
            "/api/v1/operations/echo:repeat",
            body_chunks=[b"{}"],
            extra_headers=[(b"content-length", declared_length.encode())],
            on_receive=lambda: reads.append(True),
        )
    )
    assert response.status_code == 413
    assert reads == []


@pytest.mark.parametrize("declared_length", ["-1", "abc", "1.5", "\u0661", ""])
def test_operation_body_limit_rejects_invalid_content_length(tmp_path, declared_length):
    client = _client(tmp_path, max_request_body_bytes=64)
    response = asyncio.run(
        _asgi_request(
            client._app,
            "POST",
            "/api/v1/operations/echo:repeat",
            body_chunks=[b"{}"],
            extra_headers=[(b"content-length", declared_length.encode("utf-8"))],
        )
    )
    assert response.status_code == 400


def test_operation_body_limit_default_is_one_mib(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/operations/echo:repeat", json={"args": {"text": "x" * (1024 * 1024)}}
    )
    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_request_body_bytes"] == 1024 * 1024


@pytest.mark.parametrize("limit", [0, -1])
def test_server_rejects_unbounded_request_body_configuration(limit):
    cfg = AppConfig()
    cfg.arbiter.limits.max_request_body_bytes = limit
    with pytest.raises(
        ValueError, match="max_request_body_bytes must be a positive integer"
    ):
        build_server(OmegaConf.structured(cfg), service_plugins=[], prepare_tls=False)


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


@pytest.mark.parametrize(
    "code,status",
    [
        ("policy_denied", 403),
        ("upstream_auth_failed", 500),
        ("upstream_timeout", 504),
        ("partial_failure", 500),
        ("plugin_error", 500),
        ("rate_limited", 429),
    ],
)
def test_native_http_preserves_plugin_classification(
    tmp_path, monkeypatch, code, status
):
    error = OperationError(
        code,
        "Selected public explanation.",
        plugin="echo",
        domain_code="plugin_owned_reason",
        details={"steps": {"first": "succeeded", "second": "failed"}},
        transient="yes",
        replay="unknown",
    )

    def fail(*_args):
        raise error

    monkeypatch.setattr(FakePlugin, "invoke_operation", fail)
    response = _client(tmp_path).post(
        "/api/v1/operations/echo:repeat", json={"args": {"text": "hello"}}
    )
    assert response.status_code == status
    assert response.json() == error.to_dict()


def test_native_http_sanitizes_dataclass_constructor_validation(
    tmp_path, monkeypatch, caplog
):
    secret = "submitted-credential-42a7"

    def describe_operations(*_args):
        return (
            OperationDescriptor(
                name="repeat",
                description="Validate submitted text.",
                input_schema=ConstructorValidatedInput,
            ),
        )

    monkeypatch.setattr(FakePlugin, "describe_operations", describe_operations)
    response = _client(tmp_path).post(
        "/api/v1/operations/echo:repeat", json={"args": {"text": secret}}
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Invalid operation arguments."
    assert secret not in response.content.decode()
    assert secret not in "\n".join(caplog.messages)

    catalog = OperationCatalog(
        [cast(ServicePlugin, FakePlugin())],
        ServicePluginContext(runtimes=RuntimeRegistry({})),
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )
    with pytest.raises(OperationValidationError, match=secret):
        catalog.invoke_operation("echo:repeat", {"text": secret})


@pytest.mark.parametrize(
    "arguments,fields",
    [
        ({"text": "hello", "extra": "private-value-7c9e"}, ["extra"]),
        ({"text": "hello", "times": "private-value-7c9e"}, ["times"]),
    ],
)
def test_native_http_validation_identifies_fields_without_values(
    tmp_path, arguments, fields
):
    response = _client(tmp_path).post(
        "/api/v1/operations/echo:repeat", json={"args": arguments}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"fields": fields}
    assert "private-value-7c9e" not in response.content.decode()


def test_native_http_renders_typed_error_surrogates_and_unicode(tmp_path, monkeypatch):
    surrogate = "\ud800"
    error = OperationError(
        "plugin_error",
        f"Public café {surrogate}",
        plugin=f"echo-{surrogate}",
        domain_code=f"remote-{surrogate}",
        details={"summary": f"Details café {surrogate}"},
        transient="no",
        replay="safe",
    )

    def fail(*_args):
        raise error

    monkeypatch.setattr(FakePlugin, "invoke_operation", fail)
    response = _client(tmp_path).post(
        "/api/v1/operations/echo:repeat", json={"args": {"text": "hello"}}
    )

    assert response.status_code == 500
    assert response.json() == error.to_dict()
    assert b"\\ud800" in response.content
    assert b"caf\\u00e9" in response.content


@pytest.mark.parametrize(
    "exception_type", [ValueError, KeyError, RuntimeError, OSError]
)
def test_unexpected_plugin_errors_do_not_escape_to_response_or_logs(
    tmp_path, monkeypatch, caplog, exception_type
):
    exception_secret = "native-operation-secret-7f9a"
    local_only_sentinel = "native-operation-local-91bd"

    def fail(*_args):
        private_local = local_only_sentinel
        raise exception_type(exception_secret)  # source-only-marker-2c4e

    monkeypatch.setattr(FakePlugin, "invoke_operation", fail)
    caplog.set_level(logging.ERROR, logger="arbiter_server.services")

    response = _client(tmp_path).post(
        "/api/v1/operations/echo:repeat", json={"args": {"text": "hello"}}
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal Arbiter server error.",
            "plugin": "echo",
            "retry": {"transient": "unknown", "replay": "unknown"},
        }
    }
    log_text = "\n".join(caplog.messages)
    assert exception_type.__name__ in log_text
    assert "fail" in log_text
    assert exception_secret not in log_text
    assert local_only_sentinel not in log_text
    assert "source-only-marker-2c4e" not in log_text


@pytest.mark.parametrize(
    "limit,content,status",
    [
        (3, "abcd", 403),
        (4, "abcd", 200),
        (3, "\u00e9\u00e9", 403),
        (4, "\u00e9\u00e9", 200),
    ],
)
def test_core_artifact_limit_is_enforced_and_published(
    tmp_path, limit, content, status
):
    client = _client(tmp_path, max_artifact_bytes=limit)
    details = client.get("/api/v1/operations/echo:make_artifact").json()
    assert details["artifact_policy"]["max_artifact_bytes"] == limit
    response = client.post(
        "/api/v1/operations/echo:make_artifact", json={"args": {"content": content}}
    )
    assert response.status_code == status
    if status == 403:
        assert response.json() == {
            "error": {
                "code": "policy_denied",
                "message": "Artifact exceeds the configured size limit.",
                "details": {"max_artifact_bytes": limit},
                "retry": {"transient": "no", "replay": "unknown"},
            }
        }
        assert list(tmp_path.glob("*/artifacts/*/payload")) == []
    else:
        artifact = response.json()["artifacts"][0]
        assert client.get(artifact["content_url"]).content == content.encode("utf-8")


def test_request_body_limit_does_not_limit_artifact_download_bytes(tmp_path):
    class ProducingPlugin(FakePlugin):
        def invoke_operation(self, operation, arguments, context):
            runtime = context.runtimes.require("echo", FakeRuntime)
            assert runtime.artifact_store is not None
            artifact = runtime.artifact_store.create(
                content=b"x" * 1024,
                filename="result.txt",
                content_type="text/plain",
                source={},
            )
            return {"artifact": artifact.to_dict()}

    client = _client(
        tmp_path,
        plugin=ProducingPlugin(),
        max_request_body_bytes=64,
        max_artifact_bytes=1024,
    )
    response = client.post(
        "/api/v1/operations/echo:make_artifact", json={"args": {"content": "seed"}}
    )
    assert response.status_code == 200
    content_url = response.json()["artifacts"][0]["content_url"]
    assert client.get(content_url).content == b"x" * 1024


@pytest.mark.parametrize("content", ["x" * 2048, "x"])
def test_artifact_that_exceeds_empty_store_capacity_is_nontransient(tmp_path, content):
    client = _client(tmp_path, max_artifact_storage_bytes=256)
    response = client.post(
        "/api/v1/operations/echo:make_artifact", json={"args": {"content": content}}
    )
    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "policy_denied",
            "message": "Artifact exceeds the configured storage limit.",
            "details": {"max_artifact_storage_bytes": 256},
            "retry": {"transient": "no", "replay": "unknown"},
        }
    }
    assert list(tmp_path.glob("*/artifacts/*")) == []


def test_artifact_count_is_published_and_full_store_remains_downloadable(tmp_path):
    client = _client(tmp_path, max_artifact_count=1)
    details = client.get("/api/v1/operations/echo:make_artifact").json()
    assert details["artifact_policy"]["max_artifact_count"] == 1
    first = client.post(
        "/api/v1/operations/echo:make_artifact", json={"args": {"content": "first"}}
    )
    assert first.status_code == 200
    content_url = first.json()["artifacts"][0]["content_url"]
    assert client.get(content_url).content == b"first"
    # Downloading consumes the token, but retained files still occupy a slot.
    second = client.post(
        "/api/v1/operations/echo:make_artifact", json={"args": {"content": "second"}}
    )
    assert second.status_code == 503
    assert second.json() == {
        "error": {
            "code": "resource_exhausted",
            "message": "Artifact storage exhausted; try again later.",
            "details": {
                "max_artifact_storage_bytes": 1024 * 1024 * 1024,
                "max_artifact_count": 1,
            },
            "retry": {"transient": "yes", "replay": "unknown"},
        }
    }
    assert len(list(tmp_path.glob("*/artifacts/*/metadata.json"))) == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_server_rejects_unbounded_artifact_count(limit):
    cfg = AppConfig()
    cfg.arbiter.limits.max_artifact_count = limit
    with pytest.raises(
        ValueError, match="max_artifact_count must be a positive integer"
    ):
        build_server(OmegaConf.structured(cfg), service_plugins=[], prepare_tls=False)


def test_total_artifact_quota_is_published_and_returns_retryable_error(tmp_path):
    cap = 13000
    client = _client(tmp_path, max_artifact_storage_bytes=cap)
    details = client.get("/api/v1/operations/echo:make_artifact").json()
    assert details["artifact_policy"]["max_artifact_storage_bytes"] == cap
    first = client.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "x" * 10000}},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "y" * 10000}},
    )
    assert second.status_code == 503
    assert second.json() == {
        "error": {
            "code": "resource_exhausted",
            "message": "Artifact storage exhausted; try again later.",
            "details": {"max_artifact_storage_bytes": cap},
            "retry": {"transient": "yes", "replay": "unknown"},
        }
    }
    # Rejection does not consume or damage the previously stored artifact.
    content_url = first.json()["artifacts"][0]["content_url"]
    assert client.get(content_url).content == b"x" * 10000
    assert len(list(tmp_path.glob("*/artifacts/*/payload"))) == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_server_rejects_unbounded_total_artifact_quota(limit):
    cfg = AppConfig()
    cfg.arbiter.limits.max_artifact_storage_bytes = limit
    with pytest.raises(
        ValueError, match="max_artifact_storage_bytes must be a positive integer"
    ):
        build_server(OmegaConf.structured(cfg), service_plugins=[], prepare_tls=False)


@pytest.mark.parametrize("limit", [0, -1])
def test_server_rejects_unbounded_artifact_configuration(limit):
    cfg = AppConfig()
    cfg.arbiter.limits.max_artifact_bytes = limit
    with pytest.raises(
        ValueError, match="max_artifact_bytes must be a positive integer"
    ):
        build_server(OmegaConf.structured(cfg), service_plugins=[], prepare_tls=False)


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


@pytest.mark.parametrize("cap_delta", [0, -1], ids=["exact-cap", "below-usage"])
def test_native_http_protocol_keeps_legacy_artifact_access_at_lowered_quota(
    tmp_path, cap_delta
) -> None:
    client = _client(tmp_path)
    operation = client.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "legacy artifact"}},
    ).json()
    artifact_url = operation["result"]["artifact"]["url"]
    parsed = urlparse(artifact_url)
    metadata_path = next(tmp_path.glob("*/artifacts/*/metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["last_accessed_at"] = None
    metadata["last_accessed_at_iso"] = None
    metadata["access_count"] = 0
    metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    retained_before = sum(
        path.stat().st_size for path in tmp_path.glob("*/artifacts/*/*")
    )
    cap = retained_before + cap_delta
    capped = _client(tmp_path, max_artifact_storage_bytes=cap)
    metadata_payload: dict[str, Any] | None = None

    for _ in range(3):
        metadata_response = capped.get(f"{parsed.path}?{parsed.query}")
        assert metadata_response.status_code == 200
        metadata_payload = metadata_response.json()
        head = capped.head(f"{parsed.path}?{parsed.query}")
        assert head.status_code == 200
        retained_after_access = sum(
            path.stat().st_size for path in tmp_path.glob("*/artifacts/*/*")
        )
        assert retained_after_access <= retained_before

    assert metadata_payload is not None
    rejected = capped.post(
        "/api/v1/operations/echo:make_artifact",
        json={"args": {"content": "new artifact"}},
    )
    # The cap is below a new artifact's reserved metadata allocation.
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "policy_denied"
    assert rejected.json()["error"]["retry"]["transient"] == "no"

    content = capped.get(metadata_payload["content_url"])
    assert content.status_code == 200
    assert content.content == b"legacy artifact"
    consumed = capped.get(metadata_payload["content_url"])
    assert consumed.status_code == 410
    assert consumed.json()["error"]["code"] == "artifact_consumed"


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
    assert 'filename="weird \\"name\\"\\\\ __snowman _.txt"' in (content_disposition)
    assert f"filename*=UTF-8''{quote(filename, safe='')}" in content_disposition
