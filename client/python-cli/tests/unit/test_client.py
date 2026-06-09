from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from arbiter_python_client import client


@pytest.fixture(autouse=True)
def isolate_client_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ARBITER_MCP_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(client, "_STAGED_DEPLOYMENT_WARNING_EMITTED", False)


def test_client_help_uses_arbiter_py_program_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: arbiter-py ")
    assert "--config-name" in output
    assert "--version" in output
    assert "bootstrap" in output


def test_client_without_args_prints_short_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main([]) == 2

    assert capsys.readouterr().out == (
        "usage: arbiter-py {info,op,mcp} ...\n"
        "Run 'arbiter-py --help' for full help.\n"
    )


def test_client_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        client.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("arbiter-py ")


def test_client_info_summarizes_server_plugins_and_accounts(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Mapping[str, Any]]] = []

    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://localhost:18025/mcp"
        calls.append((name, arguments))
        if name == "info":
            assert arguments == {"kind": "overview"}
            return SimpleNamespace(
                structuredContent={
                    "kind": "overview",
                    "deployment_scope": "staged",
                    "plugins": [
                        {
                            "id": "imap",
                            "version": "0.9.0",
                            "description": (
                                "Read and manage mail through configured IMAP "
                                "accounts."
                            ),
                            "account_count": 1,
                            "operation_count": 6,
                            "accounts": [
                                {
                                    "plugin": "imap",
                                    "name": "bot",
                                    "description": "",
                                    "guidance": "",
                                }
                            ],
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "info",
                "arbiter.mcp_url=http://localhost:18025/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == (
        '{"deployment_scope": "staged", "kind": "overview", "plugins": '
        '[{"account_count": 1, "accounts": [{"description": "", "guidance": "", '
        '"name": "bot", "plugin": "imap"}], "description": "Read and manage mail '
        'through configured IMAP accounts.", "id": "imap", "operation_count": 6, '
        '"version": "0.9.0"}], "server_url": "http://localhost:18025/mcp"}\n'
    )
    assert calls == [
        ("info", {"kind": "overview"}),
    ]


def test_client_info_short_summarizes_plugin_accounts(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://localhost:18025/mcp"
        assert name == "info"
        assert arguments == {"kind": "overview"}
        return SimpleNamespace(
            structuredContent={
                "kind": "overview",
                "plugins": [
                    {
                        "id": "imap",
                        "description": "Read mail",
                        "accounts": [
                            {"name": "bot", "description": "Bot mailbox"},
                            {"name": "personal"},
                        ],
                    },
                    {
                        "id": "smtp",
                        "description": "Send mail",
                        "accounts": [
                            {"name": "bot", "description": "Bot sender"},
                        ],
                    },
                ],
                "operations": [{"id": "imap:get_message"}],
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert (
        client.main(
            [
                "info",
                "--short",
                "arbiter.mcp_url=http://localhost:18025/mcp",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == (
        '{"accounts": [{"description": "Bot mailbox", "id": "imap:bot"}, '
        '{"id": "imap:personal"}, {"description": "Bot sender", '
        '"id": "smtp:bot"}], "kind": "overview_short", '
        '"server_url": "http://localhost:18025/mcp"}\n'
    )


def test_client_info_short_accepts_trailing_yaml(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "info"
        assert arguments == {"kind": "overview"}
        return SimpleNamespace(
            structuredContent={
                "kind": "overview",
                "plugins": [
                    {
                        "id": "imap",
                        "accounts": [
                            {"name": "bot", "description": "Bot mailbox"},
                        ],
                    },
                ],
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "--short", "--yaml"]) == 0

    assert capsys.readouterr().out == (
        "kind: overview_short\n"
        "server_url: http://127.0.0.1:8000/mcp\n"
        "accounts:\n"
        "- id: imap:bot\n"
        "  description: Bot mailbox\n"
    )


def test_client_info_short_rejects_subcommands(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        raise AssertionError("info --short subcommands should not call the server")

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "plugin", "smtp", "--short"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "info --short is only valid for overview" in captured.err


def test_client_artifact_get_requires_explicit_destination(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["artifact", "get", "http://artifact.test/file"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "artifact get requires exactly one of --stdout or --output PATH" in captured.err
    )


def test_client_artifact_get_saves_binary_artifact_to_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []
    body = b"%PDF\x00\xff"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        assert request.method == "GET"
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "application/pdf",
                "content-length": str(len(body)),
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def fake_http_client(*args: Any, **kwargs: Any) -> httpx.Client:
        return original_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_http_client)
    output_path = tmp_path / "attachment.pdf"

    assert (
        client.main(
            [
                "artifact",
                "get",
                "http://artifact.test/file",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    assert output_path.read_bytes() == body
    if client.os.name != "nt" and stat.S_IMODE(output_path.stat().st_mode) != 0o600:
        pytest.fail(f"unexpected artifact file mode: {output_path.stat().st_mode:o}")
    assert requests == [("GET", "http://artifact.test/file")]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_client_artifact_get_removes_output_when_close_fails(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, content=b"partial")

    class FailingClose:
        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped
            self.closed = False

        def write(self, data: bytes) -> object:
            return self._wrapped.write(data)

        def close(self) -> None:
            if not self.closed:
                self._wrapped.close()
                self.closed = True
            raise OSError("simulated close failure")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    original_fdopen = client.os.fdopen

    def fake_http_client(*args: Any, **kwargs: Any) -> httpx.Client:
        return original_client(*args, transport=transport, **kwargs)

    def fake_fdopen(fd: int, mode: str) -> FailingClose:
        return FailingClose(original_fdopen(fd, mode))

    monkeypatch.setattr(httpx, "Client", fake_http_client)
    monkeypatch.setattr(client.os, "fdopen", fake_fdopen)
    output_path = tmp_path / "attachment.pdf"

    assert (
        client.main(
            [
                "artifact",
                "get",
                "http://artifact.test/file",
                "--output",
                str(output_path),
            ]
        )
        == 1
    )

    assert requests == [("GET", "http://artifact.test/file")]
    assert not output_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "simulated close failure" in captured.err


def test_client_info_plugin_subcommand_calls_info_tool(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "info"
        assert arguments == {"kind": "plugin", "plugin": "smtp"}
        return SimpleNamespace(structuredContent={"kind": "plugin", "id": "smtp"})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "plugin", "smtp"]) == 0

    assert capsys.readouterr().out == (
        '{"id": "smtp", "kind": "plugin", '
        '"server_url": "http://127.0.0.1:8000/mcp"}\n'
    )


def test_client_info_account_subcommand_accepts_yaml(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "info"
        assert arguments == {
            "kind": "account",
            "plugin": "smtp",
            "account": "bot",
        }
        return SimpleNamespace(
            structuredContent={
                "kind": "account",
                "plugin": "smtp",
                "account": "bot",
                "description": "Support sender",
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "--yaml", "account", "smtp", "bot"]) == 0

    assert capsys.readouterr().out == (
        "server_url: http://127.0.0.1:8000/mcp\n"
        "kind: account\n"
        "plugin: smtp\n"
        "account: bot\n"
        "description: Support sender\n"
    )


def test_client_info_tests_subcommand_calls_info_tool(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "info"
        assert arguments == {"kind": "tests"}
        return SimpleNamespace(structuredContent={"kind": "tests", "plugins": []})

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "tests"]) == 0

    assert capsys.readouterr().out == (
        '{"kind": "tests", "plugins": [], '
        '"server_url": "http://127.0.0.1:8000/mcp"}\n'
    )


def test_client_info_test_subcommand_accepts_account(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "info"
        assert arguments == {
            "kind": "test",
            "plugin": "smtp",
            "account": "bot",
        }
        return SimpleNamespace(
            structuredContent={
                "kind": "test",
                "plugin": "smtp",
                "account": "bot",
                "status": "ok",
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "test", "smtp", "bot"]) == 0

    assert capsys.readouterr().out == (
        '{"account": "bot", "kind": "test", "plugin": "smtp", '
        '"server_url": "http://127.0.0.1:8000/mcp", "status": "ok"}\n'
    )


def test_client_info_tests_reports_stale_server_support(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert name == "info"
        assert arguments == {"kind": "tests"}
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=(
                        "Error executing tool info: unknown info kind: tests; "
                        "supported kinds: account, accounts, op, ops, overview, "
                        "plugin, plugins"
                    )
                )
            ],
            isError=True,
            structuredContent=None,
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "tests"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Arbiter tool error: unknown info kind: tests; supported kinds: account, "
        "accounts, op, ops, overview, plugin, plugins\n"
        "  The local Arbiter client understands 'info tests', but the server at "
        "http://127.0.0.1:8000/mcp does not. This usually means the running "
        "server is older than the client or was not restarted after updating "
        "the wheelhouse. Rebuild/redeploy the server package and restart the "
        "Arbiter service, then retry the command.\n"
    )


def test_client_info_op_subcommand_accepts_trailing_yaml(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        assert url == "http://127.0.0.1:8000/mcp"
        assert name == "info"
        assert arguments == {
            "kind": "op",
            "plugin": "smtp",
            "operation": "send_email",
        }
        return SimpleNamespace(
            structuredContent={
                "kind": "op",
                "plugin": "smtp",
                "operation": "send_email",
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["info", "op", "smtp", "send_email", "--yaml"]) == 0

    assert capsys.readouterr().out == (
        "server_url: http://127.0.0.1:8000/mcp\n"
        "kind: op\n"
        "plugin: smtp\n"
        "operation: send_email\n"
    )


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


def test_client_cap_list_accepts_fields_query(
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
                "capabilities": [
                    {
                        "id": "smtp",
                        "description": "Send email.",
                        "version": "0.9.0",
                        "account_count": 2,
                    }
                ]
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "fields=desc,version,num_accts"]) == 0

    assert capsys.readouterr().out == "smtp\tSend email.\t0.9.0\t2\n"


def test_client_cap_list_accepts_quoted_bracket_fields_query_as_json(
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
                "capabilities": [
                    {
                        "id": "smtp",
                        "description": "Send email.",
                        "version": "0.9.0",
                        "account_count": 2,
                    }
                ]
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "--json", "fields=[desc,version,num_accts]"]) == 0

    assert capsys.readouterr().out == (
        '{"capabilities": [{"desc": "Send email.", "id": "smtp", '
        '"num_accts": 2, "version": "0.9.0"}]}\n'
    )


def test_client_cap_list_accepts_format_query(
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
                "capabilities": [
                    {
                        "id": "smtp",
                        "description": "Send email.",
                        "version": "0.9.0",
                    }
                ]
            },
        )

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "format={id}=={version}: {desc}"]) == 0

    assert capsys.readouterr().out == "smtp==0.9.0: Send email.\n"


def test_client_cap_list_format_falls_back_to_version_info(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Mapping[str, Any]]] = []

    async def fake_call_tool(
        url: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        calls.append((name, arguments))
        if name == "describe_caps":
            assert arguments == {}
            return SimpleNamespace(
                structuredContent={
                    "capabilities": [
                        {
                            "id": "smtp",
                            "description": "Send email.",
                        }
                    ]
                },
            )
        if name == "version_info":
            assert arguments == {}
            return SimpleNamespace(
                structuredContent={
                    "plugins": [
                        {
                            "name": "smtp",
                            "version": "0.9.0",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(client, "call_tool", fake_call_tool)

    assert client.main(["cap", "format={id}=={version}: {desc}"]) == 0

    assert capsys.readouterr().out == "smtp==0.9.0: Send email.\n"
    assert calls == [("describe_caps", {}), ("version_info", {})]


def test_client_cap_list_rejects_unknown_format_field(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["cap", "format={bogus}"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "Arbiter usage error: unsupported cap format field: bogus; "
        "supported fields: "
    ) in captured.err


def test_client_cap_list_rejects_unknown_fields_query(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.main(["cap", "fields=bogus"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "Arbiter usage error: unsupported cap list field: bogus; " "supported fields: "
    ) in captured.err


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
    monkeypatch.setattr(client, "arbiter_python_client_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.4"))
    )

    assert capsys.readouterr().err == (
        "Arbiter server version warning: local Python client version 1.2.3 "
        "does not match remote server server version 1.2.4.\n"
    )


def test_client_warns_when_connected_to_staged_deployment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client._warn_if_staged_deployment(
        {"deployment_scope": "staged"},
        "http://127.0.0.1:8025/mcp",
    )

    assert capsys.readouterr().err == (
        "Heads up: connected to staged Arbiter at http://127.0.0.1:8025/mcp.\n"
    )


def test_client_emits_staged_deployment_warning_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client._warn_if_staged_deployment(
        {"deployment_scope": "staged"},
        "http://127.0.0.1:8025/mcp",
    )
    client._warn_if_staged_deployment(
        {"deployment_scope": "staged"},
        "http://127.0.0.1:8025/mcp",
    )

    assert capsys.readouterr().err == (
        "Heads up: connected to staged Arbiter at http://127.0.0.1:8025/mcp.\n"
    )


@pytest.mark.parametrize(
    "version_info",
    [
        {"deployment_scope": "installed"},
        {"deployment_scope": "unknown"},
        {},
        None,
    ],
)
def test_client_does_not_warn_for_non_staged_deployment(
    version_info: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client._warn_if_staged_deployment(version_info, "http://127.0.0.1:8025/mcp")

    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("remote_version", ["1.2.3", "unknown", None])
def test_client_does_not_warn_when_remote_version_matches_or_is_unavailable(
    remote_version: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "arbiter_python_client_version", lambda: "1.2.3")

    client._warn_if_remote_version_mismatch(
        SimpleNamespace(serverInfo=SimpleNamespace(version=remote_version))
    )

    assert capsys.readouterr().err == ""


def test_client_does_not_warn_when_local_version_is_unknown(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "arbiter_python_client_version", lambda: "unknown")

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

    monkeypatch.setattr(
        "arbiter_python_client.client.anyio.run",
        raise_keyboard_interrupt,
    )

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

    monkeypatch.setattr(
        "arbiter_python_client.client.anyio.run",
        raise_connection_error,
    )

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

    monkeypatch.setattr("arbiter_python_client.client.anyio.run", raise_read_error)

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

    monkeypatch.setattr(
        "arbiter_python_client.client.anyio.run",
        raise_connection_error,
    )

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
