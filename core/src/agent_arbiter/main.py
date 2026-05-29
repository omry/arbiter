from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Literal, cast

import hydra
from omegaconf import DictConfig, OmegaConf

from .app import AgentArbiterApp
from .config import (
    AppConfig,
    FastMCPConfig,
    configured_service_names,
    register_configs,
    service_accounts_for,
    service_policies_for,
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
HydraConfig = AppConfig | DictConfig


def _to_object(value: Any) -> Any:
    if OmegaConf.is_config(value):
        return OmegaConf.to_object(value)
    return value


def _select_object(cfg: DictConfig, key: str, default: Any) -> Any:
    value = OmegaConf.select(cfg, key, default=default)
    return _to_object(value)


def _instantiate_app_config_from_hydra(cfg: DictConfig) -> AppConfig:
    server = cast(FastMCPConfig, _select_object(cfg, "server", FastMCPConfig()))
    return AppConfig(
        server=server,
        accounts=cast(dict[str, Any], _select_object(cfg, "accounts", {})),
        policies=cast(dict[str, Any], _select_object(cfg, "policies", {})),
        etc=cast(dict[str, Any], _select_object(cfg, "etc", {})),
    )


def _instantiate_app_config(cfg: HydraConfig) -> AppConfig:
    if isinstance(cfg, AppConfig):
        return cfg
    return _instantiate_app_config_from_hydra(cfg)


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
    for service_name in configured_service_names(cfg.accounts):
        service_plugin = available_plugins.get(service_name)
        if service_plugin is None:
            raise RuntimeError(
                f"configured service plugin is not installed: {service_name}"
            )
        active_service_plugins.append(service_plugin)
    return active_service_plugins


def build_app(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
    runtime_dependencies: dict[str, object] | None = None,
) -> AgentArbiterApp:
    app_config = _instantiate_app_config(cfg)
    available_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(app_config, available_plugins)
    runtime_context = ServiceRuntimeContext(
        dependencies=runtime_dependencies or {},
    )
    runtimes: dict[str, object] = {}
    for service_plugin in active_service_plugins:
        accounts = service_accounts_for(app_config, service_plugin.name)
        if accounts is None:
            raise RuntimeError(
                f"service config is not configured: {service_plugin.name}"
            )
        policies = service_policies_for(app_config, service_plugin.name)
        runtimes[service_plugin.name] = service_plugin.build_runtime(
            accounts=accounts,
            policies=policies,
            context=runtime_context,
        )
    return AgentArbiterApp(RuntimeRegistry(runtimes))


def package_version() -> str:
    for package_name in ("agent-arbiter", "agent-arbiter-core"):
        try:
            return version(package_name)
        except PackageNotFoundError:
            continue
    return "unknown"


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def _service_accounts_summary(cfg: AppConfig) -> str:
    summaries: list[str] = []
    for service_name in configured_service_names(cfg.accounts):
        accounts = cfg.accounts.get(service_name, {})
        account_names = sorted(str(account_name) for account_name in accounts)
        summaries.append(f"{service_name}:{_csv_or_none(account_names)}")
    return ";".join(summaries) if summaries else "none"


def log_startup_summary(cfg: AppConfig) -> None:
    active_services = configured_service_names(cfg.accounts)

    LOGGER.info(
        "Agent Arbiter starting version=%s transport=%s bind=%s:%s%s "
        "services=%s service_accounts=%s",
        package_version(),
        cfg.server.transport,
        cfg.server.host,
        cfg.server.port,
        cfg.server.path,
        _csv_or_none(active_services),
        _service_accounts_summary(cfg),
    )


def _register_core_tools(server: "FastMCP", app: AgentArbiterApp) -> None:
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
    app: AgentArbiterApp,
    service_plugins: Sequence[ServicePlugin],
) -> None:
    context = ServicePluginContext(runtimes=app.runtime_registry)
    for service_plugin in service_plugins:
        service_plugin.register_tools(server, context)


def build_server(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    app_config = _instantiate_app_config(cfg)
    available_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(
        app_config, available_service_plugins
    )
    app = build_app(app_config, service_plugins=active_service_plugins)
    server = FastMCP(
        app_config.server.name,
        stateless_http=app_config.server.stateless_http,
        json_response=app_config.server.json_response,
    )
    server.settings.host = app_config.server.host
    server.settings.port = app_config.server.port
    server.settings.streamable_http_path = app_config.server.path

    _register_core_tools(server, app)
    _register_service_plugins(server, app, active_service_plugins)

    return server


register_configs()


@hydra.main(version_base=None, config_path="conf", config_name="config")
def _main(cfg: DictConfig) -> None:
    app_config = _instantiate_app_config(cfg)
    log_startup_summary(app_config)
    server = build_server(app_config)
    server.run(transport=cast(TransportMode, app_config.server.transport))


def main() -> None:
    _main()
