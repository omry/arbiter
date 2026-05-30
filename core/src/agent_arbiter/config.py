from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
import logging
from pathlib import Path
from typing import Any, cast

from hydra.core.config_store import ConfigStore
from omegaconf import OmegaConf

from .services import SERVICE_PLUGIN_ENTRY_POINT_GROUP, ServicePluginFactory


LOGGER = logging.getLogger(__name__)


@dataclass
class FastMCPConfig:
    name: str = "agent-arbiter"
    transport: str = "streamable-http"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    stateless_http: bool = True
    json_response: bool = True


@dataclass
class DiscoveryConfig:
    max_account_preview_limit: int = 25
    max_operation_preview_limit: int = 25

    def __post_init__(self) -> None:
        if self.max_account_preview_limit < 1:
            raise ValueError("max_account_preview_limit must be >= 1")
        if self.max_operation_preview_limit < 1:
            raise ValueError("max_operation_preview_limit must be >= 1")


@dataclass
class Policy:
    pass


@dataclass
class ArbiterConfig:
    server: FastMCPConfig = field(default_factory=FastMCPConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    account: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    etc: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    arbiter: ArbiterConfig = field(default_factory=ArbiterConfig)


def _config_mapping_items(config: Any) -> list[tuple[str, object]]:
    if isinstance(config, Mapping):
        return [
            (str(service_name), service_config)
            for service_name, service_config in config.items()
            if not str(service_name).startswith("_")
        ]
    return [
        (service_name, service_config)
        for service_name, service_config in vars(config).items()
        if not service_name.startswith("_")
    ]


def _service_config_mapping(config: Any, service_name: str) -> Mapping[str, object]:
    if isinstance(config, Mapping):
        value = config.get(service_name, {})
    else:
        value = getattr(config, service_name, {})
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"service config must be a mapping: {service_name}")


def configured_service_names(accounts: Any) -> list[str]:
    return [
        service_name
        for service_name, service_accounts in _config_mapping_items(accounts)
        if service_accounts
    ]


def service_accounts_for(
    config: AppConfig,
    service_name: str,
) -> Mapping[str, object] | None:
    accounts = _service_config_mapping(config.arbiter.account, service_name)
    if not accounts:
        return None
    return accounts


def service_policies_for(
    config: AppConfig,
    service_name: str,
) -> Mapping[str, object]:
    return _service_config_mapping(config.arbiter.policy, service_name)


_CONFIG_SCHEMA_NAMES = (
    "agent_arbiter_app_config_schema",
    "mailgateway_app_config_schema",
)
_CONFIG_REGISTERED = False
_RESOLVERS_REGISTERED = False


def _read_secret_file(path: str) -> str:
    secret_path = Path(path).expanduser()
    try:
        return secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ValueError(f"failed to read secret file: {secret_path}") from exc


def _register_resolvers() -> None:
    global _RESOLVERS_REGISTERED
    if _RESOLVERS_REGISTERED:
        return
    if not OmegaConf.has_resolver("secret_file"):
        OmegaConf.register_new_resolver(
            "secret_file",
            _read_secret_file,
            use_cache=False,
        )
    _RESOLVERS_REGISTERED = True


def _register_core_configs(config_store: ConfigStore) -> None:
    for schema_name in _CONFIG_SCHEMA_NAMES:
        config_store.store(name=schema_name, node=AppConfig)

    config_store.store(
        group="arbiter/server",
        name="schema",
        node=FastMCPConfig,
        package="arbiter.server",
        provider="agent-arbiter-core",
    )
    config_store.store(
        group="arbiter/server",
        name="streamable-http",
        node=FastMCPConfig(),
        package="arbiter.server",
        provider="agent-arbiter-core",
    )
    config_store.store(
        group="arbiter/server",
        name="stdio",
        node=FastMCPConfig(transport="stdio"),
        package="arbiter.server",
        provider="agent-arbiter-core",
    )
    config_store.store(
        group="arbiter/server",
        name="sse",
        node=FastMCPConfig(transport="sse"),
        package="arbiter.server",
        provider="agent-arbiter-core",
    )


def _register_service_plugin_configs(config_store: ConfigStore) -> None:
    for entry_point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP):
        try:
            plugin_factory = cast(ServicePluginFactory, entry_point.load())
        except ModuleNotFoundError as exc:
            LOGGER.warning(
                "Skipping unavailable service plugin config entry point %s=%s: %s",
                entry_point.name,
                entry_point.value,
                exc,
            )
            continue
        service_plugin = plugin_factory()
        service_plugin.register_configs(config_store)


def register_configs() -> None:
    global _CONFIG_REGISTERED
    _register_resolvers()
    if _CONFIG_REGISTERED:
        return
    config_store = ConfigStore.instance()
    _register_core_configs(config_store)
    _register_service_plugin_configs(config_store)
    _CONFIG_REGISTERED = True
