from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent_arbiter import client


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
        client.main(["--url", "http://localhost:9000/mcp", "tools", "list", "--json"])
        == 0
    )

    assert capsys.readouterr().out == (
        '{"tools": [{"description": "List configured accounts.", '
        '"input_schema": {"type": "object"}, "name": "list_accounts"}]}\n'
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
        "Is agent-arbiter serve running?\n"
    )
