from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, cast

import hydra

from .app import MailSentryApp
from .config import (
    AppConfig,
    configured_service_names,
    register_configs,
    service_config_for,
)
from .plugins import discover_service_plugins
from .services import (
    RuntimeRegistry,
    ServicePlugin,
    ServicePluginContext,
    ServiceRuntimeContext,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


LOGGER = logging.getLogger(__name__)
TransportMode = Literal["stdio", "sse", "streamable-http"]


def _service_plugin_map(
    service_plugins: Sequence[ServicePlugin],
) -> dict[str, ServicePlugin]:
    return {service_plugin.name: service_plugin for service_plugin in service_plugins}


def _configured_service_plugins(
    cfg: AppConfig,
    service_plugins: Sequence[ServicePlugin],
) -> list[ServicePlugin]:
    available_plugins = _service_plugin_map(service_plugins)
    active_service_plugins: list[ServicePlugin] = []
    for service_name in configured_service_names(cfg.services):
        service_plugin = available_plugins.get(service_name)
        if service_plugin is None:
            raise RuntimeError(
                f"configured service plugin is not installed: {service_name}"
            )
        active_service_plugins.append(service_plugin)
    return active_service_plugins


def build_app(
    cfg: AppConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
    runtime_dependencies: dict[str, object] | None = None,
) -> MailSentryApp:
    available_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(cfg, available_plugins)
    runtime_context = ServiceRuntimeContext(
        mail_config=cfg.mail,
        dependencies=runtime_dependencies or {},
    )
    runtimes: dict[str, object] = {}
    for service_plugin in active_service_plugins:
        service_config = service_config_for(cfg.services, service_plugin.name)
        if service_config is None:
            raise RuntimeError(
                f"service config is not configured: {service_plugin.name}"
            )
        runtimes[service_plugin.name] = service_plugin.build_runtime(
            service_config,
            runtime_context,
        )
    return MailSentryApp(cfg.mail, RuntimeRegistry(runtimes))


def package_version() -> str:
    try:
        return version("mail-sentry")
    except PackageNotFoundError:
        return "unknown"


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def log_startup_summary(cfg: AppConfig) -> None:
    accounts = sorted(cfg.mail.accounts)
    active_services = configured_service_names(cfg.services)
    smtp_accounts = sorted(cfg.services.smtp.accounts) if cfg.services.smtp else []
    imap_accounts = sorted(cfg.services.imap.accounts) if cfg.services.imap else []

    LOGGER.info(
        "Mail Sentry starting version=%s transport=%s bind=%s:%s%s "
        "accounts=%s services=%s smtp_accounts=%s imap_accounts=%s",
        package_version(),
        cfg.server.transport,
        cfg.server.host,
        cfg.server.port,
        cfg.server.path,
        _csv_or_none(accounts),
        _csv_or_none(active_services),
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

    available_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(cfg, available_service_plugins)
    app = build_app(cfg, service_plugins=active_service_plugins)
    server = FastMCP(
        cfg.server.name,
        stateless_http=cfg.server.stateless_http,
        json_response=cfg.server.json_response,
    )
    server.settings.host = cfg.server.host
    server.settings.port = cfg.server.port
    server.settings.streamable_http_path = cfg.server.path

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
