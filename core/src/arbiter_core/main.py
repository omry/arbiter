from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import unquote, urlparse

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

from .app import ArbiterApp
from .cli_errors import print_cli_error
from .config import (
    AppConfig,
    ArbiterConfig,
    DiscoveryConfig,
    FastMCPConfig,
    DeploymentScope,
    configured_service_names,
    register_configs,
    service_accounts_for,
    service_policies_for,
)
from .plugins import discover_service_plugins
from .services import (
    CORE_API_VERSION,
    CORE_VERSION,
    OperationCatalog,
    RuntimeRegistry,
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    ServicePlugin,
    ServicePluginContext,
    ServicePluginFactory,
    ServiceRuntimeContext,
    service_plugin_runtime_info,
    validate_service_plugin_compatibility,
    validate_service_plugins,
)
from .version import arbiter_core_version, source_info

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


LOGGER = logging.getLogger(__name__)
TransportMode = Literal["stdio", "sse", "streamable-http"]
HydraConfig = AppConfig | DictConfig
BootstrapObjectKind = Literal["account", "policy"]
CLI_COMMANDS = {"serve", "config", "plugins", "bootstrap", "env", "deploy", "version"}
BOOTSTRAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_FILE_CONFIG_KEY = "arbiter.env_file"
ENV_REFERENCE_PATTERN = re.compile(r"\$\{oc\.env:(?P<name>[^,}\s]+)(?:,[^}]*)?\}")
DEPLOY_PINNED_REQUIREMENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[^<>=!~\s#]+$"
)
DEPLOY_PINNED_REQUIREMENT_PARTS_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==(?P<version>[^<>=!~\s#]+)$"
)
DEFAULT_ENV_FILE_NAME = ".env"
DEFAULT_CONFIG_DIR = "~/.arbiter"
DEFAULT_SERVER_CONFIG_NAME = "arbiter-server"
CONFIG_FILE_MODE = 0o640
ENV_FILE_MODE = 0o600
DEFAULT_DOCKER_DEPLOY_DIR = "./arbiter-docker"
DEPLOY_MANIFEST_FILE_NAME = ".arbiter-deploy.json"
ARBITER_CORE_PACKAGE = "arbiter-core"
ARBITER_ALL_META_PACKAGE = "arbiter-suite"
DOCKER_META_PACKAGE_GROUPS = {
    ARBITER_ALL_META_PACKAGE: (
        ARBITER_CORE_PACKAGE,
        "arbiter-smtp",
        "arbiter-imap",
    )
}
DOCKER_LOCAL_SOURCE_CONTAINER_ROOT = "/source/arbiter"
DOCKER_WHEELS_CONTAINER_ROOT = "/wheels"
DOCKER_COMPOSE_ENV_DEFAULTS = [
    ("ARBITER_IMAGE", "python:3.11-slim"),
    ("ARBITER_CONTAINER_NAME", "arbiter-staging"),
    ("ARBITER_RESTART", "unless-stopped"),
    ("ARBITER_APP_ENV_FILE", "./conf/.env"),
    ("ARBITER_CONFIG_DIR", "./conf"),
    ("ARBITER_CONFIG_NAME", "arbiter-server"),
    ("ARBITER_REQUIREMENTS_FILE", "./requirements.txt"),
    ("ARBITER_WHEELS_DIR", "./wheels"),
    ("ARBITER_HOST_BIND", "127.0.0.1"),
    ("ARBITER_HOST_PORT", "18025"),
    ("ARBITER_CONTAINER_PORT", "8025"),
    ("ARBITER_DOCKER_NETWORK_NAME", "arbiter-staging"),
    ("ARBITER_DOCKER_BRIDGE_NAME", "arbiter-stg0"),
    ("ARBITER_DOCKER_SUBNET", "172.31.251.0/24"),
]
GROUP_SELECTION_PATTERN = re.compile(
    r"^\s*-\s*(?P<item>[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?)\s*(?:#.*)?$"
)
MISC_ENV_BLOCK = "miscellaneous"
MAIN_CONFIG_TEMPLATE = """defaults:
# Arbiter composes this config at startup from the defaults below.
# Inspect the composed config with:
#   arbiter-server --config-dir <dir> --config-name arbiter-server config show
# Override composed values with Hydra overrides, for example:
#   arbiter-server --config-dir <dir> serve arbiter.server.port=8025
# Optionally load a config-dir-relative dotenv file before composition:
#   arbiter:
#     env_file: local.env
  - arbiter_app_config_schema
  - arbiter: server
  - _self_
"""
SERVER_CONFIG_TEMPLATE = """# @package arbiter
server:
  name: arbiter
  transport: streamable-http
  host: 127.0.0.1
  port: 8000
  path: /mcp
  stateless_http: true
  json_response: true
deployment_scope: unknown
discovery:
  max_account_preview_limit: 25
  max_operation_preview_limit: 25
"""


@dataclass(frozen=True)
class EnvReference:
    name: str
    block: str


@dataclass(frozen=True)
class DockerDeployArgs:
    action: str
    directory: Path
    requirements: tuple[str, ...]
    force: bool


@dataclass(frozen=True)
class DockerDeployRequirements:
    requirements: tuple[str, ...]


def _to_object(value: Any) -> Any:
    if OmegaConf.is_config(value):
        return OmegaConf.to_object(value)
    return value


def _select_object(cfg: DictConfig, key: str, default: Any) -> Any:
    value = OmegaConf.select(cfg, key, default=default)
    return _to_object(value)


def _instantiate_app_config_from_hydra(cfg: DictConfig) -> AppConfig:
    raw_deployment_scope = _select_object(
        cfg,
        "arbiter.deployment_scope",
        DeploymentScope.unknown,
    )
    deployment_scope = (
        raw_deployment_scope
        if isinstance(raw_deployment_scope, DeploymentScope)
        else DeploymentScope(str(raw_deployment_scope))
    )
    server_cfg = cast(
        DictConfig,
        OmegaConf.merge(
            OmegaConf.structured(FastMCPConfig),
            OmegaConf.select(cfg, "arbiter.server", default={}),
        ),
    )
    server = cast(
        FastMCPConfig,
        _to_object(server_cfg),
    )
    discovery_cfg = cast(
        DictConfig,
        OmegaConf.merge(
            OmegaConf.structured(DiscoveryConfig),
            OmegaConf.select(cfg, "arbiter.discovery", default={}),
        ),
    )
    discovery = cast(
        DiscoveryConfig,
        _to_object(discovery_cfg),
    )
    return AppConfig(
        arbiter=ArbiterConfig(
            server=server,
            deployment_scope=deployment_scope,
            discovery=discovery,
            account=cast(dict[str, Any], _select_object(cfg, "arbiter.account", {})),
            policy=cast(dict[str, Any], _select_object(cfg, "arbiter.policy", {})),
            etc=cast(dict[str, Any], _select_object(cfg, "arbiter.etc", {})),
        )
    )


def _instantiate_app_config(cfg: HydraConfig) -> AppConfig:
    if isinstance(cfg, AppConfig):
        return cfg
    return _instantiate_app_config_from_hydra(cfg)


def _service_plugin_map(
    service_plugins: Sequence[ServicePlugin],
) -> dict[str, ServicePlugin]:
    validate_service_plugins(service_plugins)
    return {service_plugin.name: service_plugin for service_plugin in service_plugins}


def _configured_service_plugins(
    cfg: AppConfig,
    service_plugins: Sequence[ServicePlugin],
) -> list[ServicePlugin]:
    available_plugins = _service_plugin_map(service_plugins)
    active_service_plugins: list[ServicePlugin] = []
    for service_name in configured_service_names(cfg.arbiter.account):
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
) -> ArbiterApp:
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
    return ArbiterApp(RuntimeRegistry(runtimes))


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def _service_accounts_summary(cfg: AppConfig) -> str:
    summaries: list[str] = []
    for service_name in configured_service_names(cfg.arbiter.account):
        accounts = cfg.arbiter.account.get(service_name, {})
        account_names = sorted(str(account_name) for account_name in accounts)
        summaries.append(f"{service_name}:{_csv_or_none(account_names)}")
    return ";".join(summaries) if summaries else "none"


def _server_mcp_url(cfg: AppConfig) -> str:
    if cfg.arbiter.server.transport == "stdio":
        return "stdio"
    return (
        f"http://{cfg.arbiter.server.host}:"
        f"{cfg.arbiter.server.port}{cfg.arbiter.server.path}"
    )


def log_startup_summary(cfg: AppConfig) -> None:
    active_services = configured_service_names(cfg.arbiter.account)

    LOGGER.info(
        "Arbiter starting version=%s deployment_scope=%s transport=%s bind=%s:%s%s "
        "mcp_url=%s services=%s service_accounts=%s",
        arbiter_core_version(),
        cfg.arbiter.deployment_scope.value,
        cfg.arbiter.server.transport,
        cfg.arbiter.server.host,
        cfg.arbiter.server.port,
        cfg.arbiter.server.path,
        _server_mcp_url(cfg),
        _csv_or_none(active_services),
        _service_accounts_summary(cfg),
    )


def _installed_plugin_summary(
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> str:
    names = service_plugin_names(service_plugins)
    return ", ".join(names) if names else "none"


def ensure_runnable_config(
    cfg: AppConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> None:
    if not configured_service_names(cfg.arbiter.account):
        raise ValueError(
            "config must define at least one service account before Arbiter can run\n"
            f"currently installed arbiter plugins: "
            f"{_installed_plugin_summary(service_plugins)}\n"
            "use `arbiter-server --config-dir DIR bootstrap plugin PLUGIN "
            "account NAME` to create an account config"
        )


def config_check_summary(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> str:
    app_config = _instantiate_app_config(cfg)
    ensure_runnable_config(app_config, service_plugins=service_plugins)
    build_app(app_config, service_plugins=service_plugins)
    return (
        "config ok: "
        f"services={_csv_or_none(configured_service_names(app_config.arbiter.account))} "
        f"service_accounts={_service_accounts_summary(app_config)}"
    )


def service_plugin_names(
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> list[str]:
    plugins = discover_service_plugins() if service_plugins is None else service_plugins
    validate_service_plugins(plugins)
    return sorted(service_plugin.name for service_plugin in plugins)


def service_plugin_infos(
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> list[dict[str, str]]:
    plugins = discover_service_plugins() if service_plugins is None else service_plugins
    validate_service_plugins(plugins)
    return [
        {
            "name": info.name,
            "version": info.version,
            "core_api_version": info.core_api_version,
        }
        for info in sorted(
            (service_plugin_runtime_info(service_plugin) for service_plugin in plugins),
            key=lambda plugin_info: plugin_info.name,
        )
    ]


def runtime_version_info(
    service_plugins: Sequence[ServicePlugin] | None = None,
    *,
    deployment_scope: DeploymentScope | str = DeploymentScope.unknown,
) -> dict[str, object]:
    source = source_info()
    if isinstance(deployment_scope, DeploymentScope):
        deployment_scope_value = deployment_scope.value
    else:
        deployment_scope_value = deployment_scope
    return {
        "core": {
            "version": CORE_VERSION,
            "api_version": CORE_API_VERSION,
        },
        "deployment_scope": deployment_scope_value,
        "source": {
            "commit": source.commit,
            "dirty": source.dirty,
        },
        "plugins": service_plugin_infos(service_plugins),
    }


def _print_runtime_version_info(
    service_plugins: Sequence[ServicePlugin] | None = None,
    *,
    as_json: bool,
) -> None:
    version_info = runtime_version_info(service_plugins)
    if as_json:
        print(json.dumps(version_info))
        return

    core = cast(dict[str, str], version_info["core"])
    print(f"core {core['version']} (api {core['api_version']})")
    print(f"deployment scope {version_info['deployment_scope']}")
    source = cast(dict[str, object], version_info["source"])
    if source["commit"] is not None:
        dirty = " dirty" if source["dirty"] else ""
        print(f"source {source['commit']}{dirty}")
    print("plugins:")
    plugins = cast(list[dict[str, str]], version_info["plugins"])
    if not plugins:
        print("  none")
        return
    for plugin in plugins:
        print(
            f"  {plugin['name']} {plugin['version']} "
            f"(core api {plugin['core_api_version']})"
        )


def _register_core_tools(
    server: "FastMCP",
    catalog: OperationCatalog,
    service_plugins: Sequence[ServicePlugin],
    deployment_scope: DeploymentScope,
) -> None:
    @server.tool(
        description=(
            "Return Arbiter core and loaded service plugin version " "information."
        )
    )
    def version_info() -> dict[str, object]:
        return runtime_version_info(
            service_plugins,
            deployment_scope=deployment_scope,
        )

    @server.tool(
        description=(
            "Discover Arbiter server identity, installed plugins, accounts, "
            "account policy summaries, and operation schemas."
        )
    )
    def info(
        kind: str = "overview",
        plugin: str | None = None,
        account: str | None = None,
        operation: str | None = None,
    ) -> dict[str, object]:
        return catalog.info(
            kind=kind,
            plugin=plugin,
            account=account,
            operation=operation,
            version_info=runtime_version_info(
                service_plugins,
                deployment_scope=deployment_scope,
            ),
        )

    @server.tool(
        description=(
            "Return the available Arbiter capability names. Use "
            "describe_caps or describe_cap to drill down before "
            "choosing an operation."
        )
    )
    def list_caps() -> dict[str, object]:
        return catalog.list_capabilities()

    @server.tool(
        description=(
            "Return bounded summaries of all Arbiter capabilities, including "
            "account and operation previews."
        )
    )
    def describe_caps(
        operation_preview_limit: int = 8,
        account_preview_limit: int = 8,
    ) -> dict[str, object]:
        return catalog.describe_capabilities(
            operation_preview_limit=operation_preview_limit,
            account_preview_limit=account_preview_limit,
        )

    @server.tool(
        description=(
            "Return focused account and operation context for one Arbiter "
            "capability."
        )
    )
    def describe_cap(capability: str) -> dict[str, object]:
        return catalog.describe_capability(capability)

    @server.tool(
        description=(
            "Return the description and input schema for one Arbiter "
            "operation. Operation ids use CAPABILITY:OPERATION syntax."
        )
    )
    def describe_op(id: str) -> dict[str, object]:
        return catalog.describe_operation(id)

    @server.tool(
        description=(
            "Run one Arbiter operation by id. Operation ids use "
            "CAPABILITY:OPERATION syntax."
        )
    )
    def run_op(
        id: str,
        arguments: dict[str, Any] | None = None,
    ) -> object:
        return catalog.invoke_operation(id, arguments)


def _create_fastmcp_server(app_config: AppConfig) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(
        app_config.arbiter.server.name,
        stateless_http=app_config.arbiter.server.stateless_http,
        json_response=app_config.arbiter.server.json_response,
    )
    server.settings.host = app_config.arbiter.server.host
    server.settings.port = app_config.arbiter.server.port
    server.settings.streamable_http_path = app_config.arbiter.server.path
    mcp_server = getattr(server, "_mcp_server", None)
    if mcp_server is not None:
        mcp_server.version = arbiter_core_version()
    return server


def build_server(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> "FastMCP":
    app_config = _instantiate_app_config(cfg)
    available_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(
        app_config,
        available_service_plugins,
    )
    app = build_app(app_config, service_plugins=active_service_plugins)
    server = _create_fastmcp_server(app_config)
    catalog = OperationCatalog(
        active_service_plugins,
        ServicePluginContext(runtimes=app.runtime_registry),
        max_account_preview_limit=app_config.arbiter.discovery.max_account_preview_limit,
        max_operation_preview_limit=app_config.arbiter.discovery.max_operation_preview_limit,
    )
    _register_core_tools(
        server,
        catalog,
        active_service_plugins,
        app_config.arbiter.deployment_scope,
    )

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


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    if args and args[0] == "--":
        return list(args[1:])
    return list(args)


def _strip_env_comment(value: str) -> str:
    in_single_quotes = False
    in_double_quotes = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double_quotes:
            escaped = True
            continue
        if char == "'" and not in_double_quotes:
            in_single_quotes = not in_single_quotes
            continue
        if char == '"' and not in_single_quotes:
            in_double_quotes = not in_double_quotes
            continue
        if (
            char == "#"
            and not in_single_quotes
            and not in_double_quotes
            and (index == 0 or value[index - 1].isspace())
        ):
            return value[:index].rstrip()
    return value


def _decode_double_quoted_env_value(value: str) -> str:
    replacements = {
        "\\n": "\n",
        "\\r": "\r",
        "\\t": "\t",
        '\\"': '"',
        "\\\\": "\\",
    }
    for escaped, replacement in replacements.items():
        value = value.replace(escaped, replacement)
    return value


def _parse_env_value(value: str) -> str:
    stripped = _strip_env_comment(value.strip()).strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "'":
        return stripped[1:-1]
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return _decode_double_quoted_env_value(stripped[1:-1])
    return stripped


def _read_env_file_values(
    env_file: Path, *, missing_ok: bool = False
) -> dict[str, str]:
    env_file_path = env_file.expanduser()
    if not env_file_path.exists():
        if missing_ok:
            return {}
        raise ValueError(f"env file not found: {env_file_path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        env_file_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(
                f"invalid env file line {line_number} in {env_file_path}: "
                "expected KEY=VALUE"
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not ENV_NAME_PATTERN.fullmatch(key):
            raise ValueError(
                f"invalid env variable name on line {line_number} in "
                f"{env_file_path}: {key}"
            )
        values[key] = _strip_env_comment(raw_value.strip()).strip()
    return values


def _write_text_with_mode(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = -1
            handle.write(content)
        os.replace(temporary_path, path)
        path.chmod(mode)
    except BaseException:
        if file_descriptor != -1:
            os.close(file_descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _other_read_write_bits(mode: int) -> int:
    return mode & (stat.S_IROTH | stat.S_IWOTH)


def _group_or_other_read_write_bits(mode: int) -> int:
    return mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)


def _ensure_runtime_config_permissions(
    *,
    config_dir: Path,
    env_file: Path | None,
) -> None:
    for config_file in sorted(config_dir.rglob("*.yaml")):
        if not config_file.is_file():
            continue
        if _other_read_write_bits(config_file.stat().st_mode):
            raise ValueError(
                "unsafe config file permissions: "
                f"{config_file} must not be readable or writable by others; "
                f"run `chmod o-rw {config_file}`"
            )

    if env_file is None or not env_file.exists():
        return
    if _group_or_other_read_write_bits(env_file.stat().st_mode):
        raise ValueError(
            "unsafe app env file permissions: "
            f"{env_file} must not be readable or writable by group or others; "
            f"run `chmod 600 {env_file}`"
        )


def load_env_file(env_file: str | Path) -> None:
    env_file_path = Path(env_file).expanduser()
    for key, raw_value in _read_env_file_values(env_file_path).items():
        os.environ.setdefault(key, _parse_env_value(raw_value))


def _configured_env_file(
    *,
    config_dir: Path,
    config_name: str,
) -> Path | None:
    config_file = config_dir / f"{config_name}.yaml"
    if not config_file.exists():
        return None
    env_file = OmegaConf.select(OmegaConf.load(config_file), ENV_FILE_CONFIG_KEY)
    if env_file in (None, ""):
        return None
    if not isinstance(env_file, str):
        raise ValueError(f"{ENV_FILE_CONFIG_KEY} must be a string path")
    env_file_path = Path(env_file).expanduser()
    if env_file_path.is_absolute():
        return env_file_path
    return config_dir / env_file_path


def _configure_default_env_file(
    *,
    config_dir: Path,
    config_name: str,
) -> Path:
    config_file = config_dir / f"{config_name}.yaml"
    if not config_file.exists():
        raise ValueError(f"main config not found: {config_file}")
    lines = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    env_line = f"  env_file: {DEFAULT_ENV_FILE_NAME}\n"
    for index, line in enumerate(lines):
        if line.strip() == "arbiter:":
            lines[index + 1 : index + 1] = [env_line]
            _write_text_with_mode(config_file, "".join(lines), CONFIG_FILE_MODE)
            return config_dir / DEFAULT_ENV_FILE_NAME
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = f"{lines[-1]}\n"
    if lines and lines[-1].strip():
        lines.append("\n")
    lines.extend(["arbiter:\n", env_line])
    _write_text_with_mode(config_file, "".join(lines), CONFIG_FILE_MODE)
    return config_dir / DEFAULT_ENV_FILE_NAME


def compose_config(
    *,
    config_dir: str | Path,
    config_name: str,
    overrides: Sequence[str] = (),
    enforce_runtime_permissions: bool = False,
) -> DictConfig:
    config_dir_path = Path(config_dir).expanduser().resolve()
    env_file = _configured_env_file(
        config_dir=config_dir_path,
        config_name=config_name,
    )
    if enforce_runtime_permissions:
        _ensure_runtime_config_permissions(
            config_dir=config_dir_path,
            env_file=env_file,
        )
    if env_file is not None:
        load_env_file(env_file)
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(config_dir_path),
        job_name="arbiter-server",
    ):
        return compose(
            config_name=config_name,
            overrides=list(_strip_arg_separator(overrides)),
        )


def _env_block_for_path(path: Sequence[str]) -> str:
    if (
        len(path) >= 3
        and path[0] == "arbiter"
        and path[1]
        in {
            "account",
            "policy",
        }
    ):
        return f"arbiter-{path[2]}"
    return MISC_ENV_BLOCK


def _collect_env_references_from_value(
    value: object,
    *,
    path: Sequence[str],
    references: dict[str, EnvReference],
) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _collect_env_references_from_value(
                nested_value,
                path=[*path, str(key)],
                references=references,
            )
        return
    if isinstance(value, list):
        for index, nested_value in enumerate(value):
            _collect_env_references_from_value(
                nested_value,
                path=[*path, str(index)],
                references=references,
            )
        return
    if not isinstance(value, str):
        return
    for match in ENV_REFERENCE_PATTERN.finditer(value):
        name = match.group("name")
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid env variable reference: {name}")
        block = _env_block_for_path(path)
        existing = references.get(name)
        if existing is None or existing.block == MISC_ENV_BLOCK:
            references[name] = EnvReference(name=name, block=block)


def collect_env_references(cfg: DictConfig) -> dict[str, EnvReference]:
    container = OmegaConf.to_container(cfg, resolve=False)
    references: dict[str, EnvReference] = {}
    _collect_env_references_from_value(
        container,
        path=[],
        references=references,
    )
    return references


def _compose_config_for_env_command(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> tuple[Path, Path | None, DictConfig, dict[str, EnvReference]]:
    config_dir_path = Path(config_dir).expanduser().resolve()
    env_file = _configured_env_file(
        config_dir=config_dir_path,
        config_name=config_name,
    )
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(config_dir_path),
        job_name="arbiter-server-env",
    ):
        cfg = compose(
            config_name=config_name,
            overrides=list(_strip_arg_separator(overrides)),
        )
    return config_dir_path, env_file, cfg, collect_env_references(cfg)


def _run_env_check(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> int:
    try:
        _config_dir_path, env_file, _cfg, references = _compose_config_for_env_command(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
        )
        env_file_values: dict[str, str] = {}
        if env_file is not None:
            env_file_values = _read_env_file_values(env_file)
        satisfied = set(env_file_values) | set(os.environ)
        missing = [
            reference
            for reference in references.values()
            if reference.name not in satisfied
        ]
    except ValueError as exc:
        print_cli_error(str(exc), area="env")
        return 1
    if missing:
        print_cli_error(
            "missing required environment variables:",
            area="env",
            details=[
                f"{reference.name} ({reference.block})"
                for reference in sorted(
                    missing, key=lambda item: (item.block, item.name)
                )
            ],
        )
        return 1
    print(f"env ok: {len(references)} variables satisfied")
    return 0


def _format_env_file_blocks(block_values: Mapping[str, Mapping[str, str]]) -> str:
    lines: list[str] = []
    block_names = sorted(
        block_name for block_name, values in block_values.items() if values
    )
    if MISC_ENV_BLOCK in block_names:
        block_names = [
            block_name for block_name in block_names if block_name != MISC_ENV_BLOCK
        ]
        block_names.append(MISC_ENV_BLOCK)
    for block_index, block_name in enumerate(block_names):
        if block_index:
            lines.append("")
        lines.append(f"# {block_name}")
        for name, value in block_values[block_name].items():
            lines.append(f"{name}={value}")
    return "\n".join(lines) + ("\n" if lines else "")


def _run_env_bootstrap(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> int:
    try:
        _config_dir_path, env_file, _cfg, references = _compose_config_for_env_command(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
        )
        if env_file is None:
            env_file = _configure_default_env_file(
                config_dir=Path(config_dir).expanduser().resolve(),
                config_name=config_name,
            )
        existing_values = _read_env_file_values(env_file, missing_ok=True)
    except ValueError as exc:
        print_cli_error(str(exc), area="env")
        return 1

    block_values: dict[str, dict[str, str]] = {}
    for name, value in existing_values.items():
        reference = references.get(name)
        block = reference.block if reference is not None else MISC_ENV_BLOCK
        block_values.setdefault(block, {})[name] = value

    satisfied = set(existing_values) | set(os.environ)
    for reference in references.values():
        if reference.name not in satisfied:
            block_values.setdefault(reference.block, {})[reference.name] = ""

    content = _format_env_file_blocks(block_values)
    if env_file.exists() and env_file.read_text(encoding="utf-8") == content:
        env_file.chmod(ENV_FILE_MODE)
        print(f"env file already up to date: {env_file}")
        return 0
    env_file.parent.mkdir(parents=True, exist_ok=True)
    _write_text_with_mode(env_file, content, ENV_FILE_MODE)
    print(f"wrote {env_file}")
    return 0


def _deploy_template_text(name: str) -> str:
    return (
        files("arbiter_core")
        .joinpath("deploy")
        .joinpath("docker")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _entry_point_distribution_name(entry_point: Any) -> str | None:
    distribution = getattr(entry_point, "dist", None)
    metadata = getattr(distribution, "metadata", None)
    if metadata is not None:
        name = metadata.get("Name")
        if isinstance(name, str) and name:
            return name
    name = getattr(distribution, "name", None)
    if isinstance(name, str) and name:
        return name
    return None


def _normalized_distribution_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _distribution_direct_url_source_root(installed_distribution: Any) -> Path | None:
    for distribution_file in installed_distribution.files or ():
        if not str(distribution_file).endswith(".dist-info/direct_url.json"):
            continue
        direct_url_path = Path(installed_distribution.locate_file(distribution_file))
        try:
            direct_url = json.loads(direct_url_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        dir_info = direct_url.get("dir_info")
        if not isinstance(dir_info, dict):
            return None
        if not dir_info.get("editable"):
            return None
        url = direct_url.get("url")
        if not isinstance(url, str):
            return None
        parsed_url = urlparse(url)
        if parsed_url.scheme != "file":
            return None
        source_root = Path(unquote(parsed_url.path))
        if source_root.is_dir() and (source_root / "pyproject.toml").is_file():
            return source_root
    return None


def _build_local_source_wheel(source_root: Path, wheel_dir: Path) -> Path | None:
    if not _ensure_writable_wheel_dir(wheel_dir):
        return None
    with TemporaryDirectory(prefix="arbiter-wheel-") as temporary_wheel_dir_raw:
        temporary_wheel_dir = Path(temporary_wheel_dir_raw)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(temporary_wheel_dir),
                str(source_root),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            details = [f"source: {source_root}"]
            if result.stderr:
                details.extend(result.stderr.strip().splitlines()[-5:])
            print_cli_error(
                "cannot build local docker wheel", area="deploy", details=details
            )
            return None
        built_wheels = sorted(temporary_wheel_dir.glob("*.whl"))
        if len(built_wheels) != 1:
            print_cli_error(
                "cannot identify built local docker wheel",
                area="deploy",
                details=[
                    f"source: {source_root}",
                    f"wheel count: {len(built_wheels)}",
                ],
            )
            return None
        wheel = built_wheels[0]
        destination = wheel_dir / wheel.name
        try:
            if destination.exists():
                destination.unlink()
            shutil.copy2(wheel, destination)
        except OSError as exc:
            print_cli_error(
                "cannot write local docker wheel",
                area="deploy",
                details=[
                    f"source: {source_root}",
                    f"wheel: {destination}",
                    f"error: {exc}",
                ],
            )
            return None
        return destination


def _ensure_writable_wheel_dir(wheel_dir: Path) -> bool:
    try:
        wheel_dir.mkdir(parents=True, exist_ok=True)
        write_check = wheel_dir / ".arbiter-write-check"
        write_check.write_text("", encoding="utf-8")
        write_check.unlink()
    except OSError as exc:
        print_cli_error(
            "deployment wheelhouse is not writable",
            area="deploy",
            details=[
                f"wheel dir: {wheel_dir}",
                f"error: {exc}",
                "remove or chown the wheelhouse directory, then retry",
            ],
        )
        return False
    return True


def _docker_requirement_for_installed_distribution(
    *,
    distribution_name: str,
    version: str,
    installed_distribution: Any | None,
    wheel_dir: Path | None,
) -> str | None:
    if installed_distribution is not None and wheel_dir is not None:
        source_root = _distribution_direct_url_source_root(installed_distribution)
        if source_root is not None:
            wheel = _build_local_source_wheel(source_root, wheel_dir)
            if wheel is None:
                return None
    if version == "unknown":
        return None
    return f"{distribution_name}=={version}"


def _installed_python_deploy_requirements(
    *, wheel_dir: Path | None = None
) -> DockerDeployRequirements | None:
    core_version = arbiter_core_version()
    try:
        core_distribution = distribution(ARBITER_CORE_PACKAGE)
    except PackageNotFoundError:
        core_distribution = None
    core_requirement = _docker_requirement_for_installed_distribution(
        distribution_name=ARBITER_CORE_PACKAGE,
        version=core_version,
        installed_distribution=core_distribution,
        wheel_dir=wheel_dir,
    )
    if core_requirement is None:
        return None

    plugin_pins: dict[str, tuple[str, str]] = {}
    for entry_point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP):
        try:
            plugin_factory = cast(ServicePluginFactory, entry_point.load())
        except ModuleNotFoundError as exc:
            LOGGER.warning(
                "Skipping unavailable service plugin entry point %s=%s: %s",
                entry_point.name,
                entry_point.value,
                exc,
            )
            continue
        service_plugin = plugin_factory()
        validate_service_plugin_compatibility(service_plugin)
        plugin_info = service_plugin_runtime_info(service_plugin)
        if plugin_info.version == "unknown":
            return None
        distribution_name = _entry_point_distribution_name(entry_point)
        if distribution_name is None:
            return None
        requirement = _docker_requirement_for_installed_distribution(
            distribution_name=distribution_name,
            version=plugin_info.version,
            installed_distribution=getattr(entry_point, "dist", None),
            wheel_dir=wheel_dir,
        )
        if requirement is None:
            return None
        plugin_pins[_normalized_distribution_name(distribution_name)] = (
            distribution_name,
            requirement,
        )

    return DockerDeployRequirements(
        requirements=(
            core_requirement,
            *(
                requirement
                for _normalized_name, (_name, requirement) in sorted(
                    plugin_pins.items()
                )
            ),
        )
    )


def _default_deploy_requirements(
    *, wheel_dir: Path | None
) -> DockerDeployRequirements | None:
    return _installed_python_deploy_requirements(wheel_dir=wheel_dir)


def _format_deploy_requirements(requirements: Sequence[str]) -> str:
    return "\n".join(requirements) + "\n"


def _deploy_requirement_error(requirement: str) -> str | None:
    if not requirement:
        return "docker.requirement must not be empty"
    if requirement.startswith("/"):
        return None
    if DEPLOY_PINNED_REQUIREMENT_PATTERN.fullmatch(requirement):
        return None
    return (
        "docker.requirement must be an exact package pin "
        "(name==version) or an absolute container path"
    )


def _pinned_requirement_parts(requirement: str) -> tuple[str, str] | None:
    match = DEPLOY_PINNED_REQUIREMENT_PARTS_PATTERN.fullmatch(requirement)
    if match is None:
        return None
    return match.group("name"), match.group("version")


def _deploy_requirements_semantic_error(requirements: Sequence[str]) -> str | None:
    pins: dict[str, str] = {}
    for requirement in requirements:
        parts = _pinned_requirement_parts(requirement)
        if parts is None:
            continue
        name, version = parts
        existing_version = pins.get(name)
        if existing_version is not None and existing_version != version:
            return (
                f"conflicting docker.requirement pins for {name}: "
                f"{existing_version}, {version}"
            )
        pins[name] = version
    return None


def _expand_meta_deploy_requirements(requirements: Sequence[str]) -> tuple[str, ...]:
    pins = {
        name: version
        for requirement in requirements
        if (parts := _pinned_requirement_parts(requirement)) is not None
        for name, version in (parts,)
    }
    expanded_meta_packages = {
        meta_package
        for meta_package, package_names in DOCKER_META_PACKAGE_GROUPS.items()
        if meta_package in pins and any(name in pins for name in package_names)
    }
    expanded_package_names = {
        package_name
        for meta_package in expanded_meta_packages
        for package_name in DOCKER_META_PACKAGE_GROUPS[meta_package]
    }
    if not expanded_meta_packages:
        return tuple(requirements)

    expanded_requirements: list[str] = []
    for requirement in requirements:
        parts = _pinned_requirement_parts(requirement)
        if parts is None:
            expanded_requirements.append(requirement)
            continue
        name, version = parts
        if name in expanded_meta_packages:
            for package_name in DOCKER_META_PACKAGE_GROUPS[name]:
                expanded_requirements.append(
                    f"{package_name}=={pins.get(package_name, version)}"
                )
            continue
        if name in expanded_package_names:
            continue
        expanded_requirements.append(requirement)
    return tuple(expanded_requirements)


def _parse_docker_deploy_args(args: Sequence[str]) -> DockerDeployArgs | None:
    action: str | None = None
    directory = Path(DEFAULT_DOCKER_DEPLOY_DIR)
    requirements: list[str] = []
    force = False

    for arg in _strip_arg_separator(args):
        if arg == "--force":
            force = True
            continue
        if arg in {"init", "update"}:
            if action is not None:
                print_cli_error(
                    f"multiple deploy actions provided: {action}, {arg}",
                    area="deploy",
                )
                return None
            action = arg
            continue
        if "=" not in arg:
            print_cli_error(
                f"unknown docker deploy argument: {arg}",
                area="deploy",
                details=[
                    "expected init, update, --force, docker.dir=PATH, or "
                    "docker.requirement=REQUIREMENT"
                ],
            )
            return None
        key, value = arg.split("=", 1)
        if key == "docker.dir":
            directory = Path(value)
            continue
        if key == "docker.requirement":
            requirements.append(value)
            continue
        print_cli_error(f"unknown docker deploy override: {key}", area="deploy")
        return None

    if action is None:
        print_cli_error(
            "docker deploy requires an action: init or update",
            area="deploy",
        )
        return None
    if force and action != "update":
        print_cli_error(
            "--force is only supported with docker deploy update",
            area="deploy",
        )
        return None
    for requirement in requirements:
        error = _deploy_requirement_error(requirement)
        if error is not None:
            print_cli_error(error, area="deploy", details=[f"value: {requirement}"])
            return None
    semantic_error = _deploy_requirements_semantic_error(requirements)
    if semantic_error is not None:
        print_cli_error(semantic_error, area="deploy")
        return None
    return DockerDeployArgs(
        action=action,
        directory=directory.expanduser(),
        requirements=tuple(requirements),
        force=force,
    )


def _resolve_docker_deploy_requirements(
    requirements: Sequence[str],
    *,
    wheel_dir: Path | None,
) -> DockerDeployRequirements | None:
    if requirements:
        return DockerDeployRequirements(
            requirements=_expand_meta_deploy_requirements(requirements)
        )
    default_requirements = _default_deploy_requirements(wheel_dir=wheel_dir)
    if default_requirements is None:
        print_cli_error(
            "cannot infer default docker requirements",
            area="deploy",
            details=[
                "install Arbiter packages in the current Python environment so "
                "the generator can pin them",
                "or pass docker.requirement=arbiter-suite==VERSION for the "
                "all-in-one meta package",
                "or pass one or more docker.requirement=PACKAGE==VERSION "
                "entries for another meta package or explicit packages",
                "for local checkout testing, pass absolute container source paths",
            ],
        )
        return None
    return default_requirements


def _format_docker_compose_env_file(existing_values: Mapping[str, str]) -> str:
    lines = [
        "# Docker Compose settings for the Arbiter deployment.",
        "# These values control the container wrapper, not Arbiter runtime config.",
        "",
    ]
    default_names = {name for name, _default in DOCKER_COMPOSE_ENV_DEFAULTS}
    for name, default in DOCKER_COMPOSE_ENV_DEFAULTS:
        lines.append(f"{name}={existing_values.get(name, default)}")
    extra_names = sorted(name for name in existing_values if name not in default_names)
    if extra_names:
        lines.extend(["", "# Extra local Compose values."])
        for name in extra_names:
            lines.append(f"{name}={existing_values[name]}")
    return "\n".join(lines) + "\n"


def _write_deploy_file(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    print(f"wrote {path}")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _deploy_managed_paths(deploy_dir: Path) -> dict[str, Path]:
    return {
        "compose": deploy_dir / "compose.yaml",
        "compose_override": deploy_dir / "compose.override.yaml",
        "docker_env": deploy_dir / "docker.env",
        "requirements": deploy_dir / "requirements.txt",
        "helper": deploy_dir / "arbiter-docker",
    }


def _deploy_manifest_path(deploy_dir: Path) -> Path:
    return deploy_dir / DEPLOY_MANIFEST_FILE_NAME


def _load_deploy_manifest(deploy_dir: Path) -> dict[str, str]:
    manifest_path = _deploy_manifest_path(deploy_dir)
    if not manifest_path.exists():
        return {}
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    raw_files = data.get("files", {})
    if not isinstance(raw_files, dict):
        return {}
    file_hashes: dict[str, str] = {}
    for relative_path, raw_entry in raw_files.items():
        if not isinstance(relative_path, str) or not isinstance(raw_entry, dict):
            continue
        sha256 = raw_entry.get("sha256")
        if isinstance(sha256, str):
            file_hashes[relative_path] = sha256
    return file_hashes


def _write_deploy_manifest(
    deploy_dir: Path,
    *,
    file_hashes: Mapping[str, str],
) -> None:
    manifest_path = _deploy_manifest_path(deploy_dir)
    manifest = {
        "schema_version": 1,
        "generator": "arbiter-server deploy docker",
        "arbiter_core_version": arbiter_core_version(),
        "files": {
            relative_path: {
                "kind": "template",
                "sha256": file_hashes[relative_path],
            }
            for relative_path in sorted(file_hashes)
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")


def _write_manifest_owned_deploy_file(
    *,
    path: Path,
    relative_path: str,
    content: str,
    executable: bool,
    manifest_hashes: dict[str, str],
) -> None:
    _write_deploy_file(path, content, executable=executable)
    manifest_hashes[relative_path] = _sha256_file(path)


def _deploy_requirement_names(requirements: Sequence[str]) -> set[str] | None:
    names: set[str] = set()
    for requirement in requirements:
        parts = _pinned_requirement_parts(requirement)
        if parts is None:
            return None
        name, _version = parts
        names.add(_normalized_distribution_name(name))
    return names


def _read_deploy_requirements(path: Path) -> tuple[str, ...]:
    requirements: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = line.strip()
        if not requirement or requirement.startswith("#"):
            continue
        requirements.append(requirement.split(" #", 1)[0].strip())
    return tuple(requirements)


def _ensure_deploy_file_mode(path: Path, *, executable: bool) -> bool:
    if not executable:
        return False
    current_mode = path.stat().st_mode
    if current_mode & 0o111:
        return False
    path.chmod(0o755)
    return True


def _update_manifest_owned_deploy_file(
    *,
    path: Path,
    relative_path: str,
    content: str,
    executable: bool,
    manifest_hashes: dict[str, str],
    force: bool,
) -> Literal["updated", "up_to_date", "skipped"]:
    if not path.exists():
        _write_manifest_owned_deploy_file(
            path=path,
            relative_path=relative_path,
            content=content,
            executable=executable,
            manifest_hashes=manifest_hashes,
        )
        return "updated"

    current_hash = _sha256_file(path)
    desired_hash = _sha256_bytes(content.encode("utf-8"))
    if current_hash == desired_hash:
        manifest_hashes[relative_path] = current_hash
        if _ensure_deploy_file_mode(path, executable=executable):
            return "updated"
        return "up_to_date"

    previous_hash = manifest_hashes.get(relative_path)
    if previous_hash is None:
        if force:
            print(f"force updating managed file without manifest ownership: {path}")
            _write_manifest_owned_deploy_file(
                path=path,
                relative_path=relative_path,
                content=content,
                executable=executable,
                manifest_hashes=manifest_hashes,
            )
            return "updated"
        print(f"skipped managed file without manifest ownership: {path}")
        return "skipped"
    if current_hash != previous_hash:
        if force:
            print(f"force updating managed file with local edits: {path}")
            _write_manifest_owned_deploy_file(
                path=path,
                relative_path=relative_path,
                content=content,
                executable=executable,
                manifest_hashes=manifest_hashes,
            )
            return "updated"
        print(f"skipped managed file with local edits: {path}")
        return "skipped"

    _write_manifest_owned_deploy_file(
        path=path,
        relative_path=relative_path,
        content=content,
        executable=executable,
        manifest_hashes=manifest_hashes,
    )
    return "updated"


def _run_deploy_docker(argv: Sequence[str]) -> int:
    parsed = _parse_docker_deploy_args(argv)
    if parsed is None:
        return 2

    deploy_dir = parsed.directory
    paths = _deploy_managed_paths(deploy_dir)
    compose_text = _deploy_template_text("compose.yaml")
    helper_text = _deploy_template_text("arbiter-docker")

    if parsed.action == "init":
        manifest_path = _deploy_manifest_path(deploy_dir)
        init_paths = [
            paths["compose"],
            paths["docker_env"],
            paths["requirements"],
            paths["helper"],
            manifest_path,
        ]
        existing = [path for path in init_paths if path.exists()]
        if existing:
            print_cli_error(
                f"refusing to overwrite existing deployment file: {existing[0]}",
                area="deploy",
                details=["use update to refresh generated files"],
            )
            return 1
        requirement_resolution = _resolve_docker_deploy_requirements(
            parsed.requirements,
            wheel_dir=deploy_dir / "wheels",
        )
        if requirement_resolution is None:
            return 2
        if not _ensure_writable_wheel_dir(deploy_dir / "wheels"):
            return 1
        manifest_hashes: dict[str, str] = {}
        _write_manifest_owned_deploy_file(
            path=paths["compose"],
            relative_path="compose.yaml",
            content=compose_text,
            executable=False,
            manifest_hashes=manifest_hashes,
        )
        _write_deploy_file(
            paths["docker_env"],
            _format_docker_compose_env_file(existing_values={}),
        )
        _write_deploy_file(
            paths["requirements"],
            _format_deploy_requirements(requirement_resolution.requirements),
        )
        _write_manifest_owned_deploy_file(
            path=paths["helper"],
            relative_path="arbiter-docker",
            content=helper_text,
            executable=True,
            manifest_hashes=manifest_hashes,
        )
        _write_deploy_manifest(deploy_dir, file_hashes=manifest_hashes)
        (deploy_dir / "conf").mkdir(exist_ok=True)
        print("")
        print("Next steps:")
        print(f"  bootstrap or copy an Arbiter config into {deploy_dir / 'conf'}")
        print(f"  {paths['helper']} sync-env")
        print(f"  {paths['helper']} edit-env")
        print(f"  {paths['helper']} up")
        return 0

    if parsed.action == "update":
        deploy_dir.mkdir(parents=True, exist_ok=True)
        if not _ensure_writable_wheel_dir(deploy_dir / "wheels"):
            return 1
        manifest_hashes = _load_deploy_manifest(deploy_dir)
        original_manifest_hashes = dict(manifest_hashes)
        update_statuses = [
            _update_manifest_owned_deploy_file(
                path=paths["compose"],
                relative_path="compose.yaml",
                content=compose_text,
                executable=False,
                manifest_hashes=manifest_hashes,
                force=parsed.force,
            ),
            _update_manifest_owned_deploy_file(
                path=paths["helper"],
                relative_path="arbiter-docker",
                content=helper_text,
                executable=True,
                manifest_hashes=manifest_hashes,
                force=parsed.force,
            ),
        ]
        try:
            existing_docker_env = _read_env_file_values(
                paths["docker_env"],
                missing_ok=True,
            )
        except ValueError as exc:
            print_cli_error(str(exc), area="deploy")
            return 1
        docker_env_content = _format_docker_compose_env_file(existing_docker_env)
        wrote_local_state = False
        update_requirement_resolution: DockerDeployRequirements | None = None
        refresh_existing_requirements = False
        if parsed.force and paths["requirements"].exists():
            update_requirement_resolution = _resolve_docker_deploy_requirements(
                parsed.requirements,
                wheel_dir=deploy_dir / "wheels",
            )
            if update_requirement_resolution is None:
                return 2
            if parsed.requirements:
                refresh_existing_requirements = True
            else:
                existing_names = _deploy_requirement_names(
                    _read_deploy_requirements(paths["requirements"])
                )
                resolved_names = _deploy_requirement_names(
                    update_requirement_resolution.requirements
                )
                refresh_existing_requirements = (
                    existing_names is not None and existing_names == resolved_names
                )
        if not paths["requirements"].exists() or refresh_existing_requirements:
            if update_requirement_resolution is None:
                update_requirement_resolution = _resolve_docker_deploy_requirements(
                    parsed.requirements,
                    wheel_dir=deploy_dir / "wheels",
                )
                if update_requirement_resolution is None:
                    return 2
            if paths["requirements"].exists() and refresh_existing_requirements:
                print(f"force updating requirements file: {paths['requirements']}")
            _write_deploy_file(
                paths["requirements"],
                _format_deploy_requirements(update_requirement_resolution.requirements),
            )
            wrote_local_state = True
        if (
            not paths["docker_env"].exists()
            or paths["docker_env"].read_text(encoding="utf-8") != docker_env_content
        ):
            _write_deploy_file(paths["docker_env"], docker_env_content)
            wrote_local_state = True
        if manifest_hashes != original_manifest_hashes:
            _write_deploy_manifest(deploy_dir, file_hashes=manifest_hashes)
        elif all(status == "up_to_date" for status in update_statuses) and not (
            wrote_local_state
        ):
            print(f"Files already up to date: {deploy_dir}")
        (deploy_dir / "conf").mkdir(exist_ok=True)
        return 0

    raise AssertionError(f"unknown docker deploy action: {parsed.action}")


def _run_serve(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> int:
    try:
        cfg = compose_config(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
            enforce_runtime_permissions=True,
        )
        app_config = _instantiate_app_config(cfg)
        ensure_runnable_config(app_config)
        log_startup_summary(app_config)
        server = build_server(app_config)
        _run_server(server, cast(TransportMode, app_config.arbiter.server.transport))
    except KeyboardInterrupt:
        print("Arbiter server stopped.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print_cli_error(str(exc), area="config")
        return 1
    return 0


def _run_config_check(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> int:
    try:
        cfg = compose_config(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
        )
        print(config_check_summary(cfg))
    except ValueError as exc:
        print_cli_error(str(exc), area="config")
        return 1
    return 0


def _run_config_show(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
    resolve: bool,
) -> int:
    try:
        cfg = compose_config(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
        )
        print(OmegaConf.to_yaml(cfg, resolve=resolve), end="")
    except ValueError as exc:
        print_cli_error(str(exc), area="config")
        return 1
    return 0


def _ensure_config_dir(config_dir: str | None) -> Path | None:
    return Path(DEFAULT_CONFIG_DIR if config_dir is None else config_dir).expanduser()


def _write_bootstrap_file(path: Path, content: str, *, force: bool) -> int:
    if path.exists() and not force:
        print_cli_error(
            f"refusing to overwrite existing file: {path}",
            area="bootstrap",
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_with_mode(path, content, CONFIG_FILE_MODE)
    print(f"wrote {path}")
    return 0


def _write_bootstrap_files(
    files: Sequence[tuple[Path, str]],
    *,
    force: bool,
) -> int:
    for path, _content in files:
        if path.exists() and not force:
            print_cli_error(
                f"refusing to overwrite existing file: {path}",
                area="bootstrap",
            )
            return 1
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_with_mode(path, content, CONFIG_FILE_MODE)
        print(f"wrote {path}")
    return 0


def _run_bootstrap_arbiter(
    *,
    config_dir: str | None,
    config_name: str,
    force: bool,
) -> int:
    config_dir_path = _ensure_config_dir(config_dir)
    if config_dir_path is None:
        return 2
    if not BOOTSTRAP_NAME_PATTERN.fullmatch(config_name):
        print_cli_error(
            "config name must contain only letters, numbers, underscores, and "
            "dashes.",
            area="bootstrap",
        )
        return 2
    return _write_bootstrap_files(
        [
            (config_dir_path / f"{config_name}.yaml", MAIN_CONFIG_TEMPLATE),
            (config_dir_path / "arbiter" / "server.yaml", SERVER_CONFIG_TEMPLATE),
        ],
        force=force,
    )


def _bootstrap_object_path(
    *,
    config_dir: Path,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
) -> Path:
    return config_dir / "arbiter" / kind / plugin / f"{name}.yaml"


def _validate_bootstrap_object_args(plugin: str, name: str) -> bool:
    for label, value in (("plugin", plugin), ("name", name)):
        if not BOOTSTRAP_NAME_PATTERN.fullmatch(value):
            print_cli_error(
                f"{label} must contain only letters, numbers, underscores, and "
                "dashes.",
                area="bootstrap",
            )
            return False
    return True


def _load_plugin_example_yaml(
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
) -> str | None:
    plugins = _service_plugin_map(discover_service_plugins())
    service_plugin = plugins.get(plugin)
    if service_plugin is None:
        print_cli_error(f"service plugin is not installed: {plugin}", area="bootstrap")
        return None

    node = service_plugin.bootstrap_config(kind=kind, name=name)
    if node is None:
        print_cli_error(
            f"service plugin does not provide an {kind} bootstrap example: {plugin}",
            area="bootstrap",
        )
        return None
    if isinstance(node, str):
        return node
    return OmegaConf.to_yaml(node, resolve=False)


def _bootstrap_account_policy_name(account_name: str) -> str:
    return f"{account_name}_policy"


def _config_group_for_kind(kind: BootstrapObjectKind) -> str:
    return f"arbiter/{kind}"


def _config_group_item(plugin: str, name: str) -> str:
    return f"{plugin}/{name}"


def _config_file_path(config_dir: Path, config_name: str) -> Path:
    return config_dir / f"{config_name}.yaml"


def _load_main_config_lines(config_file: Path) -> list[str] | None:
    if not config_file.exists():
        print_cli_error(
            f"main config not found: {config_file}; run bootstrap arbiter first",
            area="config",
        )
        return None
    lines = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    if "defaults:\n" not in lines:
        print_cli_error(
            f"main config does not contain a defaults list: {config_file}",
            area="config",
        )
        return None
    return lines


def _find_defaults_group(lines: Sequence[str], group: str) -> tuple[int, int] | None:
    start_index = None
    for index, line in enumerate(lines):
        if line == f"  - {group}: []\n" or line == f"  - {group}:\n":
            start_index = index
            break
    if start_index is None:
        return None
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        if lines[index].startswith("  - "):
            end_index = index
            break
    return start_index, end_index


def _insert_defaults_group(lines: list[str], group: str, items: Sequence[str]) -> None:
    if "  - _self_\n" not in lines:
        raise ValueError("main config defaults list must contain _self_")
    self_index = lines.index("  - _self_\n")
    lines[self_index:self_index] = [
        f"  - {group}:\n",
        *[f"    - {item}\n" for item in items],
    ]


def _active_group_items(lines: Sequence[str], group: str) -> list[str]:
    group_span = _find_defaults_group(lines, group)
    if group_span is None:
        return []
    start_index, end_index = group_span
    if lines[start_index] == f"  - {group}: []\n":
        return []
    items: list[str] = []
    for line in lines[start_index + 1 : end_index]:
        match = GROUP_SELECTION_PATTERN.match(line.strip())
        if match is not None:
            items.append(match.group("item"))
    return items


def _set_group_items(lines: list[str], group: str, items: Sequence[str]) -> bool:
    group_span = _find_defaults_group(lines, group)
    unique_items = list(dict.fromkeys(items))
    if group_span is None:
        if not unique_items:
            return False
        _insert_defaults_group(lines, group, unique_items)
        return True
    start_index, end_index = group_span
    replacement = (
        []
        if not unique_items
        else [f"  - {group}:\n", *[f"    - {item}\n" for item in unique_items]]
    )
    if lines[start_index:end_index] == replacement:
        return False
    lines[start_index:end_index] = replacement
    return True


def _add_group_item(lines: list[str], group: str, item: str) -> bool:
    items = _active_group_items(lines, group)
    if item in items:
        return False
    items.append(item)
    return _set_group_items(lines, group, items)


def _remove_group_item(lines: list[str], group: str, item: str) -> bool:
    items = _active_group_items(lines, group)
    if item not in items:
        return False
    return _set_group_items(
        lines, group, [existing for existing in items if existing != item]
    )


def _active_default_configs(
    lines: Sequence[str],
    *,
    plugin: str,
    kind: BootstrapObjectKind,
) -> list[str]:
    prefix = f"{plugin}/"
    return [
        item.removeprefix(prefix)
        for item in _active_group_items(lines, _config_group_for_kind(kind))
        if item.startswith(prefix)
    ]


def _read_account_policy(
    *,
    config_dir: Path,
    plugin: str,
    account_name: str,
) -> str | None:
    account_file = _bootstrap_object_path(
        config_dir=config_dir,
        plugin=plugin,
        kind="account",
        name=account_name,
    )
    if not account_file.exists():
        print_cli_error(f"account config not found: {account_file}", area="config")
        return None
    cfg = OmegaConf.load(account_file)
    policy = OmegaConf.select(cfg, "policy")
    if not isinstance(policy, str) or not policy:
        print_cli_error(
            f"account config must define a non-empty policy: {account_file}",
            area="config",
        )
        return None
    return policy


def _ensure_config_object_file(
    *,
    config_dir: Path,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
) -> bool:
    object_file = _bootstrap_object_path(
        config_dir=config_dir,
        plugin=plugin,
        kind=kind,
        name=name,
    )
    if not object_file.exists():
        print_cli_error(f"{kind} config not found: {object_file}", area="config")
        return False
    return True


def _config_object_exists(
    *,
    config_dir: Path,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
) -> bool:
    return _bootstrap_object_path(
        config_dir=config_dir,
        plugin=plugin,
        kind=kind,
        name=name,
    ).exists()


def _resolve_policy_config_name(
    *,
    config_dir: Path,
    plugin: str,
    account_name: str,
    policy_name: str,
) -> str | None:
    for candidate in (policy_name, account_name):
        if _config_object_exists(
            config_dir=config_dir,
            plugin=plugin,
            kind="policy",
            name=candidate,
        ):
            return candidate
    print_cli_error(
        "policy config not found for account policy "
        f"{policy_name}: expected "
        f"{config_dir / 'arbiter' / 'policy' / plugin / f'{policy_name}.yaml'} "
        "or "
        f"{config_dir / 'arbiter' / 'policy' / plugin / f'{account_name}.yaml'}",
        area="config",
    )
    return None


def _write_main_config_lines(config_file: Path, lines: Sequence[str]) -> None:
    _write_text_with_mode(config_file, "".join(lines), CONFIG_FILE_MODE)


def _run_config_activate_account(
    *,
    config_dir: str,
    config_name: str,
    plugin: str,
    name: str,
) -> int:
    if not _validate_bootstrap_object_args(plugin, name):
        return 2
    config_dir_path = Path(config_dir).expanduser()
    if not _ensure_config_object_file(
        config_dir=config_dir_path,
        plugin=plugin,
        kind="account",
        name=name,
    ):
        return 1
    policy_name = _read_account_policy(
        config_dir=config_dir_path,
        plugin=plugin,
        account_name=name,
    )
    if policy_name is None:
        return 1
    policy_config_name = _resolve_policy_config_name(
        config_dir=config_dir_path,
        plugin=plugin,
        account_name=name,
        policy_name=policy_name,
    )
    if policy_config_name is None:
        return 1
    config_file = _config_file_path(config_dir_path, config_name)
    lines = _load_main_config_lines(config_file)
    if lines is None:
        return 1
    try:
        changed_account = _add_group_item(
            lines,
            _config_group_for_kind("account"),
            _config_group_item(plugin, name),
        )
        changed_policy = _add_group_item(
            lines,
            _config_group_for_kind("policy"),
            _config_group_item(plugin, policy_config_name),
        )
    except ValueError as exc:
        print_cli_error(str(exc), area="config")
        return 1
    if changed_account or changed_policy:
        _write_main_config_lines(config_file, lines)
        print(f"updated {config_file}")
    else:
        print(f"account already active: {plugin}/{name}")
    return 0


def _run_config_deactivate_account(
    *,
    config_dir: str,
    config_name: str,
    plugin: str,
    name: str,
) -> int:
    if not _validate_bootstrap_object_args(plugin, name):
        return 2
    config_dir_path = Path(config_dir).expanduser()
    policy_name = _read_account_policy(
        config_dir=config_dir_path,
        plugin=plugin,
        account_name=name,
    )
    if policy_name is None:
        return 1
    config_file = _config_file_path(config_dir_path, config_name)
    lines = _load_main_config_lines(config_file)
    if lines is None:
        return 1
    changed = _remove_group_item(
        lines,
        _config_group_for_kind("account"),
        _config_group_item(plugin, name),
    )
    remaining_account_names = _active_default_configs(
        lines,
        plugin=plugin,
        kind="account",
    )
    policy_still_used = False
    for remaining_account_name in remaining_account_names:
        remaining_policy = _read_account_policy(
            config_dir=config_dir_path,
            plugin=plugin,
            account_name=remaining_account_name,
        )
        if remaining_policy is None:
            return 1
        if remaining_policy == policy_name:
            policy_still_used = True
            break
    if not policy_still_used:
        policy_config_name = _resolve_policy_config_name(
            config_dir=config_dir_path,
            plugin=plugin,
            account_name=name,
            policy_name=policy_name,
        )
        if policy_config_name is None:
            return 1
        changed = (
            _remove_group_item(
                lines,
                _config_group_for_kind("policy"),
                _config_group_item(plugin, policy_config_name),
            )
            or changed
        )
    if changed:
        _write_main_config_lines(config_file, lines)
        print(f"updated {config_file}")
    else:
        print(f"account already inactive: {plugin}/{name}")
    return 0


def _run_config_account_activation(
    *,
    action: str,
    config_dir: str,
    config_name: str,
    plugin: str,
    name: str,
) -> int:
    if action == "activate":
        return _run_config_activate_account(
            config_dir=config_dir,
            config_name=config_name,
            plugin=plugin,
            name=name,
        )
    if action == "deactivate":
        return _run_config_deactivate_account(
            config_dir=config_dir,
            config_name=config_name,
            plugin=plugin,
            name=name,
        )
    raise AssertionError(f"unknown activation action: {action}")


def _print_bootstrap_activation_hint(
    *,
    config_dir: Path,
    config_name: str,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
) -> None:
    config_file = config_dir / f"{config_name}.yaml"
    print("")
    if kind == "account":
        print("Edit the generated account and policy files, then activate the account:")
        print(
            f"  arbiter-server --config-dir {config_dir} "
            f"config activate account {plugin} {name}"
        )
        print("")
        print("Then inspect the composed config with:")
        print(f"  arbiter-server --config-dir {config_dir} config show")
        return
    print(f"To activate the generated policy, add this to {config_file}:")
    print("defaults:")
    print(f"  - {_config_group_for_kind('policy')}:")
    print(f"    - {_config_group_item(plugin, name)}")
    print("")
    print("Then inspect the composed config with:")
    print(f"  arbiter-server --config-dir {config_dir} config show")


def _run_plugin_bootstrap(
    *,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
    config_dir: str | None,
    config_name: str,
    force: bool,
) -> int:
    config_dir_path = _ensure_config_dir(config_dir)
    if config_dir_path is None:
        return 2
    if not _validate_bootstrap_object_args(plugin, name):
        return 2
    content = _load_plugin_example_yaml(plugin, kind, name)
    if content is None:
        return 1
    files = [
        (
            _bootstrap_object_path(
                config_dir=config_dir_path,
                plugin=plugin,
                kind=kind,
                name=name,
            ),
            content,
        )
    ]
    if kind == "account":
        policy_name = _bootstrap_account_policy_name(name)
        policy_content = _load_plugin_example_yaml(plugin, "policy", policy_name)
        if policy_content is None:
            return 1
        files.append(
            (
                _bootstrap_object_path(
                    config_dir=config_dir_path,
                    plugin=plugin,
                    kind="policy",
                    name=policy_name,
                ),
                policy_content,
            )
        )
    result = _write_bootstrap_files(
        files,
        force=force,
    )
    if result == 0:
        _print_bootstrap_activation_hint(
            config_dir=config_dir_path,
            config_name=config_name,
            plugin=plugin,
            kind=kind,
            name=name,
        )
    return result


def _add_override_arguments(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    parser.add_argument(
        "overrides",
        nargs=argparse.REMAINDER,
        help=help_text,
    )


def _extract_global_config_args(args: Sequence[str]) -> list[str]:
    extracted: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            remaining.extend(args[index:])
            break
        if arg in {"--config-dir", "--config-name"}:
            extracted.append(arg)
            if index + 1 < len(args):
                extracted.append(args[index + 1])
                index += 2
                continue
            index += 1
            continue
        if arg.startswith("--config-dir=") or arg.startswith("--config-name="):
            extracted.append(arg)
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return [*extracted, *remaining]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter-server",
        description="Policy-controlled MCP gateway for agent-accessible services.",
    )
    parser.add_argument(
        "--config-dir",
        default=DEFAULT_CONFIG_DIR,
        help=f"filesystem directory containing the root Hydra config (default: {DEFAULT_CONFIG_DIR})",
    )
    parser.add_argument(
        "--config-name",
        default=DEFAULT_SERVER_CONFIG_NAME,
        help="root config file name without .yaml",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="run the Arbiter MCP server")
    _add_override_arguments(
        serve,
        help_text="Hydra-style config overrides applied before serving",
    )

    config = subcommands.add_parser("config", help="inspect and validate config")
    config_subcommands = config.add_subparsers(dest="config_command", required=True)
    check = config_subcommands.add_parser(
        "check",
        help="validate config and service runtime construction without serving",
    )
    _add_override_arguments(
        check,
        help_text="Hydra-style config overrides applied before validation",
    )
    show = config_subcommands.add_parser(
        "show",
        help="print the composed Arbiter config",
    )
    show.add_argument(
        "--resolve",
        action="store_true",
        help="resolve OmegaConf interpolations before printing",
    )
    _add_override_arguments(
        show,
        help_text="Hydra-style config overrides applied before printing",
    )
    for activation_action in ("activate", "deactivate"):
        activation = config_subcommands.add_parser(
            activation_action,
            help=f"{activation_action} a config object in the main defaults list",
        )
        activation.add_argument("kind", choices=["account"])
        activation.add_argument("plugin")
        activation.add_argument("name")

    bootstrap = subcommands.add_parser("bootstrap", help="create config templates")
    bootstrap_subcommands = bootstrap.add_subparsers(
        dest="bootstrap_command",
        required=True,
    )
    bootstrap_arbiter = bootstrap_subcommands.add_parser(
        "arbiter",
        help="create the main Arbiter config",
    )
    bootstrap_arbiter.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config file",
    )
    bootstrap_plugin = bootstrap_subcommands.add_parser(
        "plugin",
        help="create a plugin-owned account or policy template",
    )
    bootstrap_plugin.add_argument("plugin")
    bootstrap_plugin.add_argument("kind", choices=["account", "policy"])
    bootstrap_plugin.add_argument("name")
    bootstrap_plugin.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config object file",
    )

    env = subcommands.add_parser("env", help="inspect and bootstrap env files")
    env_subcommands = env.add_subparsers(dest="env_command", required=True)
    env_check = env_subcommands.add_parser(
        "check",
        help="check that all config env references are satisfied",
    )
    _add_override_arguments(
        env_check,
        help_text="Hydra-style config overrides applied before checking env",
    )
    env_bootstrap = env_subcommands.add_parser(
        "bootstrap",
        help="rebuild the configured env file with missing variables",
    )
    _add_override_arguments(
        env_bootstrap,
        help_text="Hydra-style config overrides applied before bootstrapping env",
    )

    version_command = subcommands.add_parser(
        "version",
        help="print Arbiter core and plugin versions",
    )
    version_command.add_argument(
        "--json",
        action="store_true",
        help="print version information as JSON",
    )

    deploy = subcommands.add_parser("deploy", help="create deployment files")
    deploy_subcommands = deploy.add_subparsers(dest="deploy_target", required=True)
    deploy_docker = deploy_subcommands.add_parser(
        "docker",
        help="create or update a local Docker deployment directory",
    )
    deploy_docker.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help=(
            "init or update plus optional docker.dir=PATH and "
            "docker.requirement=REQUIREMENT"
        ),
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
    args = _extract_global_config_args(list(sys.argv[1:] if argv is None else argv))
    parser = _build_parser()

    if args == ["-h"] or args == ["--help"]:
        parser.print_help()
        return 0

    namespace = parser.parse_args(args)
    if namespace.command == "serve":
        return _run_serve(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
        )
    if namespace.command == "config" and namespace.config_command == "check":
        return _run_config_check(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
        )
    if namespace.command == "config" and namespace.config_command == "show":
        return _run_config_show(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
            resolve=namespace.resolve,
        )
    if namespace.command == "config" and namespace.config_command in {
        "activate",
        "deactivate",
    }:
        return _run_config_account_activation(
            action=namespace.config_command,
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            plugin=namespace.plugin,
            name=namespace.name,
        )
    if namespace.command == "env" and namespace.env_command == "check":
        return _run_env_check(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
        )
    if namespace.command == "env" and namespace.env_command == "bootstrap":
        return _run_env_bootstrap(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
        )
    if namespace.command == "version":
        _print_runtime_version_info(as_json=namespace.json)
        return 0
    if namespace.command == "deploy" and namespace.deploy_target == "docker":
        return _run_deploy_docker(namespace.args)
    if namespace.command == "plugins" and namespace.plugins_command == "list":
        if namespace.json:
            _print_runtime_version_info(as_json=True)
        else:
            for name in service_plugin_names():
                print(name)
        return 0
    if namespace.command == "bootstrap" and namespace.bootstrap_command == "arbiter":
        return _run_bootstrap_arbiter(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            force=namespace.force,
        )
    if namespace.command == "bootstrap" and namespace.bootstrap_command == "plugin":
        return _run_plugin_bootstrap(
            plugin=namespace.plugin,
            kind=cast(BootstrapObjectKind, namespace.kind),
            name=namespace.name,
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            force=namespace.force,
        )

    parser.error("unknown command")
