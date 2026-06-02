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
    monkeypatch.delenv("ARBITER_MCP_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_client_help_uses_arbiter_program_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: arbiter ")
    assert "--config-name" in output
    assert "--version" in output
    assert "bootstrap" in output


def test_client_without_args_prints_short_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main([]) == 2

    assert capsys.readouterr().out == (
        "usage: arbiter {cap,op,accounts} ...\n" "Run 'arbiter --help' for full help.\n"
    )


def test_client_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("arbiter ")


def test_client_lists_tool_names(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://127.0.0.1:8000/mcp"
        return [
            {"name": "list_caps", "description": "", "input_schema": {}},
            {"name": "run_op", "description": "", "input_schema": {}},
        ]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["mcp", "tools"]) == 0

    assert capsys.readouterr().out == "list_caps\nrun_op\n"


def test_client_tools_defaults_to_list(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://127.0.0.1:8000/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["mcp"]) == 0

    assert capsys.readouterr().out == "list_caps\n"


def test_client_lists_tools_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9000/mcp"
        return [
            {
                "name": "list_caps",
                "description": "List Arbiter capability names.",
                "input_schema": {"type": "object"},
            },
        ]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "mcp",
                "tools",
                "--json",
                "arbiter.mcp_url=http://localhost:9000/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == (
        '{"tools": [{"description": "List Arbiter capability names.", '
        '"input_schema": {"type": "object"}, "name": "list_caps"}]}\n'
    )


def test_client_mcp_default_tools_accepts_json_option(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://127.0.0.1:8000/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["mcp", "--json"]) == 0

    assert capsys.readouterr().out == (
        '{"tools": [{"description": "", "input_schema": {}, "name": "list_caps"}]}\n'
    )


def test_client_reads_mcp_url_from_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text(
        "arbiter:\n  mcp_url: http://localhost:9001/mcp\n",
        encoding="utf-8",
    )

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9001/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "mcp",
                "tools",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_caps\n"


def test_client_rejects_top_level_mcp_url_in_client_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text("mcp_url: http://localhost:9001/mcp\n", encoding="utf-8")

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "mcp",
                "tools",
            ]
        )
        == 1
    )

    assert capsys.readouterr().err == (
        "Arbiter client config error: unsupported client config key(s) in "
        f"{config_file}: mcp_url\n"
    )


def test_client_uses_default_client_config_path(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".arbiter"
    config_dir.mkdir()
    (config_dir / "arbiter-client.yaml").write_text(
        "arbiter:\n  mcp_url: http://localhost:9002/mcp\n",
        encoding="utf-8",
    )

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9002/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert client.main(["mcp", "tools"]) == 0

    assert capsys.readouterr().out == "list_caps\n"


def test_client_mcp_url_env_overrides_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text(
        "arbiter:\n  mcp_url: http://localhost:9003/mcp\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_MCP_URL", "http://localhost:9004/mcp")

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9004/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "mcp",
                "tools",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_caps\n"


def test_client_override_overrides_env_and_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text(
        "arbiter:\n  mcp_url: http://localhost:9005/mcp\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITER_MCP_URL", "http://localhost:9006/mcp")

    async def fake_list_tools(url: str) -> list[Mapping[str, object]]:
        assert url == "http://localhost:9007/mcp"
        return [{"name": "list_caps", "description": "", "input_schema": {}}]

    monkeypatch.setattr(client, "list_tools", fake_list_tools)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "mcp",
                "tools",
                "arbiter.mcp_url=http://localhost:9007/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == "list_caps\n"


def test_client_override_after_optional_positional_is_not_consumed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://localhost:9010/mcp"
        assert name == "describe_cap"
        assert arguments == {"capability": "smtp"}
        return SimpleNamespace(structuredContent={"accounts": {}})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "accounts",
                "desc",
                "smtp",
                "arbiter.mcp_url=http://localhost:9010/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == '{"accounts": {}}\n'


def test_client_rejects_unknown_client_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["mcp", "tools", "unknown=value"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter client config error: unsupported client override key(s): " "unknown\n"
    )


def test_client_rejects_top_level_mcp_url_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["mcp", "tools", "mcp_url=http://localhost:9000/mcp"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter client config error: unsupported client override key(s): " "mcp_url\n"
    )


def test_client_rejects_malformed_client_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["mcp", "tools", "--unknown"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter client config error: client override must use KEY=VALUE "
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
                "arbiter.mcp_url=http://localhost:9008/mcp",
            ]
        )
        == 0
    )

    config_file = tmp_path / "arbiter-client.yaml"
    assert config_file.read_text(encoding="utf-8") == (
        "arbiter:\n  mcp_url: http://localhost:9008/mcp\n"
    )
    assert capsys.readouterr().out == f"wrote {config_file}\n"


def test_client_bootstrap_refuses_to_overwrite_existing_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "arbiter-client.yaml"
    config_file.write_text(
        "arbiter:\n  mcp_url: http://localhost:9009/mcp\n",
        encoding="utf-8",
    )

    assert client.main(["--config-dir", str(tmp_path), "bootstrap", "client"]) == 1
    assert capsys.readouterr().err == (
        "Arbiter client config error: refusing to overwrite existing file: "
        f"{config_file}\n"
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
        assert name == "run_op"
        assert arguments == {
            "id": "smtp:send_email",
            "arguments": {"account": "primary"},
        }
        return {"ok": True}

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "mcp",
                "call",
                "run_op",
                "--args",
                '{"id": "smtp:send_email", "arguments": {"account": "primary"}}',
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == '{"ok": true}\n'


def test_client_cap_alias_lists_capabilities(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "list_caps"
        assert arguments == {}
        return SimpleNamespace(structuredContent={"capabilities": ["imap", "smtp"]})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "list"]) == 0

    assert capsys.readouterr().out == "imap\nsmtp\n"


def test_client_cap_defaults_to_list(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "list_caps"
        assert arguments == {}
        return SimpleNamespace(structuredContent={"capabilities": ["smtp"]})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap"]) == 0

    assert capsys.readouterr().out == "smtp\n"


def test_client_cap_default_list_accepts_json_option(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "list_caps"
        assert arguments == {}
        return SimpleNamespace(structuredContent={"capabilities": ["smtp"]})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "--json"]) == 0

    assert capsys.readouterr().out == '{"capabilities": ["smtp"]}\n'


def test_client_cap_desc_alias_describes_capability(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "describe_cap"
        assert arguments == {"capability": "smtp"}
        return SimpleNamespace(structuredContent={"id": "smtp"})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "desc", "smtp"]) == 0

    assert capsys.readouterr().out == '{"id": "smtp"}\n'


def test_client_op_desc_alias_describes_operation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "describe_op"
        assert arguments == {"id": "smtp:send_email"}
        return SimpleNamespace(structuredContent={"id": "smtp:send_email"})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["op", "desc", "smtp:send_email"]) == 0

    assert capsys.readouterr().out == '{"id": "smtp:send_email"}\n'


def test_client_op_alias_runs_operation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "run_op"
        assert arguments == {
            "id": "smtp:send_email",
            "arguments": {"account": "primary"},
        }
        return SimpleNamespace(structuredContent={"ok": True})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "op",
                "run",
                "smtp:send_email",
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
    calls: list[tuple[str, Mapping[str, Any]]] = []

    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        calls.append((name, arguments))
        if name == "describe_caps":
            assert arguments == {}
            return SimpleNamespace(
                structuredContent={
                    "capabilities": [{"id": "smtp", "accounts": ["primary"]}]
                },
            )
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "list"]) == 0

    assert capsys.readouterr().out == "smtp\n  primary\n"
    assert calls == [("describe_caps", {})]


def test_client_accounts_defaults_to_list(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        if name == "describe_caps":
            return SimpleNamespace(
                structuredContent={
                    "capabilities": [{"id": "smtp", "accounts": ["primary"]}]
                },
            )
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts"]) == 0

    assert capsys.readouterr().out == "smtp\n  primary\n"


def test_client_lists_accounts_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "describe_caps"
        assert arguments == {}
        return SimpleNamespace(
            structuredContent={
                "capabilities": [{"id": "smtp", "accounts": ["primary"]}]
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "list", "--json"]) == 0

    assert capsys.readouterr().out == '{"accounts": {"smtp": ["primary"]}}\n'


def test_client_accounts_default_list_accepts_json_option(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "describe_caps"
        assert arguments == {}
        return SimpleNamespace(
            structuredContent={
                "capabilities": [{"id": "smtp", "accounts": ["primary"]}]
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "--json"]) == 0

    assert capsys.readouterr().out == '{"accounts": {"smtp": ["primary"]}}\n'


def test_client_describes_account(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "describe_cap"
        assert arguments == {"capability": "smtp"}
        return SimpleNamespace(
            structuredContent={"accounts": {"primary": {"enabled": True}}},
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "desc", "smtp", "primary"]) == 0

    assert capsys.readouterr().out == (
        '{"account": "primary", "capability": "smtp", '
        '"details": {"enabled": true}}\n'
    )


def test_client_describes_account_with_colon_shorthand(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "describe_cap"
        assert arguments == {"capability": "imap"}
        return SimpleNamespace(
            structuredContent={"accounts": {"bot": {"enabled": True}}},
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "desc", "imap:bot"]) == 0

    assert capsys.readouterr().out == (
        '{"account": "bot", "capability": "imap", ' '"details": {"enabled": true}}\n'
    )


def test_client_unwraps_mcp_tool_errors_for_high_level_commands(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "describe_cap"
        assert arguments == {"capability": "imap:bot"}
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=(
                        "Error executing tool describe_cap: "
                        "unknown capability: imap:bot"
                    )
                )
            ],
            isError=True,
            structuredContent=None,
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "desc", "imap:bot"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Arbiter tool error: unknown capability: imap:bot\n"


def test_client_lists_accounts_accepts_plain_payload(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        return {"capabilities": []}

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["accounts", "list"]) == 0

    assert capsys.readouterr().out == ""


def test_client_warns_when_remote_version_differs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "arbiter_core_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.4"))
    )

    assert capsys.readouterr().err == (
        "Arbiter core version warning: local CLI core version 1.2.3 "
        "does not match remote server core version 1.2.4.\n"
    )


@pytest.mark.parametrize("remote_version", ["1.2.3", "unknown", None])
def test_client_does_not_warn_when_remote_version_matches_or_is_unavailable(
    remote_version: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "arbiter_core_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version=remote_version))
    )

    assert capsys.readouterr().err == ""


def test_client_does_not_warn_when_local_version_is_unknown(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "arbiter_core_version", lambda: "unknown")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.4"))
    )

    assert capsys.readouterr().err == ""


def test_client_rejects_non_object_json_args() -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["mcp", "call", "run_op", "--args", "[]"])

    assert exc_info.value.code == 2


def test_client_reports_clean_keyboard_interrupt(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_keyboard_interrupt(*_args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("agent_arbiter.client.anyio.run", raise_keyboard_interrupt)

    assert client.main(["mcp", "tools"]) == 130

    assert capsys.readouterr().err == "Arbiter client stopped.\n"


def test_client_reports_clean_connection_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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

    assert client.main(["mcp", "tools"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter connection error: could not connect to Arbiter at "
        "http://127.0.0.1:8000/mcp "
        f"(built-in default; no client config found at {tmp_path / '.arbiter' / 'arbiter-client.yaml'}). "
        "Is arbiter-server serve running?\n"
    )


def test_client_reports_clean_read_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeExceptionGroup(Exception):
        def __init__(self, exceptions: tuple[BaseException, ...]) -> None:
            super().__init__("unhandled errors in a TaskGroup")
            self.exceptions = exceptions

    def raise_read_error(*_args: object) -> int:
        raise FakeExceptionGroup((httpx.ReadError("connection closed"),))

    monkeypatch.setattr("agent_arbiter.client.anyio.run", raise_read_error)

    assert client.main(["cap"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter connection error: could not connect to Arbiter at "
        "http://127.0.0.1:8000/mcp "
        f"(built-in default; no client config found at {tmp_path / '.arbiter' / 'arbiter-client.yaml'}). "
        "Is arbiter-server serve running?\n"
    )


def test_client_connection_failure_reports_url_from_client_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "local-client.yaml"
    config_file.write_text(
        "arbiter:\n  mcp_url: http://localhost:9011/mcp\n",
        encoding="utf-8",
    )

    def raise_connection_error(*_args: object) -> int:
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr("agent_arbiter.client.anyio.run", raise_connection_error)

    assert (
        client.main(
            [
                "--config-dir",
                str(tmp_path),
                "--config-name",
                "local-client",
                "cap",
            ]
        )
        == 1
    )

    assert capsys.readouterr().err == (
        "Arbiter connection error: could not connect to Arbiter at "
        f"http://localhost:9011/mcp (client config {config_file}). "
        "Is arbiter-server serve running?\n"
    )
