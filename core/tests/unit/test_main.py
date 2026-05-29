import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from types import SimpleNamespace
from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from agent_arbiter.config import AppConfig
from agent_arbiter.main import (
    _run_hydra_entrypoint,
    _run_server,
    build_app,
    build_server,
    config_check_summary,
    log_startup_summary,
    main,
    service_plugin_names,
)
from agent_arbiter.plugins import discover_service_plugins
from agent_arbiter_imap import IMAPRuntime, IMAPServicePlugin
from agent_arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFolderConfig,
)
from agent_arbiter_smtp import SendEmailResult, SMTPRuntime, SMTPServicePlugin
from agent_arbiter_smtp.config import SMTPConfig, SMTPServicePolicyConfig
from agent_arbiter.services import (
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    RuntimeRegistry,
    ServicePlugin,
)


def test_build_app_accepts_hydra_config() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp_imap())

    app = build_app(cfg, service_plugins=_test_service_plugins())

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
    cfg = OmegaConf.structured(_app_config_with_smtp())

    app = build_app(cfg, service_plugins=_test_service_plugins())

    assert app.list_accounts() == {
        "smtp": {
            "primary": {
                "description": "Bot-owned account for automated email tasks.",
                "policy": "bot",
                "enabled": True,
                "send": "allowed",
                "require_confirmation": False,
            },
        },
    }


def test_build_app_rejects_unknown_service_policy_reference() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp())
    cfg.accounts.smtp.primary.policy = "missing"

    with pytest.raises(
        ValueError,
        match="SMTP account references an unknown policy: primary -> missing",
    ):
        build_app(cfg, service_plugins=_test_service_plugins())


def test_build_app_activates_dynamic_entry_point_service() -> None:
    class FakeExternalRuntime:
        tool_names = ("send_whatsapp",)

        def account_summaries(self) -> dict[str, object]:
            return {"bot": {"enabled": True}}

    class FakeExternalPlugin:
        name = "whatsapp"

        def __init__(self) -> None:
            self.accounts: Mapping[str, object] | None = None
            self.policies: Mapping[str, object] | None = None

        def register_configs(self, config_store: object) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: object,
        ) -> object:
            self.accounts = accounts
            self.policies = policies
            return FakeExternalRuntime()

        def register_tools(self, server: object, context: object) -> None:
            return None

    plugin = FakeExternalPlugin()
    cfg = OmegaConf.create(
        {
            "server": {},
            "accounts": {
                "whatsapp": {
                    "bot": {
                        "policy": "bot",
                        "phone_number": "+15555550100",
                    }
                }
            },
            "policies": {
                "whatsapp": {
                    "bot": {
                        "allow_send": True,
                    }
                }
            },
            "etc": {},
        }
    )

    app = build_app(cfg, service_plugins=[plugin])

    assert plugin.accounts is not None
    assert plugin.policies is not None
    assert set(plugin.accounts) == {"bot"}
    assert set(plugin.policies) == {"bot"}
    assert app.tool_names() == ["list_accounts", "send_whatsapp"]
    assert app.list_accounts() == {"whatsapp": {"bot": {"enabled": True}}}


def test_discover_service_plugins_loads_entry_point_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        def __init__(self, name: str) -> None:
            self.name = name

        def register_configs(self, config_store: object) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: object,
        ) -> object:
            return object()

        def register_tools(self, server: object, context: object) -> None:
            return None

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
        "agent_arbiter.plugins.entry_points",
        lambda: FakeEntryPoints(
            [
                FakeEntryPoint(smtp_plugin),
                FakeEntryPoint(imap_plugin),
            ]
        ),
    )

    assert [plugin.name for plugin in discover_service_plugins()] == ["imap", "smtp"]


def test_config_check_summary_validates_runtime_construction() -> None:
    assert (
        config_check_summary(
            _app_config_with_smtp_imap(),
            service_plugins=_test_service_plugins(),
        )
        == "config ok: services=smtp,imap service_accounts=smtp:primary;imap:primary"
    )


def test_service_plugin_names_are_sorted() -> None:
    assert service_plugin_names(service_plugins=_test_service_plugins()) == [
        "imap",
        "smtp",
    ]


def test_cli_lists_plugins(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["plugins", "list"]) == 0

    assert capsys.readouterr().out == "imap\nsmtp\n"


def test_server_cli_help_uses_arbiter_server_program_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0

    assert capsys.readouterr().out.startswith("usage: arbiter-server ")


def test_server_cli_reports_clean_keyboard_interrupt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raise_keyboard_interrupt() -> None:
        raise KeyboardInterrupt

    assert _run_hydra_entrypoint(raise_keyboard_interrupt, []) == 130

    assert capsys.readouterr().err == "Agent Arbiter server stopped.\n"


def test_cli_lists_plugins_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["plugins", "list", "--json"]) == 0

    assert capsys.readouterr().out == (
        '{"plugins": [{"name": "imap"}, {"name": "smtp"}]}\n'
    )


def test_cli_routes_legacy_hydra_args_to_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_calls: list[list[str]] = []

    def fake_serve(args: Sequence[str]) -> int:
        serve_calls.append(list(args))
        return 0

    monkeypatch.setattr("agent_arbiter.main._run_serve", fake_serve)

    assert main(["--config-path", "/tmp", "--config-name", "agent-arbiter-local"]) == 0
    assert main(["server.port=8025"]) == 0

    assert serve_calls == [
        ["--config-path", "/tmp", "--config-name", "agent-arbiter-local"],
        ["server.port=8025"],
    ]


def test_cli_serve_subcommand_passes_hydra_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_calls: list[list[str]] = []

    def fake_serve(args: Sequence[str]) -> int:
        serve_calls.append(list(args))
        return 0

    monkeypatch.setattr("agent_arbiter.main._run_serve", fake_serve)

    assert main(["serve", "--config-path", "/tmp"]) == 0

    assert serve_calls == [["--config-path", "/tmp"]]


def test_cli_config_check_subcommand_passes_hydra_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_calls: list[list[str]] = []

    def fake_check(args: Sequence[str]) -> int:
        check_calls.append(list(args))
        return 0

    monkeypatch.setattr("agent_arbiter.main._run_config_check", fake_check)

    assert main(["config", "check", "--config-path", "/tmp"]) == 0

    assert check_calls == [["--config-path", "/tmp"]]


def _test_service_plugins() -> list[ServicePlugin]:
    return [
        SMTPServicePlugin(),
        IMAPServicePlugin(),
    ]


def _app_config_with_smtp() -> AppConfig:
    return AppConfig(
        accounts={
            "smtp": {
                "primary": SMTPConfig(
                    description="Bot-owned account for automated email tasks.",
                    policy="bot",
                )
            },
            "imap": {},
        },
        policies={
            "smtp": {"bot": SMTPServicePolicyConfig(require_confirmation=False)},
            "imap": {},
        },
    )


def _app_config_with_smtp_imap() -> AppConfig:
    return AppConfig(
        accounts={
            "smtp": {
                "primary": SMTPConfig(
                    description="Bot-owned account for automated email tasks.",
                    policy="bot",
                )
            },
            "imap": {
                "primary": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            },
        },
        policies={
            "smtp": {"bot": SMTPServicePolicyConfig(require_confirmation=False)},
            "imap": {"bot": IMAPAccessPolicyConfig()},
        },
    )


def test_log_startup_summary_includes_safe_runtime_context(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _app_config_with_smtp()
    cast(SMTPConfig, cfg.accounts["smtp"]["primary"]).password = "super-secret"

    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "1.2.3")
    caplog.set_level(logging.INFO, logger="agent_arbiter.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "Agent Arbiter starting version=1.2.3" in message
    assert "transport=streamable-http" in message
    assert "bind=127.0.0.1:8000/mcp" in message
    assert "services=smtp" in message
    assert "service_accounts=smtp:primary" in message
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
    fake_cfg = _app_config_with_smtp_imap()
    smtp_accounts = fake_cfg.accounts["smtp"]
    smtp_policies = fake_cfg.policies["smtp"]
    imap_accounts = fake_cfg.accounts["imap"]
    imap_policies = fake_cfg.policies["imap"]

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
                    accounts=smtp_accounts,
                    policies=smtp_policies,
                    smtp_client_factory=lambda config: cast(Any, object()),
                ),
                "imap": FakeIMAPRuntime(
                    accounts=imap_accounts,
                    policies=imap_policies,
                ),
            }
        )

        def list_accounts(self) -> dict[str, object]:
            nonlocal list_accounts_calls
            list_accounts_calls += 1
            return {
                "imap": {
                    "primary": {
                        "description": "Primary account",
                        "policy": "bot",
                        "enabled": True,
                    },
                },
                "smtp": {
                    "primary": {
                        "description": "Primary account",
                        "policy": "bot",
                        "enabled": True,
                        "send": "allowed",
                        "require_confirmation": False,
                    },
                },
            }

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
    monkeypatch.setattr(
        "agent_arbiter.main.build_app",
        lambda cfg, service_plugins=None, runtime_dependencies=None: FakeApp(),
    )

    cfg = OmegaConf.structured(fake_cfg)

    server = cast(Any, build_server(cfg, service_plugins=_test_service_plugins()))

    assert server.name == "agent-arbiter"
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
        "accounts": {
            "imap": {
                "primary": {
                    "description": "Primary account",
                    "policy": "bot",
                    "enabled": True,
                },
            },
            "smtp": {
                "primary": {
                    "description": "Primary account",
                    "policy": "bot",
                    "enabled": True,
                    "send": "allowed",
                    "require_confirmation": False,
                },
            },
        }
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


@pytest.mark.parametrize(
    ("transport", "expected_app"),
    [
        ("streamable-http", "streamable-http-app"),
        ("sse", "sse-app"),
    ],
)
def test_run_server_preserves_hydra_logging_for_uvicorn_transports(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    expected_app: str,
) -> None:
    captured: dict[str, object] = {}

    class FakeUvicornConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            captured["app"] = app
            captured["config_kwargs"] = kwargs

    class FakeUvicornServer:
        def __init__(self, config: FakeUvicornConfig) -> None:
            captured["config"] = config

        async def serve(self) -> None:
            captured["served"] = True

    fake_uvicorn = ModuleType("uvicorn")
    setattr(fake_uvicorn, "Config", FakeUvicornConfig)
    setattr(fake_uvicorn, "Server", FakeUvicornServer)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    def streamable_http_app() -> str:
        return "streamable-http-app"

    def sse_app(mount_path: str | None) -> str:
        captured["sse_mount_path"] = mount_path
        return "sse-app"

    fake_server = SimpleNamespace(
        settings=SimpleNamespace(host="127.0.0.1", port=8025, log_level="INFO"),
        streamable_http_app=streamable_http_app,
        sse_app=sse_app,
    )

    _run_server(cast(Any, fake_server), cast(Any, transport))

    assert captured["app"] == expected_app
    assert captured["served"] is True
    assert captured["config_kwargs"] == {
        "host": "127.0.0.1",
        "port": 8025,
        "log_level": "info",
        "log_config": None,
    }
    if transport == "sse":
        assert captured["sse_mount_path"] is None


def test_run_server_keeps_stdio_on_fastmcp_runner() -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.transport = ""

        def run(self, *, transport: str) -> None:
            self.transport = transport

    fake_server = FakeServer()

    _run_server(cast(Any, fake_server), "stdio")

    assert fake_server.transport == "stdio"


def test_build_server_describes_send_email_tool_schema() -> None:
    server = cast(
        Any,
        build_server(
            OmegaConf.structured(_app_config_with_smtp_imap()),
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
