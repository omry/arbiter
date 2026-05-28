from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, cast

import hydra

from .app import MailSentryApp
from .config import AppConfig, register_configs
from .imap import IMAPClient
from .plugins import discover_service_plugins
from .services import ServicePlugin, ServicePluginContext
from .smtp import SMTPSubmissionClient

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


LOGGER = logging.getLogger(__name__)
TransportMode = Literal["stdio", "sse", "streamable-http"]


def build_app(cfg: AppConfig) -> MailSentryApp:
    return MailSentryApp(
        cfg.mail,
        smtp_client_factory=SMTPSubmissionClient,
        imap_client_factory=IMAPClient,
    )


def package_version() -> str:
    try:
        return version("mail-sentry")
    except PackageNotFoundError:
        return "unknown"


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def log_startup_summary(cfg: AppConfig) -> None:
    accounts = sorted(cfg.mail.accounts)
    smtp_accounts = [
        account_name
        for account_name in accounts
        if cfg.mail.accounts[account_name].smtp is not None
    ]
    imap_accounts = [
        account_name
        for account_name in accounts
        if cfg.mail.accounts[account_name].imap is not None
    ]

    LOGGER.info(
        "Mail Sentry starting version=%s transport=%s bind=%s:%s%s "
        "accounts=%s smtp_accounts=%s imap_accounts=%s",
        package_version(),
        cfg.server.transport,
        cfg.server.host,
        cfg.server.port,
        cfg.server.path,
        _csv_or_none(accounts),
        _csv_or_none(smtp_accounts),
        _csv_or_none(imap_accounts),
    )


def _register_core_tools(server: "FastMCP", app: MailSentryApp) -> None:
    @server.tool(
        description=(
            "Return the configured accounts available to the caller, along with "
            "lightweight metadata needed to choose an account for later SMTP or "
            "IMAP operations."
        )
    )
    def list_accounts() -> dict[str, object]:
        return {
            "accounts": app.list_accounts(),
        }


def _register_service_plugins(
    server: "FastMCP",
    app: MailSentryApp,
    service_plugins: Sequence[ServicePlugin],
) -> None:
    context = ServicePluginContext(runtimes=app.runtime_registry)
    for service_plugin in service_plugins:
        service_plugin.register_tools(server, context)


def build_server(
    cfg: AppConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    app = build_app(cfg)
    server = FastMCP(
        cfg.server.name,
        stateless_http=cfg.server.stateless_http,
        json_response=cfg.server.json_response,
    )
    server.settings.host = cfg.server.host
    server.settings.port = cfg.server.port
    server.settings.streamable_http_path = cfg.server.path

    active_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    _register_core_tools(server, app)
    _register_service_plugins(server, app, active_service_plugins)

    return server


register_configs()


@hydra.main(version_base=None, config_path="conf", config_name="config")
def _main(cfg: AppConfig) -> None:
    logging.basicConfig(level=logging.INFO)
    log_startup_summary(cfg)
    server = build_server(cfg)
    server.run(transport=cast(TransportMode, cfg.server.transport))


def main() -> None:
    _main()
