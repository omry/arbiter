from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Collection
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import entry_points
import logging
from pathlib import Path
from typing import Any, cast

import yaml
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, II, ListConfig, OmegaConf
from omegaconf.basecontainer import BaseContainer
from omegaconf.errors import InterpolationResolutionError
from omegaconf._utils import get_omega_conf_dumper

from .services import SERVICE_PLUGIN_ENTRY_POINT_GROUP, ServicePluginFactory
from .services import validate_service_plugin_compatibility
from .limits import CoreLimitsConfig


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
    limits: CoreLimitsConfig = field(default_factory=CoreLimitsConfig)
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
_DISPLAY_REDACTION = ContextVar("arbiter_config_display_redaction", default=False)
_DISPLAY_REDACTION_MARKER = "<REDACTED>"


class _DisplayInterpolationResolutionError(InterpolationResolutionError):
    """Marks a resolver result that must redact its enclosing expression."""


class _DisplayResolver:
    def __init__(self, resolver: Callable[..., Any]) -> None:
        self._resolver = resolver

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if _DISPLAY_REDACTION.get():
            raise _DisplayInterpolationResolutionError()
        return self._resolver(*args, **kwargs)


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


@contextmanager
def config_for_display(cfg: DictConfig) -> Iterator[DictConfig]:
    _register_resolvers()
    # Wrap after composition, including resolvers registered by active plugins.
    # Keep OmegaConf's callbacks intact for their cache keys/context arguments;
    # other runtime contexts still delegate to the original implementation.
    for name in ("oc.env", "secret_file"):
        resolver = OmegaConf._get_resolver(name)
        if resolver is not None and not isinstance(resolver, _DisplayResolver):
            BaseContainer._resolvers[name] = _DisplayResolver(resolver)
    display = deepcopy(cfg)
    OmegaConf.clear_cache(display)
    token = _DISPLAY_REDACTION.set(True)
    try:
        yield display
    finally:
        _DISPLAY_REDACTION.reset(token)


def _display_resolved_container(
    value: DictConfig | ListConfig,
    *,
    resolved_node_cache: dict[int, Any],
) -> Any:
    if value._is_none():
        return None

    def get_node_value(key: Any) -> Any:
        node: Any = value._get_child(key)
        assert node is not None
        node_id = id(node)
        cached = resolved_node_cache.get(node_id)
        if cached is not None:
            node = cached
        else:
            node = node._maybe_dereference_node(
                throw_on_resolution_failure=True,
                resolved_node_cache=resolved_node_cache,
            )
            assert node is not None
            resolved_node_cache[node_id] = node

        if isinstance(node, (DictConfig, ListConfig)):
            return _display_resolved_container(
                node, resolved_node_cache=resolved_node_cache
            )
        child = node._value()
        return f"{child.name}" if isinstance(child, Enum) else child

    if isinstance(value, DictConfig):
        result: dict[Any, Any] = {}
        for key in value.keys():
            output_key = f"{key.name}" if isinstance(key, Enum) else key
            try:
                if OmegaConf.is_missing(value, key):
                    result[output_key] = "???"
                else:
                    result[output_key] = get_node_value(key)
            except _DisplayInterpolationResolutionError:
                result[output_key] = _DISPLAY_REDACTION_MARKER
        return result

    result_list: list[Any] = []
    for index in range(len(value)):
        try:
            if OmegaConf.is_missing(value, index):
                result_list.append("???")
            else:
                result_list.append(get_node_value(index))
        except _DisplayInterpolationResolutionError:
            result_list.append(_DISPLAY_REDACTION_MARKER)
    return result_list


def display_config_select(cfg: DictConfig, package: str, default: Any) -> Any:
    try:
        return OmegaConf.select(cfg, package, default=default)
    except _DisplayInterpolationResolutionError:
        return _DISPLAY_REDACTION_MARKER


def display_config_yaml(cfg: DictConfig | ListConfig, *, resolve: bool) -> str:
    if not resolve:
        return OmegaConf.to_yaml(cfg, resolve=False)
    content = _display_resolved_container(cfg, resolved_node_cache={})
    return cast(
        str,
        yaml.dump(
            content,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            Dumper=get_omega_conf_dumper(),
        ),
    )


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


def _register_service_plugin_configs(
    config_store: ConfigStore, names: Collection[str]
) -> None:
    for entry_point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP):
        if entry_point.name not in names:
            continue
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
        if service_plugin.name != entry_point.name:
            raise RuntimeError(
                f"service plugin entry point {entry_point.name} returned "
                f"plugin {service_plugin.name}"
            )
        service_plugin.register_configs(config_store)


def _register_service_plugin_namespaces(config_store: ConfigStore) -> None:
    # Hydra classifies group overrides before loading their selected config.
    # Advertise empty group directories from metadata so a cold deletion is a
    # group deletion. ConfigStore has no public empty-group registration API;
    # these are the same directory dictionaries created by store(group=...).
    # No schema/config nodes or plugin code are loaded here.
    for point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP):
        for domain in ("account", "policy"):
            directory = config_store.repo
            for component in ("arbiter", domain, point.name):
                directory = directory.setdefault(component, {})


def register_configs(service_names: Collection[str] = ()) -> None:
    global _CONFIG_REGISTERED
    _register_resolvers()
    config_store = ConfigStore.instance()
    if not _CONFIG_REGISTERED:
        _register_server_configs(config_store)
        _CONFIG_REGISTERED = True
    _register_service_plugin_namespaces(config_store)
    _register_service_plugin_configs(config_store, service_names)
