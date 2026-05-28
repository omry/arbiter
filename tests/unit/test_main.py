import logging
import sys
from collections.abc import Callable
from types import ModuleType
from types import SimpleNamespace
from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from mail_sentry.config import AppConfig
from mail_sentry.main import build_app, build_server, log_startup_summary
from mail_sentry.plugins import discover_service_plugins
from mail_sentry.plugins.imap import IMAPRuntime, IMAPServicePlugin
from mail_sentry.plugins.smtp import SendEmailResult, SMTPRuntime, SMTPServicePlugin
from mail_sentry.services import (
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    RuntimeRegistry,
    ServicePlugin,
)


def test_build_app_accepts_hydra_config() -> None:
    cfg = OmegaConf.structured(AppConfig())

    app = build_app(cfg)

    assert app.tool_names() == [
        "list_accounts",
        "send_email",
        "list_messages",
        "get_message",
        "search_messages",
        "move_message",
        "mark_message_read",
        "delete_message",
    ]


def test_build_app_list_accounts_uses_real_config_shape() -> None:
    cfg = OmegaConf.structured(AppConfig())

    app = build_app(cfg)

    assert app.list_accounts() == [
        {
            "name": "primary",
            "description": "Bot-owned account for automated email tasks.",
            "account_access_profile": "bot",
            "smtp": {
                "send": "allowed",
                "require_confirmation": False,
            },
            "imap": {
                "enabled": False,
            },
        }
    ]


def test_discover_service_plugins_loads_entry_point_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        def __init__(self, name: str) -> None:
            self.name = name

    smtp_plugin = FakePlugin("smtp")
    imap_plugin = FakePlugin("imap")

    class FakeEntryPoint:
        def __init__(self, plugin: FakePlugin) -> None:
            self._plugin = plugin

        def load(self) -> Callable[[], FakePlugin]:
            return lambda: self._plugin

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> "FakeEntryPoints":
            assert group == SERVICE_PLUGIN_ENTRY_POINT_GROUP
            return self

    monkeypatch.setattr(
        "mail_sentry.plugins.entry_points",
        lambda: FakeEntryPoints(
            [
                FakeEntryPoint(smtp_plugin),
                FakeEntryPoint(imap_plugin),
            ]
        ),
    )

    assert [plugin.name for plugin in discover_service_plugins()] == ["imap", "smtp"]


def _test_service_plugins() -> list[ServicePlugin]:
    return [
        SMTPServicePlugin(),
        IMAPServicePlugin(),
    ]


def test_log_startup_summary_includes_safe_runtime_context(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig()
    assert cfg.mail.accounts["primary"].smtp is not None
    cfg.mail.accounts["primary"].smtp.password = "super-secret"

    monkeypatch.setattr("mail_sentry.main.package_version", lambda: "1.2.3")
    caplog.set_level(logging.INFO, logger="mail_sentry.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "Mail Sentry starting version=1.2.3" in message
    assert "transport=streamable-http" in message
    assert "bind=127.0.0.1:8000/mcp" in message
    assert "accounts=primary" in message
    assert "smtp_accounts=primary" in message
    assert "imap_accounts=none" in message
    assert "super-secret" not in message
    assert "agent@example.com" not in message


def test_build_server_registers_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    tools: dict[str, Callable[..., object]] = {}
    list_accounts_calls = 0
    send_email_calls: list[dict[str, object]] = []
    list_messages_calls: list[dict[str, object]] = []
    get_message_calls: list[dict[str, object]] = []
    search_messages_calls: list[dict[str, object]] = []
    move_message_calls: list[dict[str, object]] = []
    mark_message_read_calls: list[dict[str, object]] = []
    delete_message_calls: list[dict[str, object]] = []

    class FakeSMTPRuntime(SMTPRuntime):
        def send_email(
            self,
            account: str,
            to: list[str],
            subject: str,
            text_body: str | None = None,
            html_body: str | None = None,
            cc: list[str] | None = None,
            bcc: list[str] | None = None,
        ) -> SendEmailResult:
            send_email_calls.append(
                {
                    "account": account,
                    "to": to,
                    "subject": subject,
                    "text_body": text_body,
                    "html_body": html_body,
                    "cc": cc,
                    "bcc": bcc,
                }
            )
            return SendEmailResult(
                tool="send_email",
                message_id="<message-id@example.com>",
                recipient_count=len(to) + len(cc or []) + len(bcc or []),
            )

    class FakeIMAPRuntime(IMAPRuntime):
        def list_messages(
            self,
            account: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            list_messages_calls.append(
                {"account": account, "folder": folder, "limit": limit}
            )
            return {"account": account, "folder": folder or "INBOX", "messages": []}

        def get_message(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            get_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"account": account, "folder": folder or "INBOX", "message": {}}

        def search_messages(
            self,
            account: str,
            query: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            search_messages_calls.append(
                {
                    "account": account,
                    "query": query,
                    "folder": folder,
                    "limit": limit,
                }
            )
            return {
                "account": account,
                "folder": folder or "INBOX",
                "query": query,
                "messages": [],
            }

        def move_message(
            self,
            account: str,
            message_id: str,
            destination_folder: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            move_message_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "destination_folder": destination_folder,
                    "folder": folder,
                }
            )
            return {"ok": True}

        def mark_message_read(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
            read: bool = True,
        ) -> dict[str, object]:
            mark_message_read_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "folder": folder,
                    "read": read,
                }
            )
            return {"ok": True}

        def delete_message(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            delete_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"ok": True}

    class FakeApp:
        runtime_registry = RuntimeRegistry(
            {
                "smtp": FakeSMTPRuntime(
                    AppConfig().mail,
                    smtp_client_factory=lambda config: cast(Any, object()),
                ),
                "imap": FakeIMAPRuntime(AppConfig().mail),
            }
        )

        def list_accounts(self) -> list[dict[str, object]]:
            nonlocal list_accounts_calls
            list_accounts_calls += 1
            return [
                {
                    "name": "primary",
                    "description": "Primary account",
                    "account_access_profile": "bot",
                    "smtp": {
                        "send": "allowed",
                        "require_confirmation": False,
                    },
                    "imap": {
                        "enabled": False,
                    },
                }
            ]

        def send_email(
            self,
            *,
            account: str,
            to: list[str],
            subject: str,
            text_body: str | None = None,
            html_body: str | None = None,
            cc: list[str] | None = None,
            bcc: list[str] | None = None,
        ) -> SimpleNamespace:
            send_email_calls.append(
                {
                    "account": account,
                    "to": to,
                    "subject": subject,
                    "text_body": text_body,
                    "html_body": html_body,
                    "cc": cc,
                    "bcc": bcc,
                }
            )
            return SimpleNamespace(
                message_id="<message-id@example.com>",
                recipient_count=len(to) + len(cc or []) + len(bcc or []),
            )

        def list_messages(
            self,
            *,
            account: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            list_messages_calls.append(
                {"account": account, "folder": folder, "limit": limit}
            )
            return {"account": account, "folder": folder or "INBOX", "messages": []}

        def get_message(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            get_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"account": account, "folder": folder or "INBOX", "message": {}}

        def search_messages(
            self,
            *,
            account: str,
            query: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            search_messages_calls.append(
                {
                    "account": account,
                    "query": query,
                    "folder": folder,
                    "limit": limit,
                }
            )
            return {
                "account": account,
                "folder": folder or "INBOX",
                "query": query,
                "messages": [],
            }

        def move_message(
            self,
            *,
            account: str,
            message_id: str,
            destination_folder: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            move_message_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "destination_folder": destination_folder,
                    "folder": folder,
                }
            )
            return {"ok": True}

        def mark_message_read(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
            read: bool = True,
        ) -> dict[str, object]:
            mark_message_read_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "folder": folder,
                    "read": read,
                }
            )
            return {"ok": True}

        def delete_message(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            delete_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"ok": True}

    class FakeFastMCP:
        def __init__(
            self,
            name: str,
            *,
            stateless_http: bool,
            json_response: bool,
        ) -> None:
            self.name = name
            self.stateless_http = stateless_http
            self.json_response = json_response
            self.settings = SimpleNamespace(
                host="",
                port=0,
                streamable_http_path="",
            )
            self.run_transport = ""

        def tool(
            self, **kwargs: object
        ) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(func: Callable[..., object]) -> Callable[..., object]:
                tools[func.__name__] = func
                return func

            return decorator

        def run(self, *, transport: str) -> None:
            self.run_transport = transport

    fastmcp_module = ModuleType("mcp.server.fastmcp")
    setattr(fastmcp_module, "FastMCP", FakeFastMCP)
    server_module = ModuleType("mcp.server")
    mcp_module = ModuleType("mcp")

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    monkeypatch.setattr("mail_sentry.main.build_app", lambda cfg: FakeApp())

    cfg = OmegaConf.structured(AppConfig())

    server = cast(Any, build_server(cfg, service_plugins=_test_service_plugins()))

    assert server.name == "mail-sentry"
    assert server.stateless_http is True
    assert server.json_response is True
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.streamable_http_path == "/mcp"
    assert "list_accounts" in tools
    assert "send_email" in tools
    assert "list_messages" in tools
    assert "get_message" in tools
    assert "search_messages" in tools
    assert "move_message" in tools
    assert "mark_message_read" in tools
    assert "delete_message" in tools

    list_result = tools["list_accounts"]()

    assert list_result == {
        "accounts": [
            {
                "name": "primary",
                "description": "Primary account",
                "account_access_profile": "bot",
                "smtp": {
                    "send": "allowed",
                    "require_confirmation": False,
                },
                "imap": {
                    "enabled": False,
                },
            }
        ]
    }
    assert list_accounts_calls == 1

    send_result = tools["send_email"](
        account="primary",
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert send_result == {
        "ok": True,
        "message_id": "<message-id@example.com>",
        "recipient_count": 3,
    }
    assert send_email_calls == [
        {
            "account": "primary",
            "to": ["to@example.com"],
            "subject": "Hello",
            "text_body": "Plain body",
            "html_body": None,
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
        }
    ]

    assert tools["list_messages"](account="primary", folder="INBOX", limit=5) == {
        "account": "primary",
        "folder": "INBOX",
        "messages": [],
    }
    assert list_messages_calls == [
        {"account": "primary", "folder": "INBOX", "limit": 5}
    ]

    assert tools["get_message"](account="primary", folder="INBOX", message_id="42") == {
        "account": "primary",
        "folder": "INBOX",
        "message": {},
    }
    assert get_message_calls == [
        {"account": "primary", "message_id": "42", "folder": "INBOX"}
    ]

    assert tools["search_messages"](
        account="primary", query="invoice", folder="INBOX", limit=10
    ) == {
        "account": "primary",
        "folder": "INBOX",
        "query": "invoice",
        "messages": [],
    }
    assert search_messages_calls == [
        {
            "account": "primary",
            "query": "invoice",
            "folder": "INBOX",
            "limit": 10,
        }
    ]

    assert tools["move_message"](
        account="primary",
        message_id="42",
        destination_folder="Archive",
        folder="INBOX",
    ) == {"ok": True}
    assert move_message_calls == [
        {
            "account": "primary",
            "message_id": "42",
            "destination_folder": "Archive",
            "folder": "INBOX",
        }
    ]

    assert tools["mark_message_read"](
        account="primary", message_id="42", folder="INBOX", read=False
    ) == {"ok": True}
    assert mark_message_read_calls == [
        {
            "account": "primary",
            "message_id": "42",
            "folder": "INBOX",
            "read": False,
        }
    ]

    assert tools["delete_message"](
        account="primary", message_id="42", folder="INBOX"
    ) == {"ok": True}
    assert delete_message_calls == [
        {"account": "primary", "message_id": "42", "folder": "INBOX"}
    ]


def test_build_server_describes_send_email_tool_schema() -> None:
    server = cast(
        Any,
        build_server(
            OmegaConf.structured(AppConfig()),
            service_plugins=_test_service_plugins(),
        ),
    )

    list_accounts_tool = server._tool_manager._tools["list_accounts"]
    assert (
        "configured accounts available to the caller" in list_accounts_tool.description
    )
    assert list_accounts_tool.parameters["properties"] == {}

    send_email_tool = server._tool_manager._tools["send_email"]
    parameters = send_email_tool.parameters["properties"]

    assert "selected account" in send_email_tool.description
    assert parameters["account"]["type"] == "string"
    assert parameters["account"]["description"] == (
        "Configured account name returned by list_accounts. The selected account "
        "must have SMTP enabled."
    )
    assert parameters["account"]["examples"] == ["primary"]
    assert parameters["to"]["type"] == "array"
    assert parameters["to"]["description"] == "JSON array of recipient email addresses."
    assert parameters["to"]["examples"] == [["to@example.com"]]
    assert parameters["subject"]["type"] == "string"
    assert parameters["subject"]["description"] == "Email subject line."
    assert parameters["text_body"]["description"] == (
        "Optional plain-text body. Provide this or html_body."
    )
    assert parameters["html_body"]["description"] == (
        "Optional HTML body. Provide this or text_body."
    )
    assert parameters["cc"]["description"] == (
        "Optional JSON array of recipient email addresses."
    )
    assert parameters["bcc"]["description"] == (
        "Optional JSON array of recipient email addresses."
    )

    list_messages_tool = server._tool_manager._tools["list_messages"]
    list_messages_parameters = list_messages_tool.parameters["properties"]
    assert "List recent messages" in list_messages_tool.description
    assert list_messages_parameters["account"]["type"] == "string"
    assert list_messages_parameters["folder"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert list_messages_parameters["limit"]["minimum"] == 1
    assert list_messages_parameters["limit"]["maximum"] == 100

    get_message_parameters = server._tool_manager._tools["get_message"].parameters[
        "properties"
    ]
    assert get_message_parameters["message_id"]["type"] == "string"

    search_messages_parameters = server._tool_manager._tools[
        "search_messages"
    ].parameters["properties"]
    assert search_messages_parameters["query"]["type"] == "string"

    move_message_parameters = server._tool_manager._tools["move_message"].parameters[
        "properties"
    ]
    assert move_message_parameters["destination_folder"]["type"] == "string"
