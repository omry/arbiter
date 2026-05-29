from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

from .app import AgentArbiterApp
from .config import (
    AppConfig,
    ArbiterConfig,
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
BootstrapObjectKind = Literal["account", "policy"]
CLI_COMMANDS = {"serve", "config", "plugins", "bootstrap"}
BOOTSTRAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
GROUP_SELECTION_PATTERN = re.compile(
    r"^\s*-\s*(?P<item>[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?)\s*(?:#.*)?$"
)
MAIN_CONFIG_TEMPLATE = """defaults:
# Agent Arbiter composes this config at startup from the defaults below.
# Inspect the composed config with:
#   agent-arbiter --config-dir <dir> --config-name config config show
# Override composed values with Hydra overrides, for example:
#   agent-arbiter --config-dir <dir> serve arbiter.server.port=8025
  - arbiter: server
  - _self_
"""
SERVER_CONFIG_TEMPLATE = """# @package arbiter
server:
  name: agent-arbiter
  transport: streamable-http
  host: 127.0.0.1
  port: 8000
  path: /mcp
  stateless_http: true
  json_response: true
"""


def _to_object(value: Any) -> Any:
    if OmegaConf.is_config(value):
        return OmegaConf.to_object(value)
    return value


def _select_object(cfg: DictConfig, key: str, default: Any) -> Any:
    value = OmegaConf.select(cfg, key, default=default)
    return _to_object(value)


def _instantiate_app_config_from_hydra(cfg: DictConfig) -> AppConfig:
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
    return AppConfig(
        arbiter=ArbiterConfig(
            server=server,
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
    for service_name in configured_service_names(cfg.arbiter.account):
        accounts = cfg.arbiter.account.get(service_name, {})
        account_names = sorted(str(account_name) for account_name in accounts)
        summaries.append(f"{service_name}:{_csv_or_none(account_names)}")
    return ";".join(summaries) if summaries else "none"


def log_startup_summary(cfg: AppConfig) -> None:
    active_services = configured_service_names(cfg.arbiter.account)

    LOGGER.info(
        "Agent Arbiter starting version=%s transport=%s bind=%s:%s%s "
        "services=%s service_accounts=%s",
        package_version(),
        cfg.arbiter.server.transport,
        cfg.arbiter.server.host,
        cfg.arbiter.server.port,
        cfg.arbiter.server.path,
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
            "config must define at least one service account before Agent "
            "Arbiter can run\n"
            f"currently installed arbiter plugins: "
            f"{_installed_plugin_summary(service_plugins)}\n"
            "use `agent-arbiter --config-dir DIR bootstrap plugin PLUGIN "
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
        app_config.arbiter.server.name,
        stateless_http=app_config.arbiter.server.stateless_http,
        json_response=app_config.arbiter.server.json_response,
    )
    server.settings.host = app_config.arbiter.server.host
    server.settings.port = app_config.arbiter.server.port
    server.settings.streamable_http_path = app_config.arbiter.server.path

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


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    if args and args[0] == "--":
        return list(args[1:])
    return list(args)


def compose_config(
    *,
    config_dir: str | Path,
    config_name: str,
    overrides: Sequence[str] = (),
) -> DictConfig:
    register_configs()
    config_dir_path = Path(config_dir).expanduser().resolve()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(config_dir_path),
        job_name="agent-arbiter",
    ):
        return compose(
            config_name=config_name,
            overrides=list(_strip_arg_separator(overrides)),
        )


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
        )
        app_config = _instantiate_app_config(cfg)
        ensure_runnable_config(app_config)
        log_startup_summary(app_config)
        server = build_server(app_config)
        _run_server(server, cast(TransportMode, app_config.arbiter.server.transport))
    except KeyboardInterrupt:
        print("Agent Arbiter server stopped.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"Agent Arbiter config error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_config_check(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
) -> int:
    cfg = compose_config(
        config_dir=config_dir,
        config_name=config_name,
        overrides=overrides,
    )
    try:
        print(config_check_summary(cfg))
    except ValueError as exc:
        print(f"Agent Arbiter config error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_config_show(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
    resolve: bool,
) -> int:
    cfg = compose_config(
        config_dir=config_dir,
        config_name=config_name,
        overrides=overrides,
    )
    print(OmegaConf.to_yaml(cfg, resolve=resolve), end="")
    return 0


def _ensure_config_dir(config_dir: str | None) -> Path | None:
    if config_dir is None:
        print(
            "Agent Arbiter bootstrap requires an explicit config directory; "
            "pass --config-dir DIR.",
            file=sys.stderr,
        )
        return None
    return Path(config_dir).expanduser()


def _write_bootstrap_file(path: Path, content: str, *, force: bool) -> int:
    if path.exists() and not force:
        print(f"refusing to overwrite existing file: {path}", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def _write_bootstrap_files(
    files: Sequence[tuple[Path, str]],
    *,
    force: bool,
) -> int:
    for path, _content in files:
        if path.exists() and not force:
            print(f"refusing to overwrite existing file: {path}", file=sys.stderr)
            return 1
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
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
        print(
            "config name must contain only letters, numbers, underscores, and "
            "dashes.",
            file=sys.stderr,
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
            print(
                f"{label} must contain only letters, numbers, underscores, and "
                "dashes.",
                file=sys.stderr,
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
        print(f"service plugin is not installed: {plugin}", file=sys.stderr)
        return None

    node = service_plugin.bootstrap_config(kind=kind, name=name)
    if node is None:
        print(
            f"service plugin does not provide an {kind} bootstrap example: {plugin}",
            file=sys.stderr,
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
        print(
            f"main config not found: {config_file}; run bootstrap arbiter first",
            file=sys.stderr,
        )
        return None
    lines = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    if "defaults:\n" not in lines:
        print(
            f"main config does not contain a defaults list: {config_file}",
            file=sys.stderr,
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
        print(f"account config not found: {account_file}", file=sys.stderr)
        return None
    cfg = OmegaConf.load(account_file)
    policy = OmegaConf.select(cfg, "policy")
    if not isinstance(policy, str) or not policy:
        print(
            f"account config must define a non-empty policy: {account_file}",
            file=sys.stderr,
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
        print(f"{kind} config not found: {object_file}", file=sys.stderr)
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
    print(
        "policy config not found for account policy "
        f"{policy_name}: expected "
        f"{config_dir / 'arbiter' / 'policy' / plugin / f'{policy_name}.yaml'} "
        "or "
        f"{config_dir / 'arbiter' / 'policy' / plugin / f'{account_name}.yaml'}",
        file=sys.stderr,
    )
    return None


def _write_main_config_lines(config_file: Path, lines: Sequence[str]) -> None:
    config_file.write_text("".join(lines), encoding="utf-8")


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
        print(f"Agent Arbiter config error: {exc}", file=sys.stderr)
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
            f"  agent-arbiter --config-dir {config_dir} "
            f"config activate account {plugin} {name}"
        )
        print("")
        print("Then inspect the composed config with:")
        print(f"  agent-arbiter --config-dir {config_dir} config show")
        return
    print(f"To activate the generated policy, add this to {config_file}:")
    print("defaults:")
    print(f"  - {_config_group_for_kind('policy')}:")
    print(f"    - {_config_group_item(plugin, name)}")
    print("")
    print("Then inspect the composed config with:")
    print(f"  agent-arbiter --config-dir {config_dir} config show")


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
        prog="agent-arbiter",
        description="Policy-controlled MCP gateway for agent-accessible services.",
    )
    parser.add_argument(
        "--config-dir",
        required=True,
        help="filesystem directory containing the root Hydra config",
    )
    parser.add_argument(
        "--config-name",
        default="config",
        help="root config file name without .yaml",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="run the Agent Arbiter MCP server")
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
        help="print the composed Agent Arbiter config",
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
        help="create the main Agent Arbiter config",
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
    if namespace.command == "plugins" and namespace.plugins_command == "list":
        names = service_plugin_names()
        if namespace.json:
            print(json.dumps({"plugins": [{"name": name} for name in names]}))
        else:
            for name in names:
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
