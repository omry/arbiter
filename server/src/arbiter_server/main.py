from __future__ import annotations

import argparse
import ast
import base64
import datetime as dt
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
import textwrap
import time
from collections.abc import (
    AsyncGenerator,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import asdict, dataclass, is_dataclass
from ipaddress import ip_address
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from typing import Any, Literal, cast
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import url2pathname

from hydra import compose, initialize_config_dir
from hydra.errors import CompactHydraException
from omegaconf import DictConfig, OmegaConf
from omegaconf.errors import InterpolationResolutionError

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
    ServerTlsSource,
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
from .storage import PluginStorage, default_plugin_data_root, default_server_data_root
from .version import arbiter_server_version, source_info

LOGGER = logging.getLogger(__name__)
TransportMode = Literal["https"]
HydraConfig = AppConfig | DictConfig
BootstrapObjectKind = Literal["account", "policy"]
CLI_COMMANDS = {"serve", "config", "plugins", "bootstrap", "env", "deploy", "version"}
BOOTSTRAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_FILE_CONFIG_KEY = "arbiter.env_file"
ENV_REFERENCE_PATTERN = re.compile(r"\$\{oc\.env:(?P<name>[^,}\s]+)(?:,[^}]*)?\}")
ENV_REFERENCE_FULL_VALUE_PATTERN = re.compile(
    r"^\$\{oc\.env:(?P<name>[^,}\s]+)(?:,[^}]*)?\}$"
)
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
API_ROUTE_PREFIX = "/api/v1"
ARTIFACT_ROUTE_PREFIX = f"{API_ROUTE_PREFIX}/artifacts"
HEALTH_ROUTE = "/_health_"
INLINE_ARTIFACT_MAX_BYTES = 5 * 1024
DEPLOY_MANIFEST_FILE_NAME = ".arbiter-deploy.json"
BUILD_TIME_ENV_VAR = "ARBITER_BUILD_TIME"
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
    ("ARBITER_RESTART", "on-failure"),
    ("ARBITER_APP_ENV_FILE", "./conf/.env"),
    ("ARBITER_CONFIG_DIR", "./conf"),
    ("ARBITER_CONFIG_NAME", "arbiter-server"),
    ("ARBITER_REQUIREMENTS_FILE", "./requirements.txt"),
    ("ARBITER_WHEELS_DIR", "./wheels"),
    ("ARBITER_SERVER_DATA_DIR", "./data/server"),
    ("ARBITER_PLUGIN_DATA_DIR", "./data/plugins"),
    ("ARBITER_HOST_BIND", "127.0.0.1"),
    ("ARBITER_HOST_PORT", "18075"),
    ("ARBITER_CONTAINER_PORT", "8075"),
    ("ARBITER_PUBLIC_SCHEME", "https"),
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
#   arbiter-server --config-dir <dir> serve arbiter.server.bind.port=8075
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
  transport: https
  bind:
    scheme: https
    host: 127.0.0.1
    port: 8075
    path: ""
  public:
    scheme: https
  tls:
    source: SELF_SIGNED
deployment_scope: unknown
discovery:
  max_account_preview_limit: 25
  max_operation_preview_limit: 25
"""


@dataclass(frozen=True)
class EnvReference:
    name: str
    block: str
    default: str | None = None


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
class NativeHTTPServer:
    app: object
    host: str
    port: int
    ssl_certfile: str
    ssl_keyfile: str


@dataclass(frozen=True)
class ConfigCheckReport:
    components: tuple["ConfigCheckComponentReport", ...]

    @property
    def summary(self) -> str:
        return "\n".join(self.lines)

    @property
    def lines(self) -> tuple[str, ...]:
        lines = [*_config_check_tree_lines(self.components)]
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
        return (*_config_check_tree_lines((self,)), *self.issue_lines)

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
_CONFIG_CHECK_STRUCTURE_COLOR = "90"
_CONFIG_CHECK_COLUMN_SEPARATOR = " │ "
_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN = r" [|│] "
_CONFIG_CHECK_PROGRESS_FRAMES = ("|", "/", "-", "\\")
_CONFIG_ACTIVATION_ENABLED_ICON = "✓"
_CONFIG_ACTIVATION_DISABLED_ICON = "✗"


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


def _config_check_unicode_tree_enabled(output: object) -> bool:
    encoding = getattr(output, "encoding", None)
    if not isinstance(encoding, str) or not encoding:
        return True
    try:
        "├──└──│".encode(encoding)
    except UnicodeEncodeError:
        return False
    except LookupError:
        return True
    return True


def _color_config_check_status(status: str) -> str:
    color = _CONFIG_CHECK_STATUS_COLORS.get(status)
    if color is None:
        return status
    return f"\033[{color}m{status}\033[0m"


def _color_config_check_message(status: str, message: str) -> str:
    color = _CONFIG_CHECK_STATUS_COLORS.get(status)
    if color is None or not message:
        return message
    return f"\033[{color}m{message}\033[0m"


def _color_config_check_structure(status: str, value: str) -> str:
    if not value:
        return value
    return f"\033[{_CONFIG_CHECK_STRUCTURE_COLOR}m{value}\033[0m"


def _color_config_check_component(component: str, status: str | None = None) -> str:
    color = _CONFIG_CHECK_STATUS_COLORS.get(status or "")
    if color is None:
        color = _CONFIG_CHECK_COMPONENT_COLOR
    return f"\033[{color}m{component}\033[0m"


def _activation_status_icon(*, enabled: bool, color: bool) -> str:
    icon = (
        _CONFIG_ACTIVATION_ENABLED_ICON
        if enabled
        else _CONFIG_ACTIVATION_DISABLED_ICON
    )
    if not color:
        return icon
    status = "pass" if enabled else "fail"
    color_code = _CONFIG_CHECK_STATUS_COLORS[status]
    return f"\033[{color_code}m{icon}\033[0m"


def _format_activation_plugin_status(
    plugin: str,
    *,
    enabled: bool,
    color: bool,
) -> str:
    return f"{_activation_status_icon(enabled=enabled, color=color)} {plugin}"


def _format_activation_account_row(
    account: str,
    *,
    installed_plugins: Sequence[str],
    active_plugins: Sequence[str],
    color: bool,
) -> str:
    active_plugin_set = set(active_plugins)
    plugin_statuses = [
        _format_activation_plugin_status(
            plugin,
            enabled=plugin in active_plugin_set,
            color=color,
        )
        for plugin in installed_plugins
    ]
    return f"  {account}: {'  '.join(plugin_statuses)}"


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


def _color_config_check_line(
    line: str, *, continuation_status: str | None = None
) -> str:
    match = re.fullmatch(r"([^:]+): (pass|warn|fail)", line)
    if match is not None:
        return (
            f"{_color_config_check_component(match.group(1), match.group(2))}"
            f"{_color_config_check_structure(match.group(2), ': ')}"
            f"{_color_config_check_status(match.group(2))}"
        )
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)", line)
    if match is not None:
        return _color_config_check_component(match.group(1))
    match = re.fullmatch(r"([├└]── )([A-Za-z0-9_.-]+)", line)
    if match is not None:
        return f"{match.group(1)}{_color_config_check_component(match.group(2))}"
    match = re.fullmatch(
        rf"((?:│   |    )?[├└]── )([^|│]*?)"
        rf"({_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})"
        rf"(pass|warn|fail)( *[|│] )(.*)",
        line,
    )
    if match is not None:
        return (
            f"{_color_config_check_structure(match.group(4), match.group(1))}"
            f"{_color_config_check_component(match.group(2).rstrip(), match.group(4))}"
            f"{match.group(2)[len(match.group(2).rstrip()):]}"
            f"{_color_config_check_structure(match.group(4), match.group(3))}"
            f"{_color_config_check_status(match.group(4))}"
            f"{_color_config_check_structure(match.group(4), match.group(5))}"
            f"{_color_config_check_message(match.group(4), match.group(6))}"
        )
    match = re.fullmatch(
        rf"((?:│   |    )?[├└]── )([^|│]*?)"
        rf"({_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})(pass|warn|fail)",
        line,
    )
    if match is not None:
        label = match.group(2)
        trimmed_label = label.rstrip()
        return (
            f"{_color_config_check_structure(match.group(4), match.group(1))}"
            f"{_color_config_check_component(trimmed_label, match.group(4))}"
            f"{label[len(trimmed_label):]}"
            f"{_color_config_check_structure(match.group(4), match.group(3))}"
            f"{_color_config_check_status(match.group(4))}"
        )
    match = re.fullmatch(
        rf"([^|│]*?)({_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})"
        rf"(pass|warn|fail)( *[|│] )(.*)",
        line,
    )
    if match is not None:
        return (
            f"{_color_config_check_component(match.group(1).rstrip(), match.group(3))}"
            f"{match.group(1)[len(match.group(1).rstrip()):]}"
            f"{_color_config_check_structure(match.group(3), match.group(2))}"
            f"{_color_config_check_status(match.group(3))}"
            f"{_color_config_check_structure(match.group(3), match.group(4))}"
            f"{_color_config_check_message(match.group(3), match.group(5))}"
        )
    match = re.fullmatch(
        rf"([^|│]*?)({_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})(pass|warn|fail)",
        line,
    )
    if match is not None:
        return (
            f"{_color_config_check_component(match.group(1).rstrip(), match.group(3))}"
            f"{match.group(1)[len(match.group(1).rstrip()):]}"
            f"{_color_config_check_structure(match.group(3), match.group(2))}"
            f"{_color_config_check_status(match.group(3))}"
        )
    match = re.fullmatch(r"(pass|warn|fail)( +[|│] .*[|│] )(.*)", line)
    if match is not None:
        return (
            f"{_color_config_check_status(match.group(1))}"
            f"{_color_config_check_structure(match.group(1), match.group(2))}"
            f"{_color_config_check_message(match.group(1), match.group(3))}"
        )
    match = re.fullmatch(r"(pass|warn|fail)( +[|│] .*)", line)
    if match is not None:
        return (
            f"{_color_config_check_status(match.group(1))}"
            f"{_color_config_check_structure(match.group(1), match.group(2))}"
        )
    match = re.fullmatch(r"(- )(pass|warn|fail):(.*)", line)
    if match is not None:
        return (
            f"{_color_config_check_structure(match.group(2), match.group(1))}"
            f"{_color_config_check_status(match.group(2))}"
            f"{_color_config_check_structure(match.group(2), ':')}"
            f"{_color_config_check_message(match.group(2), match.group(3))}"
        )
    if continuation_status is not None:
        match = re.fullmatch(r"(\s+)(.*)", line)
        if match is not None:
            return (
                f"{match.group(1)}"
                f"{_color_config_check_message(continuation_status, match.group(2))}"
            )
    return line


def _config_check_line_status(line: str) -> Literal["pass", "warn", "fail"] | None:
    match = re.search(
        rf"(?:^|{_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})"
        rf"(pass|warn|fail)(?:$|{_CONFIG_CHECK_COLUMN_SEPARATOR_PATTERN})",
        line,
    )
    if match is None:
        return None
    return cast(Literal["pass", "warn", "fail"], match.group(1))


def _config_check_worst_status(
    statuses: Iterable[str],
) -> Literal["pass", "warn", "fail"]:
    worst: Literal["pass", "warn", "fail"] = "pass"
    for status in statuses:
        if status == "fail":
            return "fail"
        if status == "warn":
            worst = "warn"
    return worst


def _config_check_tree_lines(
    components: Sequence[ConfigCheckComponentReport],
    *,
    unicode_tree: bool = True,
    width: int | None = None,
) -> tuple[str, ...]:
    if not components:
        return ()
    last_branch = "└──" if unicode_tree else "`--"
    mid_branch = "├──" if unicode_tree else "|--"
    mid_prefix = "│   " if unicode_tree else "|   "
    column_separator = _CONFIG_CHECK_COLUMN_SEPARATOR if unicode_tree else " | "

    server_components = tuple(
        component for component in components if component.name == "server"
    )
    plugin_components = tuple(
        component for component in components if component.name != "server"
    )
    tree_rows: list[tuple[str, str, str]] = [
        ("server", component.status, "") for component in server_components
    ]
    if plugin_components:
        tree_rows.append(
            (
                "Plugins",
                _config_check_worst_status(
                    component.status for component in plugin_components
                ),
                "",
            )
        )
    for plugin_index, component in enumerate(plugin_components):
        plugin_is_last = plugin_index == len(plugin_components) - 1
        plugin_branch = last_branch if plugin_is_last else mid_branch
        row_prefix = "    " if plugin_is_last else mid_prefix
        tree_rows.append((f"{plugin_branch} {component.name}", component.status, ""))
        plugin_rows = component.table_rows
        for index, row in enumerate(plugin_rows):
            branch = last_branch if index == len(plugin_rows) - 1 else mid_branch
            tree_rows.append(
                (
                    f"{row_prefix}{branch} {_config_check_row_account_policy(row)}",
                    row.result,
                    row.message,
                )
            )
    label_width = max(len(label) for label, _, _ in tree_rows)
    status_width = max(len(status) for _, status, _ in tree_rows)
    lines: list[str] = []
    for label, status, message in tree_rows:
        lines.extend(
            _format_config_check_tree_row(
                label=label,
                status=status,
                message=message,
                label_width=label_width,
                status_width=status_width,
                column_separator=column_separator,
                width=width,
            )
        )
    return tuple(lines)


def _format_config_check_tree_row(
    *,
    label: str,
    status: str,
    message: str,
    label_width: int,
    status_width: int,
    column_separator: str,
    width: int | None,
) -> tuple[str, ...]:
    prefix = f"{label:<{label_width}}{column_separator}{status:<{status_width}}"
    if not message:
        return (prefix.rstrip(),)
    message_prefix = f"{prefix}{column_separator}"
    if width is None or width <= len(message_prefix) + 1:
        return (f"{message_prefix}{message}".rstrip(),)
    wrapped: list[str] = []
    continuation_prefix = " " * len(message_prefix)
    available = max(1, width - len(message_prefix))
    for index, message_line in enumerate(message.splitlines() or [""]):
        line_parts = textwrap.wrap(
            message_line,
            width=available,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        for part_index, part in enumerate(line_parts):
            prefix_for_part = (
                message_prefix
                if index == 0 and part_index == 0
                else continuation_prefix
            )
            wrapped.append(f"{prefix_for_part}{part}".rstrip())
    return tuple(wrapped)


def _config_check_row_account_policy(row: _ConfigCheckTableRow) -> str:
    return row.account if not row.policy else f"{row.account}/{row.policy}"


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
    colored: list[str] = []
    continuation_status: Literal["pass", "warn", "fail"] | None = None
    for line in lines:
        status = _config_check_line_status(line)
        colored.append(
            _color_config_check_line(
                line,
                continuation_status=continuation_status if status is None else None,
            )
        )
        if status is not None:
            continuation_status = status
        elif not line.startswith(" "):
            continuation_status = None
    return tuple(colored)


def _config_check_output_width(output: object) -> int | None:
    columns = _config_check_columns_env()
    if columns is not None:
        return columns
    isatty = getattr(output, "isatty", None)
    if not callable(isatty) or not isatty():
        return None
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def _config_check_columns_env() -> int | None:
    try:
        columns = int(os.environ.get("COLUMNS", ""))
    except ValueError:
        return None
    if columns < 20:
        return None
    return columns


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


def _server_data_root(storage_config: StorageConfig | Any | None) -> Path:
    if storage_config is None:
        return default_server_data_root()
    if OmegaConf.is_config(storage_config):
        server_data_dir = OmegaConf.select(cast(Any, storage_config), "server_data_dir")
    else:
        server_data_dir = getattr(storage_config, "server_data_dir", None)
    if server_data_dir is not None:
        return Path(str(server_data_dir)).expanduser().resolve()
    return default_server_data_root()


def _server_tls_source(cfg: HydraConfig) -> ServerTlsSource:
    source = cfg.arbiter.server.tls.source
    if isinstance(source, ServerTlsSource):
        return source
    source_value = str(source)
    try:
        return ServerTlsSource[source_value]
    except KeyError:
        return ServerTlsSource(source_value)


def _server_tls_config_string(cfg: HydraConfig, key: str) -> str | None:
    value = (
        OmegaConf.select(cast(Any, cfg), f"arbiter.server.tls.{key}")
        if OmegaConf.is_config(cfg)
        else getattr(cfg.arbiter.server.tls, key, None)
    )
    if value in (None, ""):
        return None
    return str(value)


def _server_tls_default_dir(cfg: HydraConfig) -> Path:
    return _server_data_root(_storage_config(cfg)) / "tls"


def _server_tls_default_cert_file(cfg: HydraConfig) -> Path:
    return _server_tls_default_dir(cfg) / "arbiter-self-signed.crt"


def _server_tls_default_key_file(cfg: HydraConfig) -> Path:
    return _server_tls_default_dir(cfg) / "arbiter-self-signed.key"


def _server_tls_configured_files(cfg: HydraConfig) -> tuple[Path, Path]:
    cert_file = _server_tls_config_string(cfg, "cert_file")
    key_file = _server_tls_config_string(cfg, "key_file")
    if cert_file is None or key_file is None:
        raise ValueError(
            "arbiter.server.tls.cert_file and key_file are required "
            "when arbiter.server.tls.source=CERT_FILES"
        )
    cert_path = Path(cert_file).expanduser().resolve()
    key_path = Path(key_file).expanduser().resolve()
    if not cert_path.is_file():
        raise ValueError(f"TLS certificate file not found: {cert_path}")
    if not key_path.is_file():
        raise ValueError(f"TLS private key file not found: {key_path}")
    _ensure_tls_private_key_permissions(key_path)
    return cert_path, key_path


def _server_tls_self_signed_files(cfg: HydraConfig) -> tuple[Path, Path]:
    cert_file = _server_tls_config_string(cfg, "cert_file")
    key_file = _server_tls_config_string(cfg, "key_file")
    cert_path = (
        Path(cert_file).expanduser().resolve()
        if cert_file is not None
        else _server_tls_default_cert_file(cfg)
    )
    key_path = (
        Path(key_file).expanduser().resolve()
        if key_file is not None
        else _server_tls_default_key_file(cfg)
    )
    return cert_path, key_path


def _ensure_tls_private_key_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    if mode & 0o077:
        raise ValueError(f"TLS private key must not be group/world accessible: {path}")


def _server_tls_subject_names(cfg: HydraConfig) -> tuple[str, ...]:
    names = {
        "localhost",
        str(cfg.arbiter.server.bind.host),
        str(cfg.arbiter.server.public.host),
    }
    public_base_url = str(cfg.arbiter.server.public.base_url)
    if "${" not in public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.hostname:
            names.add(parsed.hostname)
    return tuple(sorted(name for name in names if name and name != "0.0.0.0"))


def _generate_self_signed_tls_certificate(
    *,
    cert_path: Path,
    key_path: Path,
    names: Sequence[str],
) -> None:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "self-signed TLS certificates require the cryptography package"
        ) from exc

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        cert_path.parent.chmod(0o700)
        key_path.parent.chmod(0o700)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject_name = names[0] if names else "localhost"
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, subject_name)]
    )
    san_entries: list[x509.GeneralName] = []
    for name in names:
        try:
            san_entries.append(x509.IPAddress(ip_address(name)))
        except ValueError:
            san_entries.append(x509.DNSName(name))
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    _write_bytes_with_mode(key_path, key_bytes, 0o600)
    _write_bytes_with_mode(cert_path, cert_bytes, 0o644)


def _write_bytes_with_mode(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "wb") as handle:
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


def _server_tls_files(
    cfg: HydraConfig,
    *,
    generate_self_signed: bool,
) -> tuple[Path, Path]:
    source = _server_tls_source(cfg)
    if source == ServerTlsSource.CERT_FILES:
        return _server_tls_configured_files(cfg)
    if source != ServerTlsSource.SELF_SIGNED:
        raise ValueError(f"unsupported Arbiter TLS source: {source}")
    cert_path, key_path = _server_tls_self_signed_files(cfg)
    if cert_path.exists() and key_path.exists():
        _ensure_tls_private_key_permissions(key_path)
        return cert_path, key_path
    if not generate_self_signed:
        return cert_path, key_path
    _generate_self_signed_tls_certificate(
        cert_path=cert_path,
        key_path=key_path,
        names=_server_tls_subject_names(cfg),
    )
    return cert_path, key_path


def _csv_or_none(values: list[str]) -> str:
    return ",".join(values) if values else "none"


def _service_accounts_summary(cfg: HydraConfig) -> str:
    summaries: list[str] = []
    for service_name in configured_service_names(cfg.arbiter.account):
        accounts = cfg.arbiter.account.get(service_name, {})
        account_names = sorted(str(account_name) for account_name in accounts)
        summaries.append(f"{service_name}:{_csv_or_none(account_names)}")
    return ";".join(summaries) if summaries else "none"


def _server_url(cfg: HydraConfig) -> str:
    return _server_base_url(cfg)


def _server_base_url(cfg: HydraConfig) -> str:
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
    if urlparse(public_base_url).scheme != "https":
        raise ValueError("arbiter.server.public.base_url must use https")
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
        "url=%s services=%s service_accounts=%s",
        arbiter_server_version(),
        _deployment_scope_value(cfg.arbiter.deployment_scope),
        cfg.arbiter.server.transport,
        cfg.arbiter.server.bind.host,
        cfg.arbiter.server.bind.port,
        cfg.arbiter.server.bind.path,
        _server_url(cfg),
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
            "use `arbiter-server --config-dir DIR bootstrap --plugin PLUGIN "
            "--account NAME` to create an account config"
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
    if isinstance(message, str):
        decoded = _format_legacy_byte_string_message(message)
        if decoded is not None:
            return decoded
    return str(message)


def _format_legacy_byte_string_message(message: str) -> str | None:
    if "b'" not in message and 'b"' not in message:
        return None
    try:
        value = ast.literal_eval(message)
    except (SyntaxError, ValueError):
        return None
    if isinstance(value, bytes):
        return _format_live_check_message(value)
    if isinstance(value, tuple | list) and any(
        isinstance(item, bytes) for item in value
    ):
        return _format_live_check_message(value)
    return None


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
    severity: Literal["warn", "fail"] = (
        "warn" if status in {"skipped", "warn", "warning"} else "fail"
    )
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
            build_server(
                cfg,
                service_plugins=active_service_plugins,
                prepare_tls=False,
            )
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
    build_time = os.environ.get(BUILD_TIME_ENV_VAR) or None
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
            "build_time": build_time,
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
    deployment_scope = version_info["deployment_scope"]
    if deployment_scope in {
        DeploymentScope.staged.value,
        DeploymentScope.installed.value,
    }:
        print(f"deployment scope {deployment_scope}")
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


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str | int | float | bool):
        return enum_value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _native_response(payload: object, *, status_code: int = 200) -> object:
    from starlette.responses import JSONResponse

    return JSONResponse(_jsonable(payload), status_code=status_code)


def _native_error_payload(
    *,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {
        "code": code,
        "message": message,
    }
    if details:
        error["details"] = dict(details)
    return {"error": error}


def _native_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> object:
    return _native_response(
        _native_error_payload(code=code, message=message, details=details),
        status_code=status_code,
    )


def _native_exception_response(
    exc: BaseException,
    *,
    validation_status_code: int = 400,
) -> object:
    if isinstance(exc, ArtifactConsumed):
        return _native_error_response(
            status_code=410,
            code="artifact_consumed",
            message="Artifact is no longer available.",
        )
    if isinstance(exc, ArtifactExpired):
        return _native_error_response(
            status_code=410,
            code="artifact_expired",
            message="Artifact has expired.",
        )
    if isinstance(exc, ArtifactNotFound):
        return _native_error_response(
            status_code=404,
            code="artifact_not_found",
            message="Artifact was not found.",
        )
    if isinstance(exc, KeyError):
        return _native_error_response(
            status_code=404,
            code="not_found",
            message=str(exc.args[0]) if exc.args else str(exc),
        )
    if isinstance(exc, ValueError):
        message = str(exc)
        status_code = 404 if message.startswith("unknown ") else validation_status_code
        code = "not_found" if status_code == 404 else "validation_error"
        return _native_error_response(
            status_code=status_code,
            code=code,
            message=message,
        )
    LOGGER.exception("native HTTP request failed", exc_info=exc)
    return _native_error_response(
        status_code=500,
        code="internal_error",
        message="Internal Arbiter server error.",
    )


def _operation_details_payload(catalog: OperationCatalog, operation_ref: str) -> object:
    details = catalog.describe_operation(operation_ref)
    plugin = details.get("capability", "")
    description = str(details.get("description", ""))
    return {
        "id": details["id"],
        "plugin": plugin,
        "name": details["name"],
        "summary": description,
        "description": description,
        "input_schema": details["input_schema"],
        "output_schema": {},
        "artifact_policy": {
            "inline_max_bytes": INLINE_ARTIFACT_MAX_BYTES,
            "supports_uploads": False,
        },
    }


def _operation_response_payload(
    result: object,
    *,
    artifact_store: ArtifactStore,
) -> dict[str, object]:
    json_result = _jsonable(result)
    return {
        "result": json_result,
        "artifacts": _operation_artifacts(json_result, artifact_store=artifact_store),
        "warnings": [],
    }


def _operation_artifacts(
    result: object,
    *,
    artifact_store: ArtifactStore,
) -> list[dict[str, object]]:
    if not isinstance(result, Mapping):
        return []
    candidates: list[object] = []
    if "artifact" in result:
        candidates.append(result["artifact"])
    artifact_items = result.get("artifacts")
    if isinstance(artifact_items, Sequence) and not isinstance(
        artifact_items,
        str | bytes | bytearray,
    ):
        candidates.extend(artifact_items)
    artifacts: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in candidates:
        artifact = _native_artifact_from_descriptor(
            candidate,
            artifact_store=artifact_store,
        )
        if artifact is None:
            continue
        artifact_id = str(artifact["id"])
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifacts.append(artifact)
    return artifacts


def _native_artifact_from_descriptor(
    descriptor: object,
    *,
    artifact_store: ArtifactStore,
) -> dict[str, object] | None:
    if not isinstance(descriptor, Mapping):
        return None
    artifact_id = _string_or_none_value(descriptor.get("id"))
    nonce = _artifact_nonce_from_url(_string_or_none_value(descriptor.get("url")))
    if artifact_id is None or nonce is None:
        return None
    content_type = _string_or_none_value(descriptor.get("content_type"))
    size = descriptor.get("size")
    sha256 = _string_or_none_value(descriptor.get("sha256"))
    if not isinstance(size, int) or size < 0 or sha256 is None:
        return None

    artifact: dict[str, object] = {
        "id": artifact_id,
        "name": descriptor.get("filename"),
        "mime_type": content_type or "application/octet-stream",
        "size": size,
        "sha256": sha256,
        "content_url": _native_artifact_content_url(artifact_id, nonce),
    }
    inline = _inline_artifact_payload(
        artifact_id,
        nonce,
        artifact_store=artifact_store,
    )
    if inline is not None:
        artifact["inline"] = inline
    return artifact


def _artifact_nonce_from_url(url: str | None) -> str | None:
    if url is None:
        return None
    values = parse_qs(urlparse(url).query).get("nonce", [])
    if not values or values[0] == "":
        return None
    return values[0]


def _inline_artifact_payload(
    artifact_id: str,
    nonce: str,
    *,
    artifact_store: ArtifactStore,
) -> dict[str, object] | None:
    try:
        artifact = artifact_store.inspect(artifact_id, nonce)
    except (ArtifactConsumed, ArtifactExpired, ArtifactNotFound):
        return None
    if artifact.size > INLINE_ARTIFACT_MAX_BYTES:
        return None
    try:
        content = artifact.path.read_bytes()
    except OSError:
        return None
    if len(content) > INLINE_ARTIFACT_MAX_BYTES:
        return None
    if _is_textual_artifact_content_type(artifact.content_type):
        try:
            return {
                "encoding": "utf-8",
                "data": content.decode("utf-8"),
            }
        except UnicodeDecodeError:
            pass
    return {
        "encoding": "base64",
        "data": base64.b64encode(content).decode("ascii"),
    }


def _is_textual_artifact_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type.startswith("text/"):
        return True
    return (
        media_type
        in {
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
            "application/toml",
            "application/javascript",
        }
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    )


def _string_or_none_value(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


_REDACTED_CONFIG_VALUE = "<redacted>"
_SECRET_CONFIG_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_INTERNAL_CONFIG_KEYS = frozenset(
    {
        "cache_dir",
        "directory",
        "dir",
        "env_file",
        "file",
        "filename",
        "path",
    }
)
_INTERNAL_CONFIG_KEY_SUFFIXES = ("_dir", "_file", "_path")


def _plain_config_value(value: object, *, resolve: bool) -> object:
    if OmegaConf.is_config(value):
        return OmegaConf.to_container(value, resolve=resolve, enum_to_str=True)
    return _jsonable(value)


def _is_redacted_config_key(key: str | None) -> bool:
    if key is None:
        return False
    key_lower = key.lower()
    return (
        any(part in key_lower for part in _SECRET_CONFIG_KEY_PARTS)
        or key_lower in _INTERNAL_CONFIG_KEYS
        or key_lower.endswith(_INTERNAL_CONFIG_KEY_SUFFIXES)
    )


def _redact_config_value(value: object, *, key: str | None = None) -> object:
    if _is_redacted_config_key(key):
        return _REDACTED_CONFIG_VALUE
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_config_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_config_value(item) for item in value]
    return value


def _resolve_redacted_config_value(value: object) -> object:
    if not isinstance(value, (Mapping, list)):
        return value
    try:
        config_value = dict(value) if isinstance(value, Mapping) else value
        return OmegaConf.to_container(
            OmegaConf.create(config_value),
            resolve=True,
            enum_to_str=True,
        )
    except Exception:
        return value


def _redacted_config_payload(value: object) -> object:
    unresolved = _plain_config_value(value, resolve=False)
    return _resolve_redacted_config_value(_redact_config_value(unresolved))


def _config_field_string(config: object, field_name: str) -> str:
    if isinstance(config, Mapping):
        value = config.get(field_name, "")
    else:
        value = getattr(config, field_name, "")
    return value if isinstance(value, str) else ""


def _native_plugin_capability(
    catalog: OperationCatalog,
    plugin: str,
) -> Mapping[str, object] | None:
    capabilities = cast(
        Sequence[Mapping[str, object]],
        catalog.describe_capabilities(
            operation_preview_limit=0,
            account_preview_limit=0,
        )["capabilities"],
    )
    for capability in capabilities:
        if capability.get("id") == plugin:
            return capability
    return None


def _native_plugin_payload(
    catalog: OperationCatalog,
    plugin: str,
) -> dict[str, object]:
    capability = _native_plugin_capability(catalog, plugin)
    if capability is None:
        raise KeyError(f"unknown plugin: {plugin}")
    return {
        "id": capability["id"],
        "summary": capability.get("description", ""),
    }


def _native_account_summary_payload(
    *,
    plugin: str,
    account: str,
    account_config: object,
) -> dict[str, object]:
    return {
        "plugin": plugin,
        "account": account,
        "description": _config_field_string(account_config, "description"),
        "guidance": _config_field_string(account_config, "guidance"),
        "policy": _account_policy_name(account_config),
    }


def _native_plugin_accounts_payload(
    cfg: HydraConfig,
    plugin: str,
) -> dict[str, object]:
    accounts = service_accounts_for(cfg, plugin)
    if accounts is None:
        raise KeyError(f"unknown plugin or no accounts configured: {plugin}")
    return {
        "plugin": plugin,
        "accounts": [
            _native_account_summary_payload(
                plugin=plugin,
                account=str(account),
                account_config=account_config,
            )
            for account, account_config in sorted(accounts.items())
        ],
    }


def _native_policy_payload(
    cfg: HydraConfig,
    plugin: str,
    policy: str,
) -> dict[str, object]:
    policies = service_policies_for(cfg, plugin)
    if policy not in policies:
        raise KeyError(f"unknown policy: {plugin}/{policy}")
    return {
        "kind": "policy",
        "plugin": plugin,
        "policy": policy,
        "rules": _redacted_config_payload(policies[policy]),
    }


def _native_account_detail_payload(
    cfg: HydraConfig,
    plugin: str,
    account: str,
) -> dict[str, object]:
    accounts = service_accounts_for(cfg, plugin)
    if accounts is None or account not in accounts:
        raise KeyError(f"unknown account: {plugin}/{account}")
    account_config = accounts[account]
    policy = _account_policy_name(account_config)
    policy_payload = None
    if policy is not None:
        policy_payload = _native_policy_payload(cfg, plugin, policy)
    return {
        "kind": "account",
        "plugin": plugin,
        "account": account,
        "description": _config_field_string(account_config, "description"),
        "guidance": _config_field_string(account_config, "guidance"),
        "config": _redacted_config_payload(account_config),
        "policy": policy_payload,
    }


async def _read_operation_arguments(request: object) -> Mapping[str, Any]:
    from json import JSONDecodeError

    try:
        payload = await cast(Any, request).json()
    except JSONDecodeError as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    args = payload.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, Mapping):
        raise ValueError("operation args must be a JSON object")
    return cast(Mapping[str, Any], args)


def _native_artifact_metadata(
    *,
    artifact_id: str,
    artifact: object,
    nonce: str,
) -> dict[str, object]:
    read = cast(Any, artifact)
    return {
        "id": artifact_id,
        "name": read.filename,
        "mime_type": read.content_type,
        "size": read.size,
        "sha256": read.sha256,
        "content_url": _native_artifact_content_url(artifact_id, nonce),
    }


def _native_artifact_content_url(artifact_id: str, nonce: str) -> str:
    encoded_id = quote(artifact_id, safe="")
    encoded_nonce = quote(nonce, safe="")
    return f"{ARTIFACT_ROUTE_PREFIX}/{encoded_id}/content?nonce={encoded_nonce}"


def _attachment_content_disposition(filename: str | None) -> str:
    if filename is None:
        return "attachment"
    fallback = "".join(char if 0x20 <= ord(char) <= 0x7E else "_" for char in filename)
    fallback = fallback.replace("\\", "\\\\").replace('"', '\\"')
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _create_native_http_app(
    *,
    cfg: HydraConfig,
    catalog: OperationCatalog,
    service_plugins: Sequence[ServicePlugin],
    deployment_scope: DeploymentScope | str,
    artifact_store: ArtifactStore,
) -> object:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response, StreamingResponse
    from starlette.routing import Route

    async def health(_request: Request) -> object:
        return _native_response({"status": "ok"})

    async def info(_request: Request) -> object:
        version_info = runtime_version_info(
            service_plugins,
            deployment_scope=deployment_scope,
        )
        server_info = cast(Mapping[str, object], version_info["server"])
        return _native_response(
            {
                "name": "arbiter",
                "version": server_info["version"],
                "api_version": server_info["api_version"],
                "deployment_scope": version_info["deployment_scope"],
                "source": version_info["source"],
            }
        )

    async def plugins(_request: Request) -> object:
        capabilities = cast(
            Sequence[Mapping[str, object]],
            catalog.describe_capabilities(
                operation_preview_limit=0,
                account_preview_limit=0,
            )["capabilities"],
        )
        return _native_response(
            {
                "plugins": [
                    {
                        "id": capability["id"],
                        "summary": capability.get("description", ""),
                    }
                    for capability in capabilities
                ]
            }
        )

    async def plugin_details(request: Request) -> object:
        try:
            return _native_response(
                _native_plugin_payload(catalog, request.path_params["plugin_id"])
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def plugin_accounts(request: Request) -> object:
        try:
            return _native_response(
                _native_plugin_accounts_payload(cfg, request.path_params["plugin_id"])
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def account_details(request: Request) -> object:
        try:
            return _native_response(
                _native_account_detail_payload(
                    cfg,
                    request.path_params["plugin_id"],
                    request.path_params["account"],
                )
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def policy_details(request: Request) -> object:
        try:
            return _native_response(
                _native_policy_payload(
                    cfg,
                    request.path_params["plugin_id"],
                    request.path_params["policy"],
                )
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def plugin_operations(request: Request) -> object:
        plugin = request.path_params["plugin_id"]
        try:
            payload = catalog.info(kind="ops", plugin=plugin)
            operations = cast(Sequence[Mapping[str, object]], payload["operations"])
            return _native_response(
                {
                    "plugin": plugin,
                    "operations": [
                        {
                            "id": operation["id"],
                            "summary": operation.get("description", ""),
                            "when_to_use": operation.get("description", ""),
                        }
                        for operation in operations
                    ],
                }
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def operation_details(request: Request) -> object:
        try:
            return _native_response(
                _operation_details_payload(catalog, request.path_params["operation_id"])
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def invoke_operation(request: Request) -> object:
        operation_id_value = request.path_params["operation_id"]
        try:
            args = await _read_operation_arguments(request)
        except Exception as exc:
            return _native_exception_response(exc)
        try:
            result = catalog.invoke_operation(operation_id_value, args)
            return _native_response(
                _operation_response_payload(result, artifact_store=artifact_store)
            )
        except Exception as exc:
            return _native_exception_response(exc, validation_status_code=422)

    async def artifact_metadata(request: Request) -> object:
        artifact_id = request.path_params["artifact_id"]
        nonce = request.query_params.get("nonce", "")
        if not nonce:
            return _native_error_response(
                status_code=404,
                code="artifact_not_found",
                message="Artifact was not found.",
            )
        try:
            artifact = artifact_store.inspect(artifact_id, nonce)
            return _native_response(
                _native_artifact_metadata(
                    artifact_id=artifact_id,
                    artifact=artifact,
                    nonce=nonce,
                )
            )
        except Exception as exc:
            return _native_exception_response(exc)

    async def artifact_content(request: Request) -> object:
        artifact_id = request.path_params["artifact_id"]
        nonce = request.query_params.get("nonce", "")
        if not nonce:
            return _native_error_response(
                status_code=404,
                code="artifact_not_found",
                message="Artifact was not found.",
            )
        try:
            if request.method == "HEAD":
                artifact = artifact_store.inspect(artifact_id, nonce)
                response = Response(status_code=200, media_type=artifact.content_type)
                response.headers["Content-Length"] = str(artifact.size)
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Digest"] = f"sha-256={artifact.sha256}"
                response.headers["X-Arbiter-Artifact-SHA256"] = artifact.sha256
                return response
            artifact = artifact_store.open_once(artifact_id, nonce)

            async def content_chunks() -> AsyncGenerator[bytes, None]:
                with artifact.path.open("rb") as handle:
                    while chunk := handle.read(64 * 1024):
                        yield chunk

            response = StreamingResponse(
                content_chunks(),
                media_type=artifact.content_type,
            )
            if artifact.filename is not None:
                response.headers["Content-Disposition"] = (
                    _attachment_content_disposition(artifact.filename)
                )
            else:
                response.headers["Content-Disposition"] = "attachment"
            response.headers["Content-Length"] = str(artifact.size)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Digest"] = f"sha-256={artifact.sha256}"
            response.headers["X-Arbiter-Artifact-SHA256"] = artifact.sha256
            return response
        except Exception as exc:
            return _native_exception_response(exc)

    return Starlette(
        routes=[
            Route(HEALTH_ROUTE, health, methods=["GET"]),
            Route(f"{API_ROUTE_PREFIX}/info", info, methods=["GET"]),
            Route(f"{API_ROUTE_PREFIX}/plugins", plugins, methods=["GET"]),
            Route(
                f"{API_ROUTE_PREFIX}/plugins/{{plugin_id}}",
                plugin_details,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/plugins/{{plugin_id}}/accounts",
                plugin_accounts,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/plugins/{{plugin_id}}/accounts/{{account}}",
                account_details,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/plugins/{{plugin_id}}/policies/{{policy}}",
                policy_details,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/plugins/{{plugin_id}}/operations",
                plugin_operations,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/operations/{{operation_id}}",
                operation_details,
                methods=["GET"],
            ),
            Route(
                f"{API_ROUTE_PREFIX}/operations/{{operation_id}}",
                invoke_operation,
                methods=["POST"],
            ),
            Route(
                f"{ARTIFACT_ROUTE_PREFIX}/{{artifact_id}}",
                artifact_metadata,
                methods=["GET"],
            ),
            Route(
                f"{ARTIFACT_ROUTE_PREFIX}/{{artifact_id}}/content",
                artifact_content,
                methods=["GET", "HEAD"],
            ),
        ]
    )


def build_server(
    cfg: HydraConfig,
    service_plugins: Sequence[ServicePlugin] | None = None,
    *,
    prepare_tls: bool = True,
) -> NativeHTTPServer:
    available_service_plugins = (
        discover_service_plugins() if service_plugins is None else service_plugins
    )
    active_service_plugins = _configured_service_plugins(
        cfg,
        available_service_plugins,
    )
    runtime_dependencies: dict[str, object] = {}
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
    catalog = OperationCatalog(
        active_service_plugins,
        ServicePluginContext(runtimes=app.runtime_registry),
        max_account_preview_limit=cfg.arbiter.discovery.max_account_preview_limit,
        max_operation_preview_limit=cfg.arbiter.discovery.max_operation_preview_limit,
    )
    native_app = _create_native_http_app(
        cfg=cfg,
        catalog=catalog,
        service_plugins=active_service_plugins,
        deployment_scope=cfg.arbiter.deployment_scope,
        artifact_store=artifact_store,
    )
    ssl_certfile, ssl_keyfile = _server_tls_files(
        cfg,
        generate_self_signed=prepare_tls,
    )

    return NativeHTTPServer(
        app=native_app,
        host=str(cfg.arbiter.server.bind.host),
        port=int(cfg.arbiter.server.bind.port),
        ssl_certfile=str(ssl_certfile),
        ssl_keyfile=str(ssl_keyfile),
    )


def _run_server(server: NativeHTTPServer, transport: TransportMode) -> None:
    if transport != "https":
        raise ValueError(f"unsupported Arbiter HTTPS transport: {transport}")
    import uvicorn

    uvicorn.run(
        cast(Any, server.app),
        host=server.host,
        port=server.port,
        ssl_certfile=server.ssl_certfile,
        ssl_keyfile=server.ssl_keyfile,
        log_config=None,
    )


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


def _env_reference_default_from_omegaconf(value: str, *, name: str) -> str | None:
    full_match = ENV_REFERENCE_FULL_VALUE_PATTERN.fullmatch(value)
    if full_match is None or full_match.group("name") != name:
        return None
    missing = object()
    previous = os.environ.pop(name, missing)
    try:
        resolved_container = cast(
            dict[str, object],
            OmegaConf.to_container(
                OmegaConf.create({"value": value}),
                resolve=True,
            ),
        )
        resolved = resolved_container["value"]
    except InterpolationResolutionError:
        return None
    finally:
        if previous is missing:
            os.environ.pop(name, None)
        else:
            os.environ[name] = cast(str, previous)
    return str(resolved)


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
        default = _env_reference_default_from_omegaconf(value, name=name)
        existing = references.get(name)
        if existing is None:
            references[name] = EnvReference(name=name, block=block, default=default)
        elif existing.block == MISC_ENV_BLOCK and block != MISC_ENV_BLOCK:
            references[name] = EnvReference(name=name, block=block, default=default)
        elif existing.default is None and default is not None:
            references[name] = EnvReference(
                name=name,
                block=existing.block,
                default=default,
            )


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
            if reference.default is None and reference.name not in satisfied
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
    required_reference_count = sum(
        1 for reference in references.values() if reference.default is None
    )
    print(f"env ok: {required_reference_count} variables satisfied")
    return 0


def _format_env_file_blocks(
    block_values: Mapping[str, Mapping[str, str]],
    *,
    commented_defaults: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    commented_defaults = commented_defaults or {}
    lines: list[str] = []
    block_names = sorted(
        {
            block_name
            for block_name, values in block_values.items()
            if values
        }
        | {
            block_name
            for block_name, values in commented_defaults.items()
            if values
        }
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
        values = block_values.get(block_name, {})
        for name, value in values.items():
            lines.append(f"{name}={value}")
        for name, value in commented_defaults.get(block_name, {}).items():
            if name not in values:
                lines.append(f"# {name}={value}")
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
        if reference.default is None and reference.name not in satisfied:
            block_values.setdefault(reference.block, {})[reference.name] = ""

    commented_defaults: dict[str, dict[str, str]] = {}
    for reference in references.values():
        if reference.default is not None and reference.name not in satisfied:
            commented_defaults.setdefault(reference.block, {})[
                reference.name
            ] = reference.default

    content = _format_env_file_blocks(
        block_values,
        commented_defaults=commented_defaults,
    )
    if env_file.exists() and env_file.read_text(encoding="utf-8") == content:
        env_file.chmod(ENV_FILE_MODE)
        print(f"env file already up to date: {_display_config_path(env_file)}")
        return 0
    env_file.parent.mkdir(parents=True, exist_ok=True)
    _write_text_with_mode(env_file, content, ENV_FILE_MODE)
    print(f"wrote {_display_config_path(env_file)}")
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
    return _ensure_writable_private_data_dir(
        plugin_data_dir,
        label="plugin data directory",
        detail_name="plugin data dir",
    )


def _ensure_writable_server_data_dir(server_data_dir: Path) -> bool:
    return _ensure_writable_private_data_dir(
        server_data_dir,
        label="server data directory",
        detail_name="server data dir",
    )


def _ensure_writable_private_data_dir(
    data_dir: Path,
    *,
    label: str,
    detail_name: str,
) -> bool:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            data_dir.chmod(0o700)
        write_check = data_dir / ".arbiter-write-check"
        write_check.write_text("", encoding="utf-8")
        write_check.unlink()
    except OSError as exc:
        print_cli_error(
            f"deployment {label} is not writable",
            area="deploy",
            details=[
                f"{detail_name}: {data_dir}",
                f"error: {exc}",
                f"remove or chown the {label}, then retry",
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
        existing_value = existing_values.get(name, default)
        if name == "ARBITER_PUBLIC_SCHEME" and existing_value == "http":
            existing_value = default
        lines.append(f"{name}={existing_value}")
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
        if not _ensure_writable_server_data_dir(deploy_dir / "data" / "server"):
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
        if not _ensure_writable_server_data_dir(deploy_dir / "data" / "server"):
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
    unicode_tree = _config_check_unicode_tree_enabled(sys.stdout)
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
            failed = failed or component.status == "fail"
        for line in _color_config_check_lines(
            _config_check_tree_lines(
                components,
                unicode_tree=unicode_tree,
                width=_config_check_output_width(sys.stdout),
            ),
            color=color,
        ):
            print(line, flush=True)
        for component in components:
            for line in _color_config_check_lines(component.issue_lines, color=color):
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


def _display_config_path(path: Path) -> str:
    display_dir = os.environ.get("REPLOY_CONFIG_DISPLAY_DIR")
    if not display_dir:
        return str(path)
    container_dir = os.environ.get("REPLOY_CONFIG_CONTAINER_DIR", "/config")
    container_dir = container_dir.replace("\\", "/").rstrip("/") or "/"
    path_text = str(path).replace("\\", "/")
    if path_text == container_dir:
        return display_dir
    prefix = container_dir + "/"
    if path_text.startswith(prefix):
        display_dir = display_dir.rstrip("/\\")
        if not display_dir:
            return path_text[len(prefix) :]
        return display_dir + "/" + path_text[len(prefix) :]
    return str(path)


def _print_bootstrap_overwrite_error(path: Path) -> None:
    print_cli_error(
        "refusing to overwrite changed bootstrap file: "
        f"{_display_config_path(path)}",
        area="bootstrap",
        details=[
            "file differs from the generated bootstrap template",
            "rerun with --force to overwrite it",
        ],
    )


def _write_bootstrap_file(path: Path, content: str, *, force: bool) -> int:
    if path.exists() and not force:
        if path.read_text(encoding="utf-8") == content:
            print(f"unchanged {_display_config_path(path)}")
            return 0
        _print_bootstrap_overwrite_error(path)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_with_mode(path, content, CONFIG_FILE_MODE)
    print(f"wrote {_display_config_path(path)}")
    return 0


def _write_bootstrap_files(
    files: Sequence[tuple[Path, str]],
    *,
    force: bool,
) -> int:
    for path, content in files:
        if path.exists() and not force and path.read_text(encoding="utf-8") != content:
            _print_bootstrap_overwrite_error(path)
            return 1
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force and path.read_text(encoding="utf-8") == content:
            print(f"unchanged {_display_config_path(path)}")
        else:
            _write_text_with_mode(path, content, CONFIG_FILE_MODE)
            print(f"wrote {_display_config_path(path)}")
    return 0


def _print_bootstrap_file_plan(
    files: Sequence[tuple[Path, str]],
    *,
    force: bool,
) -> None:
    print("dry mode; no files changed")
    for path, content in files:
        if not path.exists():
            action = "would create"
        elif path.read_text(encoding="utf-8") == content:
            action = "would leave unchanged"
        elif force:
            action = "would overwrite"
        else:
            action = "would refuse to overwrite changed"
        print(f"{action} {_display_config_path(path)}")


def _run_bootstrap_server(
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
            f"main config not found: {config_file}; run bootstrap --server first",
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


def _split_cli_name_list(
    value: str,
    *,
    label: str,
    area: str,
) -> list[str] | None:
    values = []
    seen_values = set()
    for item in value.split(","):
        name = item.strip()
        if not name or name in seen_values:
            continue
        values.append(name)
        seen_values.add(name)
    if not values:
        print_cli_error(f"{area} requires {label}", area=area)
        return None
    return values


def _run_config_activate_account(
    *,
    config_dir: str,
    config_name: str,
    plugins: Sequence[str],
    name: str,
) -> int:
    config_dir_path = Path(config_dir).expanduser()
    selections = []
    for plugin in plugins:
        if not _validate_bootstrap_object_args(plugin, name):
            return 2
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
        selections.append((plugin, policy_config_name))
    config_file = _config_file_path(config_dir_path, config_name)
    lines = _load_main_config_lines(config_file)
    if lines is None:
        return 1
    try:
        changed = False
        for plugin, policy_config_name in selections:
            changed = (
                _add_group_item(
                    lines,
                    _config_group_for_kind("account"),
                    _config_group_item(plugin, name),
                )
                or changed
            )
            changed = (
                _add_group_item(
                    lines,
                    _config_group_for_kind("policy"),
                    _config_group_item(plugin, policy_config_name),
                )
                or changed
            )
    except ValueError as exc:
        print_cli_error(str(exc), area="config")
        return 1
    if changed:
        _write_main_config_lines(config_file, lines)
        print(f"updated {_display_config_path(config_file)}")
    elif len(plugins) == 1:
        print(f"account already active: {plugins[0]}/{name}")
    else:
        accounts = ", ".join(f"{plugin}/{name}" for plugin in plugins)
        print(f"accounts already active: {accounts}")
    return 0


def _run_config_deactivate_account(
    *,
    config_dir: str,
    config_name: str,
    plugins: Sequence[str],
    name: str,
) -> int:
    config_dir_path = Path(config_dir).expanduser()
    selections = []
    for plugin in plugins:
        if not _validate_bootstrap_object_args(plugin, name):
            return 2
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
        selections.append((plugin, policy_name, policy_config_name))
    config_file = _config_file_path(config_dir_path, config_name)
    lines = _load_main_config_lines(config_file)
    if lines is None:
        return 1
    changed = False
    for plugin, policy_name, policy_config_name in selections:
        changed = (
            _remove_group_item(
                lines,
                _config_group_for_kind("account"),
                _config_group_item(plugin, name),
            )
            or changed
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
        print(f"updated {_display_config_path(config_file)}")
    elif len(plugins) == 1:
        print(f"account already inactive: {plugins[0]}/{name}")
    else:
        accounts = ", ".join(f"{plugin}/{name}" for plugin in plugins)
        print(f"accounts already inactive: {accounts}")
    return 0


def _run_config_activation_summary(*, config_dir: str, config_name: str) -> int:
    config_dir_path = Path(config_dir).expanduser()
    config_file = _config_file_path(config_dir_path, config_name)
    lines = _load_main_config_lines(config_file)
    if lines is None:
        return 1

    installed_plugins = service_plugin_names()
    active_plugins_by_account: dict[str, list[str]] = {}
    for item in _active_group_items(lines, _config_group_for_kind("account")):
        plugin, separator, account = item.partition("/")
        if separator and plugin and account:
            active_plugins_by_account.setdefault(account, []).append(plugin)

    configured_plugins_by_account: dict[str, list[str]] = {}
    for plugin in installed_plugins:
        account_dir = config_dir_path / "arbiter" / "account" / plugin
        if not account_dir.is_dir():
            continue
        for account_file in sorted(account_dir.glob("*.yaml")):
            configured_plugins_by_account.setdefault(account_file.stem, []).append(
                plugin
            )

    command_prefix = _display_command_prefix(config_dir=config_dir_path)
    color = _config_check_color_enabled(sys.stdout)
    print("installed plugins:")
    for plugin in installed_plugins:
        print(f"  {plugin}")

    account_names = sorted(
        set(configured_plugins_by_account) | set(active_plugins_by_account)
    )
    if not account_names:
        print("create account configs:")
        plugins = ",".join(installed_plugins)
        if installed_plugins:
            print(
                f"  single plugin: {command_prefix} bootstrap "
                f"--plugin {installed_plugins[0]} --account my_account"
            )
        if len(installed_plugins) > 1:
            print(
                f"  batch: {command_prefix} bootstrap "
                f"--plugins {plugins} --account my_account"
            )
            print(
                f"  preview: {command_prefix} bootstrap "
                f"--plugins {plugins} --account my_account --dry-mode"
            )
        print("  account name is optional; default is used when omitted")
        return 0

    print("accounts:")
    for account in account_names:
        active_plugins = sorted(
            dict.fromkeys(active_plugins_by_account.get(account, []))
        )
        configured_plugins = sorted(
            dict.fromkeys(configured_plugins_by_account.get(account, []))
        )
        inactive_plugins = [
            plugin for plugin in configured_plugins if plugin not in active_plugins
        ]
        print(
            _format_activation_account_row(
                account,
                installed_plugins=installed_plugins,
                active_plugins=active_plugins,
                color=color,
            )
        )
        if inactive_plugins:
            print("    activate:")
            for plugin in inactive_plugins:
                print(
                    f"      {command_prefix} config activate "
                    f"--plugin {plugin} --account {account}"
                )
    return 0


def _run_config_account_activation(
    *,
    action: str,
    config_dir: str,
    config_name: str,
    option_plugin: str | None,
    option_account: str | None,
) -> int:
    if option_plugin is None and option_account is None:
        return _run_config_activation_summary(
            config_dir=config_dir,
            config_name=config_name,
        )
    if option_plugin is None or option_account is None:
        print_cli_error(
            f"config {action} requires --plugin/--plugins and "
            "--account/--accounts",
            area="config",
        )
        return 2
    plugins = _split_cli_name_list(option_plugin, label="plugin", area="config")
    if plugins is None:
        return 2
    if action == "activate":
        return _run_config_activate_account(
            config_dir=config_dir,
            config_name=config_name,
            plugins=plugins,
            name=option_account,
        )
    if action == "deactivate":
        return _run_config_deactivate_account(
            config_dir=config_dir,
            config_name=config_name,
            plugins=plugins,
            name=option_account,
        )
    raise AssertionError(f"unknown activation action: {action}")


def _display_command_prefix(*, config_dir: Path) -> str:
    app_command_prefix = os.environ.get("REPLOY_APP_COMMAND_PREFIX", "").strip()
    if app_command_prefix:
        return app_command_prefix
    return f"arbiter-server --config-dir {config_dir}"


def _display_activation_command_prefix(*, config_dir: Path) -> str:
    app_command_prefix = os.environ.get("REPLOY_APP_COMMAND_PREFIX", "").strip()
    if app_command_prefix:
        return f"{app_command_prefix} activate"
    return f"{_display_command_prefix(config_dir=config_dir)} config activate"


def _print_bootstrap_activation_hint(
    *,
    config_dir: Path,
    config_name: str,
    plugins: Sequence[str],
    kind: BootstrapObjectKind,
    name: str,
) -> None:
    config_file = config_dir / f"{config_name}.yaml"
    print("")
    if kind == "account":
        command_prefix = _display_command_prefix(config_dir=config_dir)
        activation_command_prefix = _display_activation_command_prefix(
            config_dir=config_dir
        )
        if len(plugins) == 1:
            print("Edit the generated account and policy files.")
            print("")
            print("Rebuild the env file after edits:")
            print(f"  {command_prefix} env bootstrap")
            print("")
            print("Review activation status:")
            print(f"  {activation_command_prefix}")
            print("")
            print("Activate the account when ready:")
        else:
            print("Edit the generated account and policy files.")
            print("")
            print("Rebuild the env file after edits:")
            print(f"  {command_prefix} env bootstrap")
            print("")
            print("Review activation status:")
            print(f"  {activation_command_prefix}")
            print("")
            print("Activate the accounts when ready:")
        plugin_flag = "--plugin" if len(plugins) == 1 else "--plugins"
        print(
            f"  {activation_command_prefix} "
            f"{plugin_flag} {','.join(plugins)} --account {name}"
        )
        print("")
        print("Then inspect the composed config with:")
        print(f"  {command_prefix} config show")
        return
    if len(plugins) == 1:
        print(f"To activate the generated policy, add this to {config_file}:")
    else:
        print(f"To activate the generated policies, add this to {config_file}:")
    print("defaults:")
    print(f"  - {_config_group_for_kind('policy')}:")
    for plugin in plugins:
        print(f"    - {_config_group_item(plugin, name)}")
    print("")
    print("Then inspect the composed config with:")
    print(f"  {_display_command_prefix(config_dir=config_dir)} config show")


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
    dry_mode: bool = False,
) -> int:
    plugins = _split_cli_name_list(plugin, label="plugin", area="bootstrap")
    if plugins is None:
        return 2
    if len(plugins) > 1 and list_variants:
        print_cli_error(
            "--list-variants accepts one plugin at a time",
            area="bootstrap",
        )
        return 2
    if list_variants:
        return _run_plugin_bootstrap_list_variants(plugin=plugins[0], kind=kind)
    if kind == "account" and name is None:
        name = "default"
    if name is None:
        print_cli_error(f"bootstrap --{kind} requires name", area="bootstrap")
        return 2
    config_dir_path = _ensure_config_dir(config_dir)
    if config_dir_path is None:
        return 2
    files = []
    for plugin_name in plugins:
        plugin_files = _plugin_bootstrap_files(
            plugin=plugin_name,
            kind=kind,
            name=name,
            config_dir=config_dir_path,
            variant=variant,
        )
        if plugin_files is None:
            return 1
        if not plugin_files:
            return 2
        files.extend(plugin_files)
    if dry_mode:
        _print_bootstrap_file_plan(files, force=force)
        return 0
    result = _write_bootstrap_files(files, force=force)
    if result == 0:
        _print_bootstrap_activation_hint(
            config_dir=config_dir_path,
            config_name=config_name,
            plugins=plugins,
            kind=kind,
            name=name,
        )
    return result


def _plugin_bootstrap_files(
    *,
    plugin: str,
    kind: BootstrapObjectKind,
    name: str,
    config_dir: Path,
    variant: str | None,
) -> list[tuple[Path, str]] | None:
    if not _validate_bootstrap_object_args(plugin, name):
        return []
    content = _load_plugin_example_yaml(plugin, kind, name, variant=variant)
    if content is None:
        return None
    files = [
        (
            _bootstrap_object_path(
                config_dir=config_dir,
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
            return None
        files.append(
            (
                _bootstrap_object_path(
                    config_dir=config_dir,
                    plugin=plugin,
                    kind="policy",
                    name=policy_name,
                ),
                policy_content,
            )
        )
    return files


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
        description="Policy-controlled HTTP gateway for agent-accessible services.",
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

    serve = subcommands.add_parser("serve", help="run the Arbiter HTTP server")
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
        action_description = (
            "Show account activation status when no target is provided. "
            f"With a target, {activation_action} an account for one or more "
            "comma-separated plugins."
        )
        activation = config_subcommands.add_parser(
            activation_action,
            usage=f"%(prog)s [--plugin PLUGIN --account NAME]",
            help=f"{activation_action} a config object in the main defaults list",
            description=action_description,
            epilog=(
                "Examples:\n"
                f"  arbiter-server config {activation_action}\n"
                f"  arbiter-server config {activation_action} --plugin imap --account my_account\n"
                f"  arbiter-server config {activation_action} --plugins imap,smtp --account my_account"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        activation_plugin_group = activation.add_mutually_exclusive_group()
        activation_plugin_group.add_argument(
            "--plugin",
            "--plugins",
            dest="option_plugin",
            metavar="PLUGIN",
            help="plugin name or comma-separated plugin names",
        )
        activation_account_group = activation.add_mutually_exclusive_group()
        activation_account_group.add_argument(
            "--account",
            "--accounts",
            dest="option_account",
            metavar="NAME",
            help="account name to activate or deactivate for selected plugins",
        )

    bootstrap = subcommands.add_parser(
        "bootstrap",
        help="create config templates",
        description=(
            "Create Arbiter config templates. Prefer the option form for "
            "plugin account bootstrap."
        ),
        epilog=(
            "Examples:\n"
            "  arbiter-server bootstrap --server\n"
            "  arbiter-server bootstrap --plugin imap --account my_account\n"
            "  arbiter-server bootstrap --plugins imap,smtp --account my_account\n"
            "  arbiter-server bootstrap --plugin smtp --policy readonly\n"
            "  arbiter-server bootstrap --plugin imap --policy --list-variants\n"
            "  arbiter-server bootstrap --plugins imap,smtp --account my_account --dry-mode\n"
            "\n"
            "If --account/--accounts is omitted, the account name defaults to "
            "default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bootstrap_plugin_group = bootstrap.add_mutually_exclusive_group()
    bootstrap_plugin_group.add_argument(
        "--plugin",
        "--plugins",
        dest="option_plugin",
        metavar="PLUGIN",
        help="plugin name or comma-separated plugin names",
    )
    bootstrap_plugin_group.add_argument(
        "--server",
        action="store_true",
        help="create the main Arbiter config",
    )
    bootstrap_kind_group = bootstrap.add_mutually_exclusive_group()
    bootstrap_kind_group.add_argument(
        "--account",
        "--accounts",
        dest="option_account",
        metavar="NAME",
        help="account name to create for selected plugins (default: default)",
    )
    bootstrap_kind_group.add_argument(
        "--policy",
        "--policies",
        dest="option_policy",
        metavar="NAME",
        nargs="?",
        const="",
        help="policy name to create for selected plugins",
    )
    bootstrap.add_argument(
        "--variant",
        help="bootstrap template variant when the plugin provides variants",
    )
    bootstrap.add_argument(
        "--list-variants",
        action="store_true",
        help="list bootstrap variants for this plugin and kind",
    )
    bootstrap.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config object file",
    )
    bootstrap.add_argument(
        "--dry-mode",
        action="store_true",
        help="show which bootstrap files would be written without changing files",
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
            option_plugin=namespace.option_plugin,
            option_account=namespace.option_account,
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
    if namespace.command == "bootstrap" and namespace.server:
        if namespace.option_plugin is not None:
            print_cli_error(
                "--server cannot be combined with --plugin/--plugins",
                area="bootstrap",
            )
            return 2
        if namespace.option_account is not None or namespace.option_policy is not None:
            print_cli_error(
                "--server cannot be combined with --account/--accounts or --policy/--policies",
                area="bootstrap",
            )
            return 2
        if namespace.variant is not None or namespace.list_variants or namespace.dry_mode:
            print_cli_error(
                "--server cannot be combined with plugin bootstrap options",
                area="bootstrap",
            )
            return 2
        return _run_bootstrap_server(
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            force=namespace.force,
        )
    if namespace.command == "bootstrap" and namespace.option_plugin is not None:
        kind: BootstrapObjectKind = "account"
        name = namespace.option_account
        if namespace.option_policy is not None:
            kind = "policy"
            name = namespace.option_policy or None
        return _run_plugin_bootstrap(
            plugin=namespace.option_plugin,
            kind=kind,
            name=name,
            config_dir=namespace.config_dir,
            config_name=namespace.config_name,
            force=namespace.force,
            variant=namespace.variant,
            list_variants=namespace.list_variants,
            dry_mode=namespace.dry_mode,
        )
    if (
        namespace.command == "bootstrap"
        and (
            namespace.option_account is not None
            or namespace.option_policy is not None
            or namespace.variant is not None
            or namespace.list_variants
            or namespace.dry_mode
        )
    ):
        print_cli_error(
            "bootstrap options require --plugin/--plugins",
            area="bootstrap",
        )
        return 2

    parser.error("unknown command")
