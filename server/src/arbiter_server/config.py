from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import entry_points
import logging
from pathlib import Path
from typing import Any, cast

from hydra.core.config_store import ConfigStore
from omegaconf import II, OmegaConf

from .services import SERVICE_PLUGIN_ENTRY_POINT_GROUP, ServicePluginFactory
from .services import validate_service_plugin_compatibility


LOGGER = logging.getLogger(__name__)


@dataclass
class HTTPServerConfig:
    scheme: str = "https"
    host: str = "127.0.0.1"
    port: int = 8075
    path: str = ""
    base_url: str = "${.scheme}://${.host}:${.port}"


def _bind_http_server_config() -> HTTPServerConfig:
    return HTTPServerConfig()


def _public_http_server_config() -> HTTPServerConfig:
    return HTTPServerConfig(
        host="127.0.0.1",
        port=II("arbiter.server.bind.port"),
        path=II("arbiter.server.bind.path"),
    )


class ServerTlsSource(str, Enum):
    SELF_SIGNED = "self-signed"
    CERT_FILES = "cert-files"


@dataclass
class ServerTlsConfig:
    source: ServerTlsSource = ServerTlsSource.SELF_SIGNED
    cert_file: str | None = None
    key_file: str | None = None


@dataclass
class ServerConfig:
    name: str = "arbiter"
    transport: str = "https"
    bind: HTTPServerConfig = field(default_factory=_bind_http_server_config)
    public: HTTPServerConfig = field(default_factory=_public_http_server_config)
    tls: ServerTlsConfig = field(default_factory=ServerTlsConfig)


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
class StorageConfig:
    server_data_dir: str | None = None
    plugin_data_dir: str | None = None


class DeploymentScope(str, Enum):
    unknown = "unknown"
    staged = "staged"
    installed = "installed"


@dataclass
class Policy:
    pass


@dataclass
class ArbiterConfig:
    env_file: str | None = None
    server: ServerConfig = field(default_factory=ServerConfig)
    deployment_scope: DeploymentScope = DeploymentScope.unknown
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
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
    config: Any,
    service_name: str,
) -> Mapping[str, object] | None:
    accounts = _service_config_mapping(config.arbiter.account, service_name)
    if not accounts:
        return None
    return accounts


def service_policies_for(
    config: Any,
    service_name: str,
) -> Mapping[str, object]:
    return _service_config_mapping(config.arbiter.policy, service_name)


_CONFIG_SCHEMA_NAMES = ("arbiter_app_config_schema",)
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


def _register_server_configs(config_store: ConfigStore) -> None:
    for schema_name in _CONFIG_SCHEMA_NAMES:
        config_store.store(name=schema_name, node=AppConfig)

    config_store.store(
        group="arbiter/server",
        name="schema",
        node=ServerConfig,
        package="arbiter.server",
        provider="arbiter-server",
    )
    config_store.store(
        group="arbiter/server",
        name="http",
        node=ServerConfig(),
        package="arbiter.server",
        provider="arbiter-server",
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
        validate_service_plugin_compatibility(service_plugin)
        service_plugin.register_configs(config_store)


def register_configs() -> None:
    global _CONFIG_REGISTERED
    _register_resolvers()
    if _CONFIG_REGISTERED:
        return
    config_store = ConfigStore.instance()
    _register_server_configs(config_store)
    _register_service_plugin_configs(config_store)
    _CONFIG_REGISTERED = True
