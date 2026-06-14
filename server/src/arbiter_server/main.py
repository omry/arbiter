from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlparse
from urllib.request import url2pathname

from hydra import compose, initialize_config_dir
from hydra.errors import CompactHydraException
from omegaconf import DictConfig, OmegaConf

from .app import ArbiterApp
from .artifacts import (
    ArtifactConsumed,
    ArtifactExpired,
    ArtifactNotFound,
    ArtifactStore,
)
from .cli_errors import print_cli_error
from .config import (
    AppConfig,
    DeploymentScope,
    StorageConfig,
    configured_service_names,
    register_configs,
    service_accounts_for,
    service_policies_for,
)
from .file_protection import ensure_runtime_config_permissions
from .plugins import discover_service_plugins
from .services import (
    SERVER_API_VERSION,
    SERVER_VERSION,
    ConfigCheckError,
    ConfigCheckIssue,
    OperationCatalog,
    RuntimeRegistry,
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    ServicePlugin,
    ServicePluginContext,
    ServicePluginFactory,
    ServiceRuntimeContext,
    check_service_plugin_config,
    service_plugin_runtime_info,
    validate_service_plugin_compatibility,
    validate_service_plugins,
)
from .storage import PluginStorage, default_plugin_data_root
from .version import arbiter_server_version, source_info

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
ARTIFACT_ROUTE_PREFIX = "/_arbiter/artifacts"
DEPLOY_MANIFEST_FILE_NAME = ".arbiter-deploy.json"
ARBITER_SERVER_PACKAGE = "arbiter-server"
ARBITER_ALL_META_PACKAGE = "arbiter-suite"
DOCKER_BUNDLE_PLUGINS_FILE_NAME = "bundle-plugins.tsv"
DOCKER_BUNDLE_PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
DOCKER_BUNDLE_PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOCKER_LOCAL_SOURCE_CONTAINER_ROOT = "/source/arbiter"
DOCKER_WHEELS_CONTAINER_ROOT = "/wheels"


def _default_container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "10001:10001"
    return f"{getuid()}:{getgid()}"


DOCKER_COMPOSE_ENV_DEFAULTS = [
    ("ARBITER_IMAGE", "python:3.11-slim"),
    ("ARBITER_CONTAINER_NAME", "arbiter-staging"),
    ("ARBITER_CONTAINER_USER", _default_container_user()),
    ("ARBITER_RESTART", "unless-stopped"),
    ("ARBITER_APP_ENV_FILE", "./conf/.env"),
    ("ARBITER_CONFIG_DIR", "./conf"),
    ("ARBITER_CONFIG_NAME", "arbiter-server"),
    ("ARBITER_REQUIREMENTS_FILE", "./requirements.txt"),
    ("ARBITER_WHEELS_DIR", "./wheels"),
    ("ARBITER_PLUGIN_DATA_DIR", "./data/plugins"),
    ("ARBITER_HOST_BIND", "127.0.0.1"),
    ("ARBITER_HOST_PORT", "18025"),
    ("ARBITER_CONTAINER_PORT", "8025"),
    ("ARBITER_PUBLIC_SCHEME", "http"),
    ("ARBITER_PUBLIC_BASE_URL", ""),
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
#   arbiter-server --config-dir <dir> serve arbiter.server.bind.port=8025
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
  bind:
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


@dataclass(frozen=True)
class DockerBundlePlugin:
    name: str
    package: str
    description: str


@dataclass(frozen=True)
class ConfigCheckReport:
    components: tuple["ConfigCheckComponentReport", ...]

    @property
    def summary(self) -> str:
        return "\n".join(self.lines)

    @property
    def lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        account_rows = []
        for component in self.components:
            account_rows.extend(component.table_rows)
        for component in self.components:
            lines.extend(component.summary_lines)
        if account_rows:
            lines.extend(_config_check_table_lines(account_rows))
        for component in self.components:
            lines.extend(component.issue_lines)
        return tuple(lines)

    @property
    def failed(self) -> bool:
        return any(component.status == "fail" for component in self.components)


@dataclass(frozen=True)
class ConfigCheckComponentReport:
    name: str
    account_results: tuple["ConfigCheckAccountResult", ...] = ()
    warnings: tuple[ConfigCheckIssue, ...] = ()
    errors: tuple[ConfigCheckIssue, ...] = ()

    @property
    def status(self) -> str:
        if self.errors:
            return "fail"
        if any(result.status == "fail" for result in self.account_results):
            return "fail"
        if self.warnings:
            return "warn"
        if any(result.status == "warn" for result in self.account_results):
            return "warn"
        return "pass"

    @property
    def lines(self) -> tuple[str, ...]:
        lines = [*self.summary_lines]
        table_rows = self.table_rows
        if table_rows:
            lines.extend(_config_check_table_lines(table_rows))
        lines.extend(self.issue_lines)
        return tuple(lines)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        return (f"{self.name}: {self.status}",)

    @property
    def issue_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        issue_lines = [
            ("fail", issue)
            for issue in self.errors
            if not _config_check_issue_has_account(issue)
        ]
        issue_lines.extend(
            ("warn", issue)
            for issue in self.warnings
            if not _config_check_issue_has_account(issue)
        )
        for severity, issue in issue_lines:
            message = _config_check_issue_message(issue)
            message_lines = message.splitlines() or [""]
            lines.append(f"- {severity}: {message_lines[0]}")
            lines.extend(f"  {line}" for line in message_lines[1:])
        return tuple(lines)

    @property
    def table_rows(self) -> tuple["_ConfigCheckTableRow", ...]:
        rows = [
            _ConfigCheckTableRow(
                plugin=self.name,
                account=result.account,
                policy=result.policy or "",
                result=result.status,
                message=result.message or "",
            )
            for result in self.account_results
        ]
        rows.extend(
            _config_check_issue_table_row(
                plugin=self.name,
                result="fail",
                issue=issue,
            )
            for issue in self.errors
            if _config_check_issue_has_account(issue)
        )
        rows.extend(
            _config_check_issue_table_row(
                plugin=self.name,
                result="warn",
                issue=issue,
            )
            for issue in self.warnings
            if _config_check_issue_has_account(issue)
        )
        return tuple(rows)


@dataclass(frozen=True)
class ConfigCheckAccountResult:
    account: str
    status: Literal["pass", "warn", "fail"]
    policy: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class _ConfigCheckTableRow:
    plugin: str
    account: str
    policy: str
    result: Literal["pass", "warn", "fail"]
    message: str


_CONFIG_CHECK_STATUS_COLORS = {
    "pass": "32",
    "warn": "33",
    "fail": "31",
}
_CONFIG_CHECK_COMPONENT_COLOR = "94"
_CONFIG_CHECK_PROGRESS_FRAMES = ("|", "/", "-", "\\")


def _config_check_color_enabled(output: object) -> bool:
    color = os.environ.get("ARBITER_COLOR", "").lower()
    if color == "always":
        return True
    if color == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(output, "isatty", None)
    return bool(callable(isatty) and isatty())


def _config_check_progress_enabled(output: object) -> bool:
    isatty = getattr(output, "isatty", None)
    return bool(callable(isatty) and isatty())


def _color_config_check_status(status: str) -> str:
    color = _CONFIG_CHECK_STATUS_COLORS.get(status)
    if color is None:
        return status
    return f"\033[{color}m{status}\033[0m"


def _color_config_check_component(component: str) -> str:
    return f"\033[{_CONFIG_CHECK_COMPONENT_COLOR}m{component}\033[0m"


class _ConfigCheckProgress:
    def __init__(
        self,
        output: object,
        *,
        color: bool,
        enabled: bool,
        interval: float = 0.1,
    ) -> None:
        self._output = output
        self._color = color
        self._enabled = enabled
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._label: str | None = None
        self._lock = threading.Lock()

    def start(self, component: str, account: str | None = None) -> None:
        if not self._enabled:
            return
        self.finish()
        self._label = f"{component}/{account}" if account is not None else component
        self._stop.clear()
        self._write_frame(_CONFIG_CHECK_PROGRESS_FRAMES[0])
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def finish(self) -> None:
        if not self._enabled:
            return
        thread = self._thread
        if thread is not None:
            self._stop.set()
            thread.join()
            self._thread = None
        if self._label is not None:
            with self._lock:
                self._write("\r\033[2K")
            self._label = None

    def _run(self) -> None:
        frame_index = 1
        while not self._stop.wait(self._interval):
            self._write_frame(
                _CONFIG_CHECK_PROGRESS_FRAMES[
                    frame_index % len(_CONFIG_CHECK_PROGRESS_FRAMES)
                ]
            )
            frame_index += 1

    def _write_frame(self, frame: str) -> None:
        label = self._label
        if label is None:
            return
        if self._color:
            label = _color_config_check_component(label)
        with self._lock:
            self._write(f"\r\033[2K{label}: testing {frame}")

    def _write(self, value: str) -> None:
        write = getattr(self._output, "write", None)
        if callable(write):
            write(value)
        flush = getattr(self._output, "flush", None)
        if callable(flush):
            flush()


def _color_config_check_line(line: str) -> str:
    match = re.fullmatch(r"([^:]+): (pass|warn|fail)", line)
    if match is not None:
        return (
            f"{_color_config_check_component(match.group(1))}: "
            f"{_color_config_check_status(match.group(2))}"
        )
    match = re.fullmatch(r"(pass|warn|fail)( +\| .*)", line)
    if match is not None:
        return f"{_color_config_check_status(match.group(1))}{match.group(2)}"
    match = re.fullmatch(r"(- )(pass|warn|fail):(.*)", line)
    if match is not None:
        return (
            f"{match.group(1)}{_color_config_check_status(match.group(2))}:"
            f"{match.group(3)}"
        )
    return line


def _config_check_table_lines(
    rows: Sequence[_ConfigCheckTableRow],
) -> tuple[str, ...]:
    headers = ("result", "plugin", "account", "policy", "message")
    raw_rows = [
        (row.result, row.plugin, row.account, row.policy, row.message) for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in raw_rows))
        for index in range(len(headers))
    ]

    def format_row(values: Sequence[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ).rstrip()

    separator = "-+-".join("-" * width for width in widths)
    return (
        format_row(headers),
        separator,
        *(format_row(row) for row in raw_rows),
    )


def _config_check_issue_has_account(issue: ConfigCheckIssue) -> bool:
    return issue.account is not None


def _config_check_issue_table_row(
    *,
    plugin: str,
    result: Literal["warn", "fail"],
    issue: ConfigCheckIssue,
) -> _ConfigCheckTableRow:
    account = issue.account
    if account is None:
        raise ValueError("config check issue table rows require an account")
    return _ConfigCheckTableRow(
        plugin=plugin,
        account=account,
        policy=issue.policy or "",
        result=result,
        message=issue.message,
    )


def _color_config_check_lines(
    lines: Sequence[str],
    *,
    color: bool,
) -> tuple[str, ...]:
    if not color:
        return tuple(lines)
    return tuple(_color_config_check_line(line) for line in lines)


def _config_check_issue_message(issue: ConfigCheckIssue) -> str:
    subject_parts = []
    if issue.account is not None:
        subject_parts.append(f"account={issue.account}")
    if issue.policy is not None:
        subject_parts.append(f"policy={issue.policy}")
    if not subject_parts:
        return issue.message
    return f"{' '.join(subject_parts)}: {issue.message}"


def _service_plugin_map(
    service_plugins: Sequence[ServicePlugin],
) -> dict[str, ServicePlugin]:
    validate_service_plugins(service_plugins)
    return {service_plugin.name: service_plugin for service_plugin in service_plugins}


def _configured_service_plugins(
    cfg: HydraConfig,
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
    available_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(cfg, available_plugins)
    shared_runtime_dependencies = runtime_dependencies or {}
    plugin_data_root = _plugin_data_root(_storage_config(cfg))
    runtimes: dict[str, object] = {}
    for service_plugin in active_service_plugins:
        accounts = service_accounts_for(cfg, service_plugin.name)
        if accounts is None:
            raise RuntimeError(
                f"service config is not configured: {service_plugin.name}"
            )
        policies = service_policies_for(cfg, service_plugin.name)
        plugin_dependencies = {
            **shared_runtime_dependencies,
            "plugin_storage": PluginStorage(
                plugin_name=service_plugin.name,
                root=plugin_data_root,
            ),
        }
        artifact_store = shared_runtime_dependencies.get("artifact_store")
        if isinstance(artifact_store, ArtifactStore):
            plugin_dependencies["artifact_store"] = artifact_store.for_plugin(
                service_plugin.name
            )
        runtime_context = ServiceRuntimeContext(
            dependencies=plugin_dependencies,
        )
        runtimes[service_plugin.name] = service_plugin.build_runtime(
            accounts=accounts,
            policies=policies,
            context=runtime_context,
        )
    _configure_sent_message_appender(cfg, runtimes)
    return ArbiterApp(RuntimeRegistry(runtimes))


@dataclass(frozen=True)
class _SentCopyDestination:
    account: str
    folder: str


class _IMAPSentMessageAppender:
    def __init__(
        self,
        *,
        imap_accounts: Mapping[str, object],
        imap_runtime: object,
    ) -> None:
        self._imap_accounts = imap_accounts
        self._imap_runtime = imap_runtime

    def _destination_from_config(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> _SentCopyDestination:
        imap_config = self._imap_accounts.get(account)
        if imap_config is None:
            raise ValueError(f"matching IMAP account is not configured: {account}")

        folders = _config_mapping_value(imap_config, "folders")
        if folder is not None:
            return _SentCopyDestination(account=account, folder=folder)

        sent_folders = [
            folder_name
            for folder_name, folder_config in sorted(folders.items())
            if _folder_kind_value(folder_config) == "SENT"
        ]
        if len(sent_folders) == 1:
            return _SentCopyDestination(account=account, folder=sent_folders[0])
        if not sent_folders:
            raise ValueError(
                f"IMAP account has no folder configured with kind=SENT: {account}"
            )
        raise ValueError(
            f"IMAP account has multiple folders configured with kind=SENT: {account}"
        )

    def check_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> _SentCopyDestination:
        return self._destination_from_config(account=account, folder=folder)

    def resolve_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> _SentCopyDestination:
        return self._destination_from_config(account=account, folder=folder)

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None:
        append_sent_message = getattr(self._imap_runtime, "append_sent_message", None)
        if not callable(append_sent_message):
            raise RuntimeError("IMAP runtime does not support sent-copy append")
        append_sent_message(
            account=account,
            folder=folder,
            message_bytes=message_bytes,
        )


def _configure_sent_message_appender(
    cfg: HydraConfig,
    runtimes: Mapping[str, object],
) -> None:
    smtp_runtime = runtimes.get("smtp")
    configure = getattr(smtp_runtime, "configure_sent_message_appender", None)
    if not callable(configure):
        return
    imap_runtime = runtimes.get("imap")
    imap_accounts = service_accounts_for(cfg, "imap")
    if imap_runtime is None or imap_accounts is None:
        return
    configure(
        _IMAPSentMessageAppender(
            imap_accounts=imap_accounts,
            imap_runtime=imap_runtime,
        )
    )


def _config_mapping_value(config: object, key: str) -> Mapping[str, object]:
    value = config.get(key, {}) if isinstance(config, Mapping) else getattr(config, key)
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"config value must be a mapping: {key}")


def _folder_kind_value(folder_config: object) -> str | None:
    kind = (
        folder_config.get("kind")
        if isinstance(folder_config, Mapping)
        else getattr(folder_config, "kind", None)
    )
    if kind is None:
        return None
    value = getattr(kind, "value", kind)
    return str(value)


def _storage_config(cfg: HydraConfig) -> StorageConfig | Any | None:
    if OmegaConf.is_config(cfg):
        return OmegaConf.select(cast(Any, cfg), "arbiter.storage")
    return getattr(cfg.arbiter, "storage", None)


def _plugin_data_root(storage_config: StorageConfig | Any | None) -> Path:
    if storage_config is None:
        return default_plugin_data_root()
    if OmegaConf.is_config(storage_config):
        plugin_data_dir = OmegaConf.select(cast(Any, storage_config), "plugin_data_dir")
    else:
        plugin_data_dir = getattr(storage_config, "plugin_data_dir", None)
    if plugin_data_dir is not None:
        return Path(str(plugin_data_dir)).expanduser().resolve()
    return default_plugin_data_root()


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def _service_accounts_summary(cfg: HydraConfig) -> str:
    summaries: list[str] = []
    for service_name in configured_service_names(cfg.arbiter.account):
        accounts = cfg.arbiter.account.get(service_name, {})
        account_names = sorted(str(account_name) for account_name in accounts)
        summaries.append(f"{service_name}:{_csv_or_none(account_names)}")
    return ";".join(summaries) if summaries else "none"


def _server_mcp_url(cfg: HydraConfig) -> str:
    if cfg.arbiter.server.transport == "stdio":
        return "stdio"
    return f"{_server_base_url(cfg)}{_server_public_path(cfg)}"


def _server_public_path(cfg: HydraConfig) -> str:
    public_path = cfg.arbiter.server.public.path
    if "${" in public_path:
        public_path = cfg.arbiter.server.bind.path
    return public_path


def _server_base_url(cfg: HydraConfig) -> str:
    if cfg.arbiter.server.transport == "stdio":
        raise ValueError("stdio transport does not expose HTTP artifact URLs")
    public_base_url = cfg.arbiter.server.public.base_url.strip()
    if "${" in public_base_url:
        public_port = str(cfg.arbiter.server.public.port)
        if "${" in public_port:
            public_port = str(cfg.arbiter.server.bind.port)
        public_base_url = (
            f"{cfg.arbiter.server.public.scheme}://"
            f"{cfg.arbiter.server.public.host}:"
            f"{public_port}"
        )
    if not public_base_url:
        raise ValueError("arbiter.server.public.base_url must be non-empty")
    return public_base_url.rstrip("/")


def _artifact_base_url(cfg: HydraConfig) -> str:
    return f"{_server_base_url(cfg)}{ARTIFACT_ROUTE_PREFIX}"


def _deployment_scope_value(deployment_scope: DeploymentScope | str) -> str:
    if isinstance(deployment_scope, DeploymentScope):
        return deployment_scope.value
    return str(deployment_scope)


def log_startup_summary(cfg: HydraConfig) -> None:
    active_services = configured_service_names(cfg.arbiter.account)

    LOGGER.info(
        "Arbiter starting version=%s deployment_scope=%s transport=%s bind=%s:%s%s "
        "mcp_url=%s services=%s service_accounts=%s",
        arbiter_server_version(),
        _deployment_scope_value(cfg.arbiter.deployment_scope),
        cfg.arbiter.server.transport,
        cfg.arbiter.server.bind.host,
        cfg.arbiter.server.bind.port,
        cfg.arbiter.server.bind.path,
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
    cfg: HydraConfig,
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


def check_config(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> ConfigCheckReport:
    return config_check_report(cfg, service_plugins=service_plugins)


def config_check_summary(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> str:
    return config_check_report(cfg, service_plugins=service_plugins).summary


def _account_policy_name(account_config: object) -> str | None:
    policy = (
        account_config.get("policy")
        if isinstance(account_config, Mapping)
        else getattr(account_config, "policy", None)
    )
    return None if policy is None else str(policy)


def _format_live_check_message(message: object) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")
    if isinstance(message, BaseException):
        if not message.args:
            return str(message)
        return ": ".join(_format_live_check_message(arg) for arg in message.args)
    if isinstance(message, tuple):
        return ": ".join(_format_live_check_message(item) for item in message)
    if isinstance(message, list):
        return ", ".join(_format_live_check_message(item) for item in message)
    return str(message)


def _call_live_account_tests(
    test_accounts: Callable[..., object],
    *,
    progress: Callable[[str], None] | None,
) -> object:
    if progress is None:
        return test_accounts()
    signature = inspect.signature(test_accounts)
    accepts_progress = "progress" in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_progress:
        return test_accounts()
    return test_accounts(progress=progress)


def _live_account_test_result(
    *,
    account_name: str,
    account_config: object,
    result: object,
) -> ConfigCheckAccountResult:
    policy = _account_policy_name(account_config)
    if not isinstance(result, Mapping):
        return ConfigCheckAccountResult(
            account=account_name,
            policy=policy,
            status="fail",
            message=f"live account test result must be a mapping: {type(result).__name__}",
        )
    status = str(result.get("status", "failed"))
    if status == "ok":
        return ConfigCheckAccountResult(
            account=account_name,
            policy=policy,
            status="pass",
            message="live account check passed",
        )
    severity: Literal["warn", "fail"] = "warn" if status == "skipped" else "fail"
    reason = result.get("message") or result.get("reason")
    if reason is None:
        reason = f"live account test {status}"
    reason = _format_live_check_message(reason)
    return ConfigCheckAccountResult(
        account=account_name,
        policy=policy,
        status=severity,
        message=reason,
    )


def _live_config_check_report(
    app: ArbiterApp,
    *,
    cfg: HydraConfig,
    service_plugin: ServicePlugin,
    progress: Callable[[str], None] | None = None,
) -> ConfigCheckComponentReport:
    runtime = app.runtime_registry.require_object(service_plugin.name)
    test_accounts = getattr(runtime, "test_accounts", None)
    if not callable(test_accounts):
        return ConfigCheckComponentReport(
            name=service_plugin.name,
            warnings=(
                ConfigCheckIssue(
                    message="runtime does not implement live account tests"
                ),
            ),
        )
    accounts = service_accounts_for(cfg, service_plugin.name) or {}
    try:
        test_results = _call_live_account_tests(test_accounts, progress=progress)
    except Exception as exc:
        return ConfigCheckComponentReport(
            name=service_plugin.name,
            errors=(ConfigCheckIssue(message=_format_live_check_message(exc)),),
        )
    if not isinstance(test_results, Mapping):
        return ConfigCheckComponentReport(
            name=service_plugin.name,
            errors=(
                ConfigCheckIssue(
                    message=(
                        "live account tests must return a mapping: "
                        f"{type(test_results).__name__}"
                    )
                ),
            ),
        )
    account_results: list[ConfigCheckAccountResult] = []
    for account_name, account_config in sorted(accounts.items()):
        result = test_results.get(account_name)
        if result is None:
            account_results.append(
                ConfigCheckAccountResult(
                    account=str(account_name),
                    policy=_account_policy_name(account_config),
                    status="fail",
                    message="live account test result is missing",
                )
            )
            continue
        account_results.append(
            _live_account_test_result(
                account_name=str(account_name),
                account_config=account_config,
                result=result,
            )
        )
    return ConfigCheckComponentReport(
        name=service_plugin.name,
        account_results=tuple(account_results),
    )


def _account_policy_pair_results(
    accounts: Mapping[str, object],
    policies: Mapping[str, object],
) -> tuple[ConfigCheckAccountResult, ...]:
    results: list[ConfigCheckAccountResult] = []
    for account_name, account_config in sorted(accounts.items()):
        policy = _account_policy_name(account_config)
        if policy is None:
            results.append(
                ConfigCheckAccountResult(
                    account=str(account_name),
                    status="fail",
                    message="account policy is missing",
                )
            )
        elif policy not in policies:
            results.append(
                ConfigCheckAccountResult(
                    account=str(account_name),
                    policy=policy,
                    status="fail",
                    message="account references an unknown policy",
                )
            )
        else:
            results.append(
                ConfigCheckAccountResult(
                    account=str(account_name),
                    policy=policy,
                    status="pass",
                    message="account/policy pair valid",
                )
            )
    return tuple(results)


def _merge_config_check_component_reports(
    base: ConfigCheckComponentReport,
    extra: ConfigCheckComponentReport,
) -> ConfigCheckComponentReport:
    return ConfigCheckComponentReport(
        name=base.name,
        account_results=extra.account_results,
        warnings=(*base.warnings, *extra.warnings),
        errors=(*base.errors, *extra.errors),
    )


def config_check_components(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
    *,
    live: bool = False,
    progress: Callable[[str, str | None], None] | None = None,
) -> Iterator[ConfigCheckComponentReport]:
    def start_progress(component: str, account: str | None = None) -> None:
        if progress is not None:
            progress(component, account)

    start_progress("server")
    try:
        ensure_runnable_config(cfg, service_plugins=service_plugins)
        available_service_plugins = (
            discover_service_plugins() if service_plugins is None else service_plugins
        )
        active_service_plugins = _configured_service_plugins(
            cfg,
            available_service_plugins,
        )
    except Exception as exc:
        yield ConfigCheckComponentReport(
            name="server",
            errors=(ConfigCheckIssue(message=str(exc)),),
        )
        return

    service_errors = False
    service_components: list[ConfigCheckComponentReport] = []
    for service_plugin in active_service_plugins:
        accounts = service_accounts_for(cfg, service_plugin.name)
        if accounts is None:
            service_errors = True
            service_components.append(
                ConfigCheckComponentReport(
                    name=service_plugin.name,
                    errors=(
                        ConfigCheckIssue(
                            message=(
                                "service config is not configured: "
                                f"{service_plugin.name}"
                            )
                        ),
                    ),
                )
            )
            continue
        policies = service_policies_for(cfg, service_plugin.name)
        account_results = _account_policy_pair_results(accounts, policies)
        if any(result.status == "fail" for result in account_results):
            service_errors = True
        try:
            warnings = check_service_plugin_config(
                service_plugin,
                accounts=accounts,
                policies=policies,
            )
        except ConfigCheckError as exc:
            service_errors = True
            service_components.append(
                ConfigCheckComponentReport(
                    name=service_plugin.name,
                    account_results=account_results,
                    errors=exc.issues,
                )
            )
            continue
        except Exception as exc:
            service_errors = True
            service_components.append(
                ConfigCheckComponentReport(
                    name=service_plugin.name,
                    account_results=account_results,
                    errors=(ConfigCheckIssue(message=str(exc)),),
                )
            )
            continue
        service_components.append(
            ConfigCheckComponentReport(
                name=service_plugin.name,
                account_results=account_results,
                warnings=tuple(warnings),
            )
        )

    server_error = False
    server_component = ConfigCheckComponentReport(name="server")
    if not service_errors:
        try:
            build_server(cfg, service_plugins=active_service_plugins)
        except Exception as exc:
            server_error = True
            server_component = ConfigCheckComponentReport(
                name="server",
                errors=(ConfigCheckIssue(message=str(exc)),),
            )
    if service_errors or server_error or not live:
        yield server_component
        yield from service_components
        return

    try:
        app = build_app(cfg, service_plugins=active_service_plugins)
    except Exception as exc:
        yield ConfigCheckComponentReport(
            name="server",
            errors=(ConfigCheckIssue(message=str(exc)),),
        )
        return

    yield server_component
    service_components_by_name = {
        component.name: component for component in service_components
    }
    for service_plugin in active_service_plugins:
        component = service_components_by_name[service_plugin.name]
        start_progress(service_plugin.name)

        def account_progress(
            account_name: str,
            *,
            plugin_name: str = service_plugin.name,
        ) -> None:
            start_progress(plugin_name, account_name)

        live_component = _live_config_check_report(
            app,
            cfg=cfg,
            service_plugin=service_plugin,
            progress=account_progress,
        )
        yield _merge_config_check_component_reports(component, live_component)


def config_check_report(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
    *,
    live: bool = False,
) -> ConfigCheckReport:
    return ConfigCheckReport(
        components=tuple(
            config_check_components(
                cfg,
                service_plugins=service_plugins,
                live=live,
            )
        )
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
            "server_api_version": info.server_api_version,
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
        "server": {
            "version": SERVER_VERSION,
            "api_version": SERVER_API_VERSION,
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

    server_info = cast(dict[str, str], version_info["server"])
    print(f"server {server_info['version']} (api {server_info['api_version']})")
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
            f"(server api {plugin['server_api_version']})"
        )


def _register_server_tools(
    server: "FastMCP",
    catalog: OperationCatalog,
    service_plugins: Sequence[ServicePlugin],
    deployment_scope: DeploymentScope | str,
) -> None:
    @server.tool(
        description=(
            "Return Arbiter server and loaded service plugin version " "information."
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
            "account policy summaries, read-only account test results, and "
            "operation schemas."
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

    @server.tool(
        description=(
            "Check whether one Arbiter operation would be allowed by policy "
            "without calling external services or mutating state."
        )
    )
    def check_op(
        id: str,
        arguments: dict[str, Any] | None = None,
    ) -> object:
        return catalog.check_operation(id, arguments)


def _create_fastmcp_server(cfg: HydraConfig) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(
        cfg.arbiter.server.name,
        stateless_http=cfg.arbiter.server.stateless_http,
        json_response=cfg.arbiter.server.json_response,
    )
    server.settings.host = cfg.arbiter.server.bind.host
    server.settings.port = int(cfg.arbiter.server.bind.port)
    server.settings.streamable_http_path = cfg.arbiter.server.bind.path
    mcp_server = getattr(server, "_mcp_server", None)
    if mcp_server is not None:
        mcp_server.version = arbiter_server_version()
    return server


def build_server(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
) -> "FastMCP":
    available_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(
        cfg,
        available_service_plugins,
    )
    artifact_store: ArtifactStore | None = None
    runtime_dependencies: dict[str, object] = {}
    if cfg.arbiter.server.transport != "stdio":
        artifact_store = ArtifactStore(
            root=_plugin_data_root(_storage_config(cfg)),
            base_url=_artifact_base_url(cfg),
        )
        runtime_dependencies["artifact_store"] = artifact_store
    app = build_app(
        cfg,
        service_plugins=active_service_plugins,
        runtime_dependencies=runtime_dependencies,
    )
    server = _create_fastmcp_server(cfg)
    if artifact_store is not None:
        _register_artifact_route(server, artifact_store)
    catalog = OperationCatalog(
        active_service_plugins,
        ServicePluginContext(runtimes=app.runtime_registry),
        max_account_preview_limit=cfg.arbiter.discovery.max_account_preview_limit,
        max_operation_preview_limit=cfg.arbiter.discovery.max_operation_preview_limit,
    )
    _register_server_tools(
        server,
        catalog,
        active_service_plugins,
        cfg.arbiter.deployment_scope,
    )

    return server


def _register_artifact_route(server: "FastMCP", artifact_store: ArtifactStore) -> None:
    from starlette.requests import Request
    from starlette.responses import FileResponse, PlainTextResponse, Response

    @server.custom_route(
        f"{ARTIFACT_ROUTE_PREFIX}/{{artifact_id}}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    async def get_artifact(request: Request) -> Response:
        artifact_id = request.path_params["artifact_id"]
        nonce = request.query_params.get("nonce", "")
        if not nonce:
            return PlainTextResponse("not found", status_code=404)
        try:
            if request.method == "HEAD":
                artifact = artifact_store.inspect(artifact_id, nonce)
            else:
                artifact = artifact_store.open_once(artifact_id, nonce)
        except ArtifactConsumed:
            return PlainTextResponse("gone", status_code=410)
        except (ArtifactExpired, ArtifactNotFound):
            return PlainTextResponse("not found", status_code=404)
        if request.method == "HEAD":
            response = Response(status_code=200, media_type=artifact.content_type)
            response.headers["Content-Length"] = str(artifact.size)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Arbiter-Artifact-SHA256"] = artifact.sha256
            return response
        response = FileResponse(
            artifact.path,
            media_type=artifact.content_type,
            filename=artifact.filename,
            content_disposition_type="attachment",
        )
        response.headers.setdefault("Content-Disposition", "attachment")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Arbiter-Artifact-SHA256"] = artifact.sha256
        return response


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
        ensure_runtime_config_permissions(
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
        files("arbiter_server")
        .joinpath("deploy")
        .joinpath("docker")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _repo_root_path() -> Path | None:
    starts = [Path.cwd(), Path(__file__).resolve()]
    for start in starts:
        for root in (start, *start.parents):
            if (root / "server" / "pyproject.toml").is_file() and (
                root / "meta" / "arbiter-suite" / "pyproject.toml"
            ).is_file():
                return root
    return None


def _pyproject_project_data(
    pyproject: Path,
) -> tuple[str | None, str | None, tuple[str, ...], tuple[str, ...]]:
    section: str | None = None
    in_dependencies = False
    name: str | None = None
    description: str | None = None
    dependencies: list[str] = []
    entry_point_names: list[str] = []

    for raw_line in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            in_dependencies = False
            continue

        if section == "project":
            if in_dependencies:
                if line == "]":
                    in_dependencies = False
                    continue
                if match := re.fullmatch(r'"([^"]+)",?', line):
                    dependencies.append(match.group(1))
                continue
            if line == "dependencies = [":
                in_dependencies = True
                continue
            if match := re.fullmatch(r'name\s*=\s*"([^"]*)"', line):
                name = match.group(1)
            elif match := re.fullmatch(r'description\s*=\s*"([^"]*)"', line):
                description = match.group(1)
        elif section == f'project.entry-points."{SERVICE_PLUGIN_ENTRY_POINT_GROUP}"':
            if match := re.fullmatch(r'([A-Za-z0-9_-]+)\s*=\s*"[^"]+"', line):
                entry_point_names.append(match.group(1))
    return name, description, tuple(dependencies), tuple(entry_point_names)


def _repo_bundle_plugins() -> tuple[DockerBundlePlugin, ...]:
    repo_root = _repo_root_path()
    if repo_root is None:
        return ()
    plugins: list[DockerBundlePlugin] = []
    for pyproject in sorted((repo_root / "plugins").glob("*/pyproject.toml")):
        package, description, _, entry_point_names = _pyproject_project_data(pyproject)
        if not package or not description:
            continue
        for name in entry_point_names:
            plugin = _docker_bundle_plugin(name, package, description)
            if plugin is not None:
                plugins.append(plugin)
    return tuple(plugins)


def _docker_bundle_plugin(
    name: str,
    package: str,
    description: str,
) -> DockerBundlePlugin | None:
    package = _normalized_distribution_name(package)
    description = " ".join(description.split())
    if (
        not DOCKER_BUNDLE_PLUGIN_NAME_PATTERN.fullmatch(name)
        or not DOCKER_BUNDLE_PACKAGE_NAME_PATTERN.fullmatch(package)
        or not description
    ):
        return None
    return DockerBundlePlugin(name=name, package=package, description=description)


def _installed_bundle_plugins() -> tuple[DockerBundlePlugin, ...]:
    plugins: list[DockerBundlePlugin] = []
    for entry_point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP):
        distribution_metadata = getattr(
            getattr(entry_point, "dist", None), "metadata", None
        )
        if distribution_metadata is None:
            continue
        package = distribution_metadata.get("Name")
        description = distribution_metadata.get("Summary")
        if not isinstance(package, str) or not isinstance(description, str):
            continue
        plugin = _docker_bundle_plugin(entry_point.name, package, description)
        if plugin is not None:
            plugins.append(plugin)
    return tuple(sorted(plugins, key=lambda plugin: plugin.name))


def _docker_bundle_plugins() -> tuple[DockerBundlePlugin, ...]:
    return _repo_bundle_plugins() or _installed_bundle_plugins()


def _docker_bundle_plugins_text() -> str:
    suite_packages = set(_suite_dependency_package_names())
    return "# plugin\tpackage\tsuite\tdescription\n" + "".join(
        f"{plugin.name}\t{plugin.package}\t"
        f"{'suite' if plugin.package in suite_packages else ''}\t"
        f"{plugin.description}\n"
        for plugin in _docker_bundle_plugins()
    )


def _requirement_distribution_name(requirement: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", requirement)
    if match is None:
        return None
    return _normalized_distribution_name(match.group(1))


def _project_dependencies_from_pyproject(pyproject: Path) -> tuple[str, ...]:
    _, _, dependencies, _ = _pyproject_project_data(pyproject)
    return dependencies


def _suite_pyproject_path() -> Path | None:
    repo_root = _repo_root_path()
    if repo_root is None:
        return None
    return repo_root / "meta" / "arbiter-suite" / "pyproject.toml"


def _suite_dependency_package_names() -> tuple[str, ...]:
    pyproject = _suite_pyproject_path()
    if pyproject is not None:
        dependencies = _project_dependencies_from_pyproject(pyproject)
    else:
        try:
            dependencies = tuple(distribution(ARBITER_ALL_META_PACKAGE).requires or ())
        except PackageNotFoundError:
            dependencies = ()
    package_names = {
        name
        for dependency in dependencies
        if (name := _requirement_distribution_name(dependency)) is not None
    }
    return tuple(sorted(package_names))


def _docker_suite_plugins() -> tuple[DockerBundlePlugin, ...]:
    suite_packages = set(_suite_dependency_package_names())
    return tuple(
        plugin
        for plugin in _docker_bundle_plugins()
        if plugin.package in suite_packages
    )


def _docker_meta_package_groups() -> dict[str, tuple[str, ...]]:
    return {
        ARBITER_ALL_META_PACKAGE: (
            ARBITER_SERVER_PACKAGE,
            *(plugin.package for plugin in _docker_suite_plugins()),
        )
    }


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
    return re.sub(r"[-_.]+", "-", name.lower())


def _distribution_direct_url_source_root(installed_distribution: Any) -> Path | None:
    for distribution_file in installed_distribution.files or ():
        parts = distribution_file.parts
        if (
            len(parts) < 2
            or parts[-1] != "direct_url.json"
            or not parts[-2].endswith(".dist-info")
        ):
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
        source_root = Path(url2pathname(parsed_url.path))
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


def _ensure_writable_plugin_data_dir(plugin_data_dir: Path) -> bool:
    try:
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            plugin_data_dir.chmod(0o700)
        write_check = plugin_data_dir / ".arbiter-write-check"
        write_check.write_text("", encoding="utf-8")
        write_check.unlink()
    except OSError as exc:
        print_cli_error(
            "deployment plugin data directory is not writable",
            area="deploy",
            details=[
                f"plugin data dir: {plugin_data_dir}",
                f"error: {exc}",
                "remove or chown the plugin data directory, then retry",
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
    server_version = arbiter_server_version()
    try:
        server_distribution = distribution(ARBITER_SERVER_PACKAGE)
    except PackageNotFoundError:
        server_distribution = None
    server_requirement = _docker_requirement_for_installed_distribution(
        distribution_name=ARBITER_SERVER_PACKAGE,
        version=server_version,
        installed_distribution=server_distribution,
        wheel_dir=wheel_dir,
    )
    if server_requirement is None:
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
            server_requirement,
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
    meta_package_groups = _docker_meta_package_groups()
    pins = {
        _normalized_distribution_name(name): version
        for requirement in requirements
        if (parts := _pinned_requirement_parts(requirement)) is not None
        for name, version in (parts,)
    }
    expanded_meta_packages = {
        meta_package
        for meta_package, package_names in meta_package_groups.items()
        if meta_package in pins and any(name in pins for name in package_names)
    }
    expanded_package_names = {
        package_name
        for meta_package in expanded_meta_packages
        for package_name in meta_package_groups[meta_package]
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
        name = _normalized_distribution_name(name)
        if name in expanded_meta_packages:
            for package_name in meta_package_groups[name]:
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
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
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
        "bundle_plugins": deploy_dir / DOCKER_BUNDLE_PLUGINS_FILE_NAME,
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
        "arbiter_server_version": arbiter_server_version(),
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
    if os.name == "nt":
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
    bundle_plugins_text = _docker_bundle_plugins_text()

    if parsed.action == "init":
        manifest_path = _deploy_manifest_path(deploy_dir)
        init_paths = [
            paths["compose"],
            paths["docker_env"],
            paths["requirements"],
            paths["helper"],
            paths["bundle_plugins"],
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
        if not _ensure_writable_plugin_data_dir(deploy_dir / "data" / "plugins"):
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
        _write_manifest_owned_deploy_file(
            path=paths["bundle_plugins"],
            relative_path=DOCKER_BUNDLE_PLUGINS_FILE_NAME,
            content=bundle_plugins_text,
            executable=False,
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
        if not _ensure_writable_plugin_data_dir(deploy_dir / "data" / "plugins"):
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
            _update_manifest_owned_deploy_file(
                path=paths["bundle_plugins"],
                relative_path=DOCKER_BUNDLE_PLUGINS_FILE_NAME,
                content=bundle_plugins_text,
                executable=False,
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
    skip_runtime_permission_checks: bool = False,
) -> int:
    try:
        cfg = compose_config(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
            enforce_runtime_permissions=not skip_runtime_permission_checks,
        )
        ensure_runnable_config(cfg)
        log_startup_summary(cfg)
        server = build_server(cfg)
        _run_server(server, cast(TransportMode, cfg.arbiter.server.transport))
    except KeyboardInterrupt:
        print("Arbiter server stopped.", file=sys.stderr)
        return 130
    except (CompactHydraException, ValueError) as exc:
        print_cli_error(str(exc), area="config")
        return 1
    return 0


def _run_config_check(
    *,
    config_dir: str,
    config_name: str,
    overrides: Sequence[str],
    live: bool = False,
) -> int:
    color = _config_check_color_enabled(sys.stdout)
    progress = _ConfigCheckProgress(
        sys.stdout,
        color=color,
        enabled=_config_check_progress_enabled(sys.stdout),
    )
    try:
        cfg = compose_config(
            config_dir=config_dir,
            config_name=config_name,
            overrides=overrides,
        )
        failed = False
        components: list[ConfigCheckComponentReport] = []
        for component in config_check_components(
            cfg,
            live=live,
            progress=progress.start,
        ):
            progress.finish()
            components.append(component)
            lines = (*component.summary_lines, *component.issue_lines)
            for line in _color_config_check_lines(lines, color=color):
                print(line, flush=True)
            failed = failed or component.status == "fail"
        account_rows = [row for component in components for row in component.table_rows]
        if account_rows:
            for line in _color_config_check_lines(
                _config_check_table_lines(account_rows),
                color=color,
            ):
                print(line, flush=True)
        if failed:
            return 1
    except (CompactHydraException, ValueError) as exc:
        progress.finish()
        print_cli_error(str(exc), area="config")
        return 1
    finally:
        progress.finish()
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
    except (CompactHydraException, ValueError) as exc:
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
    *,
    variant: str | None = None,
) -> str | None:
    plugins = _service_plugin_map(discover_service_plugins())
    service_plugin = plugins.get(plugin)
    if service_plugin is None:
        print_cli_error(f"service plugin is not installed: {plugin}", area="bootstrap")
        return None

    bootstrap_config = cast(
        Callable[..., object | None],
        getattr(service_plugin, "bootstrap_config"),
    )
    try:
        node = bootstrap_config(kind=kind, name=name, variant=variant)
    except TypeError:
        if variant is not None:
            print_cli_error(
                f"service plugin does not support bootstrap variants: {plugin}",
                area="bootstrap",
            )
            return None
        node = service_plugin.bootstrap_config(kind=kind, name=name)
    except ValueError as exc:
        print_cli_error(str(exc), area="bootstrap")
        return None
    if node is None:
        print_cli_error(
            f"service plugin does not provide an {kind} bootstrap example: {plugin}",
            area="bootstrap",
        )
        return None
    if isinstance(node, str):
        return node
    return OmegaConf.to_yaml(node, resolve=False)


def _run_plugin_bootstrap_list_variants(
    *,
    plugin: str,
    kind: BootstrapObjectKind,
) -> int:
    plugins = _service_plugin_map(discover_service_plugins())
    service_plugin = plugins.get(plugin)
    if service_plugin is None:
        print_cli_error(f"service plugin is not installed: {plugin}", area="bootstrap")
        return 1
    bootstrap_variants = getattr(service_plugin, "bootstrap_variants", None)
    variants = (
        cast(Mapping[str, str], bootstrap_variants(kind=kind))
        if callable(bootstrap_variants)
        else {}
    )
    for name, description in sorted(variants.items()):
        print(f"{name}\t{description}")
    return 0


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
    name: str | None,
    config_dir: str | None,
    config_name: str,
    force: bool,
    variant: str | None = None,
    list_variants: bool = False,
) -> int:
    if list_variants:
        return _run_plugin_bootstrap_list_variants(plugin=plugin, kind=kind)
    config_dir_path = _ensure_config_dir(config_dir)
    if config_dir_path is None:
        return 2
    if name is None:
        print_cli_error("bootstrap plugin requires name", area="bootstrap")
        return 2
    if not _validate_bootstrap_object_args(plugin, name):
        return 2
    content = _load_plugin_example_yaml(plugin, kind, name, variant=variant)
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
        policy_content = _load_plugin_example_yaml(
            plugin,
            "policy",
            policy_name,
            variant=variant,
        )
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
        if arg == "--unsafe-skip-runtime-permission-checks":
            extracted.append(arg)
            index += 1
            continue
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
    parser.add_argument(
        "--unsafe-skip-runtime-permission-checks",
        action="store_true",
        help=argparse.SUPPRESS,
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
    check.add_argument(
        "--live",
        action="store_true",
        help="also run live account readiness checks using configured credentials",
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
    bootstrap_plugin.add_argument("name", nargs="?")
    bootstrap_plugin.add_argument(
        "--variant",
        help="bootstrap template variant when the plugin provides variants",
    )
    bootstrap_plugin.add_argument(
        "--list-variants",
        action="store_true",
        help="list bootstrap variants for this plugin and kind",
    )
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
        help="print Arbiter server and plugin versions",
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
            skip_runtime_permission_checks=(
                namespace.unsafe_skip_runtime_permission_checks
            ),
        )
    if namespace.command == "config" and namespace.config_command == "check":
        return _run_config_check(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            overrides=namespace.overrides,
            live=namespace.live,
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
            variant=namespace.variant,
            list_variants=namespace.list_variants,
        )

    parser.error("unknown command")
