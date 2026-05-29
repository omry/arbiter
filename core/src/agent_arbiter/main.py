from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
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
CLI_COMMANDS = {"serve", "config", "plugins"}


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


def config_check_summary(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> str:
    app_config = _instantiate_app_config(cfg)
    build_app(app_config, service_plugins=service_plugins)
    return (
        "config ok: "
        f"services={_csv_or_none(configured_service_names(app_config.accounts))} "
        f"service_accounts={_service_accounts_summary(app_config)}"
    )


def service_plugin_names(
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> list[str]:
    plugins = discover_service_plugins() if service_plugins is None else service_plugins
    return sorted(service_plugin.name for service_plugin in plugins)


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


async def _serve_uvicorn_app(server: "FastMCP", starlette_app: object) -> None:
    import uvicorn

    config = uvicorn.Config(
        cast(Any, starlette_app),
        host=server.settings.host,
        port=server.settings.port,
        log_level=server.settings.log_level.lower(),
        log_config=None,
    )
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()


def _run_server(server: "FastMCP", transport: TransportMode) -> None:
    if transport == "stdio":
        server.run(transport=transport)
        return

    import anyio

    if transport == "streamable-http":
        anyio.run(_serve_uvicorn_app, server, server.streamable_http_app())
        return

    anyio.run(_serve_uvicorn_app, server, server.sse_app(None))


@hydra.main(version_base=None, config_path="conf", config_name="config")
def _serve_main(cfg: DictConfig) -> None:
    app_config = _instantiate_app_config(cfg)
    log_startup_summary(app_config)
    server = build_server(app_config)
    _run_server(server, cast(TransportMode, app_config.server.transport))


@hydra.main(version_base=None, config_path="conf", config_name="config")
def _config_check_main(cfg: DictConfig) -> None:
    print(config_check_summary(cfg))


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    if args and args[0] == "--":
        return list(args[1:])
    return list(args)


def _run_hydra_entrypoint(
    entrypoint: Callable[[], None],
    hydra_args: Sequence[str],
) -> int:
    original_argv = sys.argv
    sys.argv = [original_argv[0], *_strip_arg_separator(hydra_args)]
    try:
        register_configs()
        entrypoint()
    except KeyboardInterrupt:
        print("Agent Arbiter server stopped.", file=sys.stderr)
        return 130
    finally:
        sys.argv = original_argv
    return 0


def _run_serve(hydra_args: Sequence[str]) -> int:
    return _run_hydra_entrypoint(_serve_main, hydra_args)


def _run_config_check(hydra_args: Sequence[str]) -> int:
    return _run_hydra_entrypoint(_config_check_main, hydra_args)


def _looks_like_hydra_arg(value: str) -> bool:
    return (
        value.startswith("-")
        or value.startswith("+")
        or value.startswith("~")
        or "=" in value
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter-server",
        description="Policy-controlled MCP gateway for agent-accessible services.",
    )
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="run the Agent Arbiter MCP server")
    serve.add_argument(
        "hydra_args",
        nargs=argparse.REMAINDER,
        help="Hydra config arguments passed to the server",
    )

    config = subcommands.add_parser("config", help="inspect and validate config")
    config_subcommands = config.add_subparsers(dest="config_command", required=True)
    check = config_subcommands.add_parser(
        "check",
        help="validate config and service runtime construction without serving",
    )
    check.add_argument(
        "hydra_args",
        nargs=argparse.REMAINDER,
        help="Hydra config arguments passed to config validation",
    )

    plugins = subcommands.add_parser("plugins", help="inspect service plugins")
    plugin_subcommands = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_list = plugin_subcommands.add_parser(
        "list",
        help="list installed service plugins",
    )
    plugins_list.add_argument(
        "--json",
        action="store_true",
        help="print plugin names as JSON",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()

    if args == ["-h"] or args == ["--help"]:
        parser.print_help()
        return 0

    if not args or (args[0] not in CLI_COMMANDS and _looks_like_hydra_arg(args[0])):
        return _run_serve(args)

    if args[0] == "serve":
        return _run_serve(args[1:])
    if len(args) >= 2 and args[0] == "config" and args[1] == "check":
        return _run_config_check(args[2:])

    namespace = parser.parse_args(args)
    if namespace.command == "plugins" and namespace.plugins_command == "list":
        names = service_plugin_names()
        if namespace.json:
            print(json.dumps({"plugins": [{"name": name} for name in names]}))
        else:
            for name in names:
                print(name)
        return 0

    parser.error("unknown command")
