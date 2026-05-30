from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent_arbiter import client


@pytest.fixture(autouse=True)
def isolate_client_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENT_ARBITER_MCP_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_client_help_uses_arbiter_program_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["--help"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: arbiter ")


def test_client_lists_tool_names(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://127.0.0.1:8000/mcp"
        return [
            {"name": "list_accounts", "description": "", "input_schema": {}},
            {"name": "send_email", "description": "", "input_schema": {}},
        ]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["tools", "list"]) == 0

    assert capsys.readouterr().out == "list_accounts\nsend_email\n"


def test_client_lists_tools_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9000/mcp"
        return [
            {
                "name": "list_accounts",
                "description": "List configured accounts.",
                "input_schema": {"type": "object"},
            },
        ]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(["tools", "list", "--json", "mcp_url=http://localhost:9000/mcp"])
        == 0
    )

    assert capsys.readouterr().out == (
        '{"tools": [{"description": "List configured accounts.", '
        '"input_schema": {"type": "object"}, "name": "list_accounts"}]}\n'
    )


def test_client_reads_mcp_url_from_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text("mcp_url: http://localhost:9001/mcp\n", encoding="utf-8")

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9001/mcp"
        return [{"name": "list_accounts", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "tools",
                "list",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_accounts\n"


def test_client_uses_default_client_config_path(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".arbiter"
    config_dir.mkdir()
    (config_dir / "arbiter-client.yaml").write_text(
        "mcp_url: http://localhost:9002/mcp\n",
        encoding="utf-8",
    )

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9002/mcp"
        return [{"name": "list_accounts", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["tools", "list"]) == 0

    assert capsys.readouterr().out == "list_accounts\n"


def test_client_mcp_url_env_overrides_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text("mcp_url: http://localhost:9003/mcp\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ARBITER_MCP_URL", "http://localhost:9004/mcp")

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9004/mcp"
        return [{"name": "list_accounts", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "tools",
                "list",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_accounts\n"


def test_client_override_overrides_env_and_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text("mcp_url: http://localhost:9005/mcp\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ARBITER_MCP_URL", "http://localhost:9006/mcp")

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9007/mcp"
        return [{"name": "list_accounts", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "tools",
                "list",
                "mcp_url=http://localhost:9007/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_accounts\n"


def test_client_rejects_unknown_client_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["tools", "list", "unknown=value"]) == 1

    assert capsys.readouterr().err == (
        "Agent Arbiter client config error: unsupported client override key(s): "
        "unknown\n"
    )


def test_client_rejects_malformed_client_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["tools", "list", "--unknown"]) == 1

    assert capsys.readouterr().err == (
        "Agent Arbiter client config error: client override must use KEY=VALUE "
        "syntax: --unknown\n"
    )


def test_client_bootstrap_writes_client_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "bootstrap",
                "client",
                "mcp_url=http://localhost:9008/mcp",
            ]
        )
        == 0
    )

    config_file = tmp_path / "arbiter-client.yaml"
    assert config_file.read_text(encoding="utf-8") == (
        "mcp_url: http://localhost:9008/mcp\n"
    )
    assert capsys.readouterr().out == f"wrote {config_file}\n"


def test_client_bootstrap_refuses_to_overwrite_existing_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "arbiter-client.yaml"
    config_file.write_text("mcp_url: http://localhost:9009/mcp\n", encoding="utf-8")

    assert client.main(["--config-dir", str(tmp_path), "bootstrap", "client"]) == 1
    assert capsys.readouterr().err == (
        f"refusing to overwrite existing file: {config_file}\n"
    )


def test_client_calls_tool_with_json_args(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "send_email"
        assert arguments == {"account": "primary"}
        return {"ok": True}

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "tools",
                "call",
                "send_email",
                "--args",
                '{"account": "primary"}',
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == '{"ok": true}\n'


def test_client_lists_accounts(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "list_accounts"
        assert arguments == {}
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text='{\n  "accounts": {\n    "smtp": {}\n  }\n}',
                )
            ],
            structuredContent={"accounts": {"smtp": {"primary": {"enabled": True}}}},
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "list"]) == 0

    assert capsys.readouterr().out == (
        '{"accounts": {"smtp": {"primary": {"enabled": true}}}}\n'
    )


def test_client_lists_accounts_accepts_plain_payload(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        return {"accounts": {}}

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "list"]) == 0

    assert capsys.readouterr().out == '{"accounts": {}}\n'


def test_client_warns_when_remote_version_differs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "package_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.4"))
    )

    assert capsys.readouterr().err == (
        "Agent Arbiter version warning: local CLI version 1.2.3 does not match "
        "remote server version 1.2.4.\n"
    )


@pytest.mark.parametrize("remote_version", ["1.2.3", "unknown", None])
def test_client_does_not_warn_when_remote_version_matches_or_is_unavailable(
    remote_version: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "package_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version=remote_version))
    )

    assert capsys.readouterr().err == ""


def test_client_does_not_warn_when_local_version_is_unknown(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "package_version", lambda: "unknown")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.4"))
    )

    assert capsys.readouterr().err == ""


def test_client_rejects_non_object_json_args() -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["tools", "call", "send_email", "--args", "[]"])

    assert exc_info.value.code == 2


def test_client_reports_clean_keyboard_interrupt(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_keyboard_interrupt(*_args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("agent_arbiter.client.anyio.run", raise_keyboard_interrupt)

    assert client.main(["tools", "list"]) == 130

    assert capsys.readouterr().err == "Agent Arbiter client stopped.\n"


def test_client_reports_clean_connection_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeExceptionGroup(Exception):
        def __init__(self, exceptions: tuple[BaseException, ...]) -> None:
            super().__init__("unhandled errors in a TaskGroup")
            self.exceptions = exceptions

    def raise_connection_error(*_args: object) -> int:
        raise FakeExceptionGroup(
            (httpx.ConnectError("All connection attempts failed"),)
        )

    monkeypatch.setattr("agent_arbiter.client.anyio.run", raise_connection_error)

    assert client.main(["tools", "list"]) == 1

    assert capsys.readouterr().err == (
        "Could not connect to Agent Arbiter at http://127.0.0.1:8000/mcp. "
        "Is arbiter-server serve running?\n"
    )
