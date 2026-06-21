import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from typing import Any, cast

import pytest
from omegaconf import OmegaConf

from arbiter_server.config import (
    AppConfig,
    ArbiterConfig,
    DiscoveryConfig,
    ServerTlsSource,
    StorageConfig,
)
from arbiter_server.file_protection.windows import (
    _WindowsAccessAce,
    _windows_icacls_remediation,
    _windows_unallowed_access_reason,
    _windows_unallowed_permission_reason,
    ensure_runtime_config_permissions as ensure_windows_runtime_config_permissions,
)
from arbiter_server.main import (
    ENV_FILE_MODE,
    _artifact_base_url,
    _build_local_source_wheel,
    _default_container_user,
    _docker_bundle_plugin,
    _run_config_check,
    _run_server,
    _server_tls_files,
    _write_text_with_mode,
    build_app,
    build_server,
    compose_config,
    ConfigCheckAccountResult,
    ConfigCheckComponentReport,
    ConfigCheckReport,
    config_check_components,
    config_check_report,
    config_check_summary,
    ensure_runnable_config,
    load_env_file,
    log_startup_summary,
    main,
    service_plugin_names,
)
from arbiter_server.artifacts import ArtifactStore
from arbiter_server.plugins import discover_service_plugins
from arbiter_imap import IMAPRuntime, IMAPServicePlugin
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
    IMAPFolderConfig,
    IMAPFolderKind,
    IMAPFolderOperationPolicyConfig,
    IMAPOperationDecision,
    IMAPSystemFlagsPolicyConfig,
)
from arbiter_smtp import SendEmailResult, SMTPRuntime, SMTPServicePlugin
from arbiter_smtp.config import SMTPConfig, SMTPServicePolicyConfig
from arbiter_server.services import (
    SERVER_API_VERSION,
    SERVER_VERSION,
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    CapabilityDescriptor,
    ConfigCheckError,
    ConfigCheckIssue,
    OperationDescriptor,
    RuntimeRegistry,
    ServicePlugin,
    ServicePluginContext,
    ServiceRuntimeContext,
)


def test_docker_bundle_plugin_sanitizes_generated_metadata() -> None:
    assert _docker_bundle_plugin("bad\tname", "acme-mail", "Plugin") is None
    assert _docker_bundle_plugin("acme", "../acme", "Plugin") is None

    plugin = _docker_bundle_plugin(
        "acme",
        "Acme.Mail",
        "Acme\tmail\nplugin",
    )

    assert plugin is not None
    assert plugin.name == "acme"
    assert plugin.package == "acme-mail"
    assert plugin.description == "Acme mail plugin"


_SUPPORTS_POSIX_FILE_MODES = os.name != "nt"


def _assert_posix_mode(path: Path, mode: int) -> None:
    if _SUPPORTS_POSIX_FILE_MODES:
        assert (path.stat().st_mode & 0o777) == mode


def _assert_posix_executable(path: Path) -> None:
    if _SUPPORTS_POSIX_FILE_MODES:
        assert path.stat().st_mode & 0o111


def _run_with_pty(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        pytest.skip("PTY checks require a POSIX platform")
    helper = r"""
import errno
import os
import pty
import select
import subprocess
import sys

master_fd, slave_fd = pty.openpty()
try:
    process = subprocess.Popen(
        sys.argv[1:],
        stdin=subprocess.DEVNULL,
        stdout=slave_fd,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    os.close(slave_fd)
    slave_fd = -1
    stdout_chunks = []
    while True:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if master_fd in ready:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            stdout_chunks.append(chunk)
        if process.poll() is not None:
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0)
                if master_fd not in ready:
                    break
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                stdout_chunks.append(chunk)
            break
    stderr = process.stderr.read() if process.stderr is not None else b""
    sys.stdout.buffer.write(b"".join(stdout_chunks))
    sys.stderr.buffer.write(stderr)
    raise SystemExit(process.wait())
finally:
    os.close(master_fd)
    if slave_fd >= 0:
        os.close(slave_fd)
"""
    return subprocess.run(
        [sys.executable, "-c", helper, *[str(part) for part in command]],
        check=False,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def _patch_installed_deploy_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server_version: str = "0.9.0.dev2",
    plugins: Sequence[tuple[str, str, str]] = (
        ("imap", "arbiter-imap", "0.9.0.dev2"),
        ("smtp", "arbiter-smtp", "0.9.0.dev2"),
    ),
    local_sources: Mapping[str, Path] | None = None,
) -> None:
    local_source_map: Mapping[str, Path] = local_sources or {}

    class FakeDeployDistribution:
        def __init__(self, distribution_name: str) -> None:
            self.metadata = {"Name": distribution_name}
            self.files: tuple[Path, ...] = ()
            self._direct_url_path: Path | None = None
            source_root = local_source_map.get(distribution_name)
            if source_root is None:
                return
            dist_info = source_root / f"{distribution_name}.dist-info"
            dist_info.mkdir(parents=True, exist_ok=True)
            self._direct_url_path = dist_info / "direct_url.json"
            self._direct_url_path.write_text(
                json.dumps(
                    {
                        "dir_info": {"editable": True},
                        "url": source_root.resolve().as_uri(),
                    }
                ),
                encoding="utf-8",
            )
            self.files = (Path(f"{distribution_name}.dist-info/direct_url.json"),)

        def locate_file(self, _path: Path) -> Path:
            assert self._direct_url_path is not None
            return self._direct_url_path

    class FakeDeployPlugin:
        def __init__(self, name: str, version: str) -> None:
            self.name = name
            self.version = version
            self.server_api_version = SERVER_API_VERSION

    class FakeDeployEntryPoint:
        def __init__(
            self,
            plugin_name: str,
            distribution_name: str,
            plugin_version: str,
        ) -> None:
            self.name = plugin_name
            self.value = f"{distribution_name}:plugin"
            self.dist = FakeDeployDistribution(distribution_name)
            self._plugin = FakeDeployPlugin(plugin_name, plugin_version)

        def load(self) -> Callable[[], FakeDeployPlugin]:
            return lambda: self._plugin

    class FakeDeployEntryPoints(list[FakeDeployEntryPoint]):
        def select(self, *, group: str) -> "FakeDeployEntryPoints":
            assert group == SERVICE_PLUGIN_ENTRY_POINT_GROUP
            return self

    monkeypatch.setattr(
        "arbiter_server.main.arbiter_server_version", lambda: server_version
    )
    monkeypatch.setattr(
        "arbiter_server.main.distribution",
        lambda distribution_name: FakeDeployDistribution(distribution_name),
    )
    monkeypatch.setattr(
        "arbiter_server.main.entry_points",
        lambda: FakeDeployEntryPoints(
            [
                FakeDeployEntryPoint(plugin_name, distribution_name, plugin_version)
                for plugin_name, distribution_name, plugin_version in plugins
            ]
        ),
    )


def _expected_version_info(
    *,
    commit: str | None,
    dirty: bool | None,
) -> dict[str, object]:
    plugins = sorted(_test_service_plugins(), key=lambda plugin: plugin.name)
    return {
        "server": {"version": SERVER_VERSION, "api_version": SERVER_API_VERSION},
        "deployment_scope": "unknown",
        "source": {"commit": commit, "dirty": dirty, "build_time": None},
        "plugins": [
            {
                "name": plugin.name,
                "version": plugin.version,
                "server_api_version": plugin.server_api_version,
            }
            for plugin in plugins
        ],
    }


def test_build_app_accepts_hydra_config() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp_imap())

    app = build_app(cfg, service_plugins=_test_service_plugins())

    assert set(app.runtime_registry.keys()) == {"imap", "smtp"}


def test_build_app_uses_dictconfig_plugin_storage_root(tmp_path: Path) -> None:
    captured: dict[str, Path] = {}

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            storage = context.dependencies["plugin_storage"]
            captured["path"] = cast(Any, storage).path("sentinel")
            return object()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    plugin_data_dir = tmp_path / "plugins"
    cfg = OmegaConf.structured(
        AppConfig(
            arbiter=ArbiterConfig(
                storage=StorageConfig(plugin_data_dir=str(plugin_data_dir)),
                account={"fake": {"primary": {}}},
                policy={"fake": {}},
            )
        )
    )

    build_app(cfg, service_plugins=[FakePlugin()])

    assert captured["path"] == plugin_data_dir / "fake" / "sentinel"


def test_build_app_list_accounts_uses_real_config_shape() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp())

    app = build_app(cfg, service_plugins=_test_service_plugins())

    assert app.list_accounts() == {
        "smtp": {
            "primary": {
                "description": "Bot-owned account for automated email tasks.",
                "guidance": "",
                "policy": "bot",
                "enabled": True,
                "send": "allowed",
            },
        },
    }


def test_build_app_rejects_unknown_service_policy_reference() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp())
    cfg.arbiter.account.smtp.primary.policy = "missing"

    with pytest.raises(
        ValueError,
        match="SMTP account references an unknown policy: primary -> missing",
    ):
        build_app(cfg, service_plugins=_test_service_plugins())


def test_build_app_activates_dynamic_entry_point_service() -> None:
    class FakeExternalRuntime:
        def account_summaries(self) -> dict[str, object]:
            return {"bot": {"enabled": True}}

    class FakeExternalPlugin:
        name = "whatsapp"
        version = "0.9.0"
        server_api_version = SERVER_API_VERSION

        def __init__(self) -> None:
            self.accounts: Mapping[str, object] | None = None
            self.policies: Mapping[str, object] | None = None

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: object,
        ) -> object:
            self.accounts = accounts
            self.policies = policies
            return FakeExternalRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(
                name=self.name,
                description="Send messages through WhatsApp.",
            )

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> tuple[OperationDescriptor, ...]:
            return ()

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            raise ValueError(f"unknown WhatsApp operation: {operation}")

    plugin = FakeExternalPlugin()
    cfg = OmegaConf.create(
        {
            "arbiter": {
                "server": {},
                "account": {
                    "whatsapp": {
                        "bot": {
                            "policy": "bot",
                            "phone_number": "+15555550100",
                        }
                    }
                },
                "policy": {
                    "whatsapp": {
                        "bot": {
                            "allow_send": True,
                        }
                    }
                },
                "etc": {},
            },
        }
    )

    app = build_app(cfg, service_plugins=[plugin])

    assert plugin.accounts is not None
    assert plugin.policies is not None
    assert set(plugin.accounts) == {"bot"}
    assert set(plugin.policies) == {"bot"}
    assert app.list_accounts() == {"whatsapp": {"bot": {"enabled": True}}}


def test_discover_service_plugins_loads_entry_point_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        def __init__(self, name: str) -> None:
            self.name = name
            self.version = "0.9.0"
            self.server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: object,
        ) -> object:
            return object()

    smtp_plugin = FakePlugin("smtp")
    imap_plugin = FakePlugin("imap")

    class FakeEntryPoint:
        def __init__(self, plugin: FakePlugin) -> None:
            self._plugin = plugin

        def load(self) -> Callable[[], FakePlugin]:
            return lambda: self._plugin

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> "FakeEntryPoints":
            assert group == SERVICE_PLUGIN_ENTRY_POINT_GROUP
            return self

    monkeypatch.setattr(
        "arbiter_server.plugins.entry_points",
        lambda: FakeEntryPoints(
            [
                FakeEntryPoint(smtp_plugin),
                FakeEntryPoint(imap_plugin),
            ]
        ),
    )

    assert [plugin.name for plugin in discover_service_plugins()] == ["imap", "smtp"]


def test_discover_service_plugins_rejects_wrong_server_api_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        name = "stale"
        version = "0.7.9"
        server_api_version = "0.7"

    class FakeEntryPoint:
        name = "stale"
        value = "stale:plugin"

        def load(self) -> Callable[[], FakePlugin]:
            return FakePlugin

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> "FakeEntryPoints":
            assert group == SERVICE_PLUGIN_ENTRY_POINT_GROUP
            return self

    monkeypatch.setattr(
        "arbiter_server.plugins.entry_points",
        lambda: FakeEntryPoints([FakeEntryPoint()]),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "service plugin stale targets Arbiter server API 0.7, "
            "but loaded server API is 0.9"
        ),
    ):
        discover_service_plugins()


def test_build_app_rejects_plugin_version_outside_server_line() -> None:
    class FakeExternalPlugin:
        name = "whatsapp"
        version = "0.7.9"
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: object,
        ) -> object:
            return object()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(
                name=self.name,
                description="Send messages through WhatsApp.",
            )

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> tuple[OperationDescriptor, ...]:
            return ()

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            raise ValueError(f"unknown WhatsApp operation: {operation}")

    cfg = OmegaConf.create(
        {
            "arbiter": {
                "server": {},
                "account": {"whatsapp": {"bot": {"policy": "bot"}}},
                "policy": {"whatsapp": {"bot": {}}},
                "etc": {},
            },
        }
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "service plugin whatsapp version 0.7.9 is not on loaded "
            "server API line 0.9"
        ),
    ):
        build_app(cfg, service_plugins=[FakeExternalPlugin()])


def test_config_check_summary_validates_runtime_construction() -> None:
    assert config_check_summary(
        _app_config_with_smtp_imap(),
        service_plugins=_test_service_plugins(),
    ) == (
        "server              | pass\n"
        "Plugins             | pass\n"
        "├── smtp            | pass\n"
        "│   └── primary/bot | pass | account/policy pair valid\n"
        "└── imap            | pass\n"
        "    └── primary/bot | pass | account/policy pair valid"
    )


def test_config_check_summary_calls_active_plugin_config_checker() -> None:
    checked: dict[str, object] = {}

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            checked["accounts"] = accounts
            checked["policies"] = policies

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return object()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    assert config_check_summary(cfg, service_plugins=[FakePlugin()]) == (
        "server              | pass\n"
        "Plugins             | pass\n"
        "└── fake            | pass\n"
        "    └── primary/bot | pass | account/policy pair valid"
    )
    assert checked["accounts"] == {"primary": {"policy": "bot"}}
    assert checked["policies"] == {"bot": {}}


def test_config_check_summary_pads_leaf_columns() -> None:
    report = ConfigCheckReport(
        components=(
            ConfigCheckComponentReport(name="server"),
            ConfigCheckComponentReport(
                name="smtp",
                account_results=(
                    ConfigCheckAccountResult(
                        account="a",
                        policy="p",
                        status="pass",
                        message="short name",
                    ),
                    ConfigCheckAccountResult(
                        account="long",
                        policy="p",
                        status="warn",
                        message="long name",
                    ),
                ),
            ),
        ),
    )

    assert report.summary == (
        "server         | pass\n"
        "Plugins        | warn\n"
        "└── smtp       | warn\n"
        "    ├── a/p    | pass | short name\n"
        "    └── long/p | warn | long name"
    )


def test_config_check_report_marks_plugin_failures() -> None:
    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            raise ConfigCheckError(
                (
                    ConfigCheckIssue(
                        message="bad fake policy",
                        account="primary",
                        policy="bot",
                    ),
                )
            )

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return object()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()])

    assert report.failed is True
    assert report.summary == (
        "server              | pass\n"
        "Plugins             | fail\n"
        "└── fake            | fail\n"
        "    ├── primary/bot | pass | account/policy pair valid\n"
        "    └── primary/bot | fail | bad fake policy"
    )


def test_config_check_report_marks_unknown_account_policy_pair() -> None:
    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            raise AssertionError("runtime should not be built")

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "missing"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()])

    assert report.failed is True
    assert report.summary == (
        "server                  | pass\n"
        "Plugins                 | fail\n"
        "└── fake                | fail\n"
        "    └── primary/missing | fail | account references an unknown policy"
    )


def test_config_check_report_live_marks_failed_account_tests() -> None:
    class FakeRuntime:
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "failed",
                    "stage": "connect_auth_noop",
                    "message": "authentication failed",
                }
            }

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return FakeRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()], live=True)

    assert report.failed is True
    assert report.summary == (
        "server              | pass\n"
        "Plugins             | fail\n"
        "└── fake            | fail\n"
        "    └── primary/bot | fail | authentication failed"
    )


def test_config_check_report_live_decodes_byte_account_test_messages() -> None:
    class FakeRuntime:
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "failed",
                    "stage": "connect_auth_noop",
                    "message": b"[AUTHENTICATIONFAILED] Authentication failed.",
                }
            }

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return FakeRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()], live=True)

    assert report.summary == (
        "server              | pass\n"
        "Plugins             | fail\n"
        "└── fake            | fail\n"
        "    └── primary/bot | fail | "
        "[AUTHENTICATIONFAILED] Authentication failed."
    )


def test_config_check_report_live_decodes_byte_exception_args() -> None:
    class FakeRuntime:
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "failed",
                    "stage": "connect_auth_noop_idempotency",
                    "message": RuntimeError(
                        535,
                        b"5.7.8 Error: authentication failed: (reason unavailable)",
                    ),
                }
            }

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return FakeRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()], live=True)

    assert "b'" not in report.summary
    assert (
        "    └── primary/bot | fail | "
        "535: 5.7.8 Error: authentication failed: (reason unavailable)"
    ) in report.summary


def test_config_check_report_live_decodes_legacy_byte_string_messages() -> None:
    class FakeRuntime:
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "failed",
                    "stage": "connect_auth_noop_idempotency",
                    "message": (
                        "(535, b'5.7.8 Error: authentication failed: "
                        "(reason unavailable)')"
                    ),
                }
            }

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return FakeRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    report = config_check_report(cfg, service_plugins=[FakePlugin()], live=True)

    assert "b'" not in report.summary
    assert (
        "    └── primary/bot | fail | "
        "535: 5.7.8 Error: authentication failed: (reason unavailable)"
    ) in report.summary


def test_config_check_components_live_forwards_account_progress() -> None:
    progress_calls: list[tuple[str, str | None]] = []

    class FakeRuntime:
        def test_accounts(
            self,
            *,
            progress: Callable[[str], None],
        ) -> dict[str, object]:
            progress("primary")
            return {
                "primary": {
                    "status": "ok",
                    "stage": "connect_auth_noop",
                }
            }

    class FakePlugin:
        name = "fake"
        version = SERVER_VERSION
        server_api_version = SERVER_API_VERSION

        def register_configs(self, config_store: object) -> None:
            return None

        def bootstrap_config(self, *, kind: str, name: str) -> object | None:
            return None

        def check_config(
            self,
            *,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
        ) -> None:
            return None

        def build_runtime(
            self,
            accounts: Mapping[str, object],
            policies: Mapping[str, object],
            context: ServiceRuntimeContext,
        ) -> object:
            return FakeRuntime()

        def describe_capability(
            self,
            context: ServicePluginContext,
        ) -> CapabilityDescriptor:
            return CapabilityDescriptor(name="fake", description="Fake")

        def describe_operations(
            self,
            context: ServicePluginContext,
        ) -> list[OperationDescriptor]:
            return []

        def invoke_operation(
            self,
            operation: str,
            arguments: Mapping[str, Any],
            context: ServicePluginContext,
        ) -> object:
            return {}

    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"fake": {"primary": {"policy": "bot"}}},
            policy={"fake": {"bot": {}}},
        )
    )

    components = tuple(
        config_check_components(
            cfg,
            service_plugins=[FakePlugin()],
            live=True,
            progress=lambda component, account: progress_calls.append(
                (component, account)
            ),
        )
    )

    assert [component.name for component in components] == ["server", "fake"]
    assert components[1].lines == (
        "Plugins             | pass",
        "└── fake            | pass",
        "    └── primary/bot | pass | live account check passed",
    )
    assert progress_calls == [("server", None), ("fake", None), ("fake", "primary")]


def test_runnable_config_requires_at_least_one_service_account() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "config must define at least one service account[\\s\\S]*"
            "currently installed arbiter plugins: imap, smtp[\\s\\S]*"
            "bootstrap plugin PLUGIN account NAME"
        ),
    ):
        ensure_runnable_config(AppConfig(), service_plugins=_test_service_plugins())


def test_service_plugin_names_are_sorted() -> None:
    assert service_plugin_names(service_plugins=_test_service_plugins()) == [
        "imap",
        "smtp",
    ]


def test_cli_lists_plugins(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", "/tmp", "plugins", "list"]) == 0

    assert capsys.readouterr().out == "imap\nsmtp\n"


def test_server_cli_help_uses_arbiter_server_program_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0

    assert capsys.readouterr().out.startswith("usage: arbiter-server ")


def test_server_cli_reports_clean_keyboard_interrupt(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_keyboard_interrupt(_server: object, _transport: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "arbiter_server.main.compose_config",
        lambda **_kwargs: OmegaConf.structured(_app_config_with_smtp()),
    )
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: object(),
    )
    monkeypatch.setattr("arbiter_server.main._run_server", raise_keyboard_interrupt)

    assert main(["--config-dir", "/tmp", "serve"]) == 130

    assert capsys.readouterr().err == "Arbiter server stopped.\n"


def test_cli_serve_unsafe_skip_runtime_permission_checks_only_skips_permission_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["--config-dir", str(tmp_path), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()

    def reject_permissions(**_kwargs: object) -> None:
        raise ValueError("permission sentinel")

    monkeypatch.setattr(
        "arbiter_server.main.ensure_runtime_config_permissions",
        reject_permissions,
    )

    assert main(["--config-dir", str(tmp_path), "serve"]) == 1
    assert "permission sentinel" in capsys.readouterr().err

    assert (
        main(
            [
                "--config-dir",
                str(tmp_path),
                "--unsafe-skip-runtime-permission-checks",
                "serve",
            ]
        )
        == 1
    )
    assert "config must define at least one service account" in capsys.readouterr().err


def test_compose_config_registers_configs_before_composing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    (tmp_path / "config.yaml").write_text(
        "arbiter:\n  server:\n    name: arbiter\n",
        encoding="utf-8",
    )

    def fake_register_configs() -> None:
        calls.append("register_configs")

    monkeypatch.setattr("arbiter_server.main.register_configs", fake_register_configs)

    cfg = compose_config(config_dir=tmp_path, config_name="config")

    assert cfg.arbiter.server.name == "arbiter"
    assert calls == ["register_configs"]


def test_compose_config_deployment_scope_override_uses_structured_schema(
    tmp_path: Path,
) -> None:
    (tmp_path / "arbiter").mkdir()
    (tmp_path / "arbiter-server.yaml").write_text(
        "defaults:\n"
        "  - arbiter_app_config_schema\n"
        "  - arbiter: server\n"
        "  - _self_\n",
        encoding="utf-8",
    )
    (tmp_path / "arbiter" / "server.yaml").write_text(
        "# @package arbiter\n" "server:\n" "  name: arbiter\n",
        encoding="utf-8",
    )

    cfg = compose_config(
        config_dir=tmp_path,
        config_name="arbiter-server",
        overrides=["arbiter.deployment_scope=staged"],
    )

    assert cfg.arbiter.deployment_scope == "staged"


def test_compose_config_loads_env_file_before_composing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARBITER_TEST_SERVER_NAME", raising=False)
    (tmp_path / "config.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  server:\n"
        "    name: ${oc.env:ARBITER_TEST_SERVER_NAME}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "\n"
        "# Local operator-owned environment.\n"
        'export ARBITER_TEST_SERVER_NAME="from-env-file" # comment\n',
        encoding="utf-8",
    )

    cfg = compose_config(
        config_dir=tmp_path,
        config_name="config",
    )

    assert cfg.arbiter.server.name == "from-env-file"
    assert cfg.arbiter.env_file == "local.env"


def test_load_env_file_keeps_existing_process_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARBITER_TEST_ENV_FILE_PRECEDENCE", "from-process")
    env_file = tmp_path / "local.env"
    env_file.write_text(
        'ARBITER_TEST_ENV_FILE_PRECEDENCE="from file"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["ARBITER_TEST_ENV_FILE_PRECEDENCE"] == "from-process"


def test_load_env_file_reports_invalid_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "local.env"
    env_file.write_text("not an assignment\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid env file line 1"):
        load_env_file(env_file)


def test_write_text_with_mode_replaces_from_restricted_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX file mode replacement semantics are not available")
    env_file = tmp_path / "local.env"
    env_file.write_text("OLD_SECRET=value\n", encoding="utf-8")
    env_file.chmod(0o644)
    observed: dict[str, object] = {}
    real_replace = os.replace

    def replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        observed["source_mode"] = source_path.stat().st_mode & 0o777
        observed["source_content"] = source_path.read_text(encoding="utf-8")
        observed["destination_content_before"] = destination_path.read_text(
            encoding="utf-8"
        )
        observed["destination_mode_before"] = destination_path.stat().st_mode & 0o777
        real_replace(source, destination)

    monkeypatch.setattr("arbiter_server.main.os.replace", replace)

    _write_text_with_mode(env_file, "NEW_SECRET=value\n", ENV_FILE_MODE)

    assert observed == {
        "source_mode": 0o600,
        "source_content": "NEW_SECRET=value\n",
        "destination_content_before": "OLD_SECRET=value\n",
        "destination_mode_before": 0o644,
    }
    assert env_file.read_text(encoding="utf-8") == "NEW_SECRET=value\n"
    _assert_posix_mode(env_file, 0o600)


def test_cli_serve_rejects_world_readable_config_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX permission checks are not available")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    config_file.chmod(0o644)
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    assert main(["--config-dir", str(tmp_path), "serve"]) == 1

    assert "unsafe config file permissions" in capsys.readouterr().err


def test_cli_serve_rejects_group_writable_config_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX permission checks are not available")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    config_file.chmod(0o660)
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    assert main(["--config-dir", str(tmp_path), "serve"]) == 1

    assert "unsafe config file permissions" in capsys.readouterr().err


def test_cli_serve_rejects_group_readable_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX permission checks are not available")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n" "  env_file: local.env\n" "  server:\n" "    transport: https\n",
        encoding="utf-8",
    )
    config_file.chmod(0o640)
    env_file = tmp_path / "local.env"
    env_file.write_text("", encoding="utf-8")
    env_file.chmod(0o640)
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    assert main(["--config-dir", str(tmp_path), "serve"]) == 1

    assert "unsafe app env file permissions" in capsys.readouterr().err


def test_cli_serve_rejects_world_writable_config_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX permission checks are not available")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    config_file.chmod(0o640)
    tmp_path.chmod(0o777)
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    try:
        assert main(["--config-dir", str(tmp_path), "serve"]) == 1
    finally:
        tmp_path.chmod(0o700)

    assert "unsafe config directory permissions" in capsys.readouterr().err


def test_cli_serve_rejects_group_writable_config_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX permission checks are not available")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    config_file.chmod(0o640)
    tmp_path.chmod(0o770)
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    try:
        assert main(["--config-dir", str(tmp_path), "serve"]) == 1
    finally:
        tmp_path.chmod(0o700)

    assert "unsafe config directory permissions" in capsys.readouterr().err


def test_windows_acl_reason_uses_allowlist() -> None:
    assert (
        _windows_unallowed_access_reason(
            [_WindowsAccessAce(sid="S-1-5-32-545", mask=0x00000001)],
            owner_sid="S-1-5-21-1-2-3-1001",
            access_mask=0x00000001,
        )
        == "Builtin Users (S-1-5-32-545) grants access outside the allowlist"
    )
    assert (
        _windows_unallowed_access_reason(
            [_WindowsAccessAce(sid="S-1-5-21-1-2-3-513", mask=0x40000000)],
            owner_sid="S-1-5-21-1-2-3-1001",
            access_mask=0x40000000,
        )
        == "Domain Users (S-1-5-21-1-2-3-513) grants access outside the allowlist"
    )
    assert (
        _windows_unallowed_access_reason(
            [_WindowsAccessAce(sid="S-1-5-21-1-2-3-1001", mask=0x00000001)],
            owner_sid="S-1-5-21-1-2-3-1001",
            access_mask=0x00000001,
        )
        is None
    )
    assert (
        _windows_unallowed_access_reason(
            [_WindowsAccessAce(sid="S-1-5-32-544", mask=0x00000001)],
            owner_sid="S-1-5-21-1-2-3-1001",
            access_mask=0x00000001,
        )
        is None
    )
    assert (
        _windows_unallowed_access_reason(
            [_WindowsAccessAce(sid="S-1-1-0", mask=0)],
            owner_sid="S-1-5-21-1-2-3-1001",
            access_mask=0x00000001,
        )
        is None
    )


def test_windows_icacls_remediation_uses_allowed_principals() -> None:
    message = _windows_icacls_remediation(Path("C:/arbiter/arbiter-server.yaml"))

    assert "elevated Command Prompt (cmd.exe)" in message
    assert "takeown /F" in message
    assert "icacls" in message
    assert "%USERDOMAIN%\\%USERNAME%:F" in message
    assert "*S-1-5-18:F" in message
    assert "*S-1-5-32-544:F" in message
    assert "S-1-3-4" not in message
    assert "Owner Rights" not in message


def test_windows_permissions_reject_broad_config_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "arbiter_server.file_protection.windows._windows_unallowed_permission_reason",
        lambda path, *, access_mask: (
            "Everyone (S-1-1-0) grants access outside the allowlist"
            if path == config_file
            else None
        ),
    )

    with pytest.raises(ValueError, match="unsafe config file permissions") as exc:
        ensure_windows_runtime_config_permissions(
            config_dir=tmp_path,
            env_file=None,
        )

    assert "Everyone" in str(exc.value)
    assert "icacls" in str(exc.value)


def test_windows_permissions_reject_unverified_env_file_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n" "  env_file: local.env\n" "  server:\n" "    transport: https\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text("", encoding="utf-8")

    def permission_reason(path: Path, *, access_mask: int) -> str | None:
        if path == env_file:
            raise OSError("ACL unavailable")
        return None

    monkeypatch.setattr(
        "arbiter_server.file_protection.windows._windows_unallowed_permission_reason",
        permission_reason,
    )

    with pytest.raises(ValueError, match="unsafe app env file permissions") as exc:
        ensure_windows_runtime_config_permissions(
            config_dir=tmp_path,
            env_file=env_file,
        )

    assert "could not verify Windows ACLs" in str(exc.value)
    assert "icacls" in str(exc.value)


def test_windows_real_acl_rejects_builtin_users_read(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("real ACL checks require Windows")
    sensitive_file = tmp_path / "sensitive.txt"
    sensitive_file.write_text("super-secret\n", encoding="utf-8")
    current_user = subprocess.check_output(["whoami"], text=True).strip()
    subprocess.run(
        [
            "icacls",
            str(sensitive_file),
            "/inheritance:r",
            "/grant:r",
            f"{current_user}:F",
            "/grant",
            "*S-1-5-32-545:R",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    reason = _windows_unallowed_permission_reason(
        sensitive_file, access_mask=0x00000001
    )

    assert reason is not None
    assert "Builtin Users" in reason


def test_windows_real_acl_rejects_broad_config_before_serve(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("real ACL checks require Windows")
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n  server:\n    transport: https\n", encoding="utf-8"
    )
    current_user = subprocess.check_output(["whoami"], text=True).strip()
    for path in (tmp_path, config_file):
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{current_user}:F",
                "*S-1-5-18:F",
                "*S-1-5-32-544:F",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
    subprocess.run(
        ["icacls", str(config_file), "/grant", "*S-1-5-32-545:R"],
        check=True,
        text=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        "arbiter_server.main.build_server",
        lambda _cfg, **_kwargs: pytest.fail("server should not be built"),
    )

    assert main(["--config-dir", str(tmp_path), "serve"]) == 1

    stderr = capsys.readouterr().err
    assert (
        "unsafe config file permissions" in stderr
        or "unsafe config directory permissions" in stderr
    )
    if "unsafe config file permissions" in stderr:
        assert "Builtin Users" in stderr
    else:
        assert str(tmp_path) in stderr


def test_cli_env_check_accepts_env_file_and_process_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        username: ${oc.env:SMTP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n"
        "  etc:\n"
        "    home: ${oc.env:HOME}\n",
        encoding="utf-8",
    )
    (tmp_path / "local.env").write_text(
        "SMTP_PRIMARY_ACCOUNT_USERNAME=agent@example.com\n"
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=secret\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "check"]) == 0

    assert capsys.readouterr().out == "env ok: 3 variables satisfied\n"


def test_cli_env_check_reports_missing_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )
    (tmp_path / "local.env").write_text("", encoding="utf-8")

    assert main(["--config-dir", str(tmp_path), "env", "check"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter env error: missing required environment variables:\n"
        "  SMTP_PRIMARY_ACCOUNT_PASSWORD (arbiter-smtp)\n"
    )


def test_cli_env_bootstrap_rebuilds_configured_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in (
        "IMAP_PRIMARY_ACCOUNT_PASSWORD",
        "IMAP_PRIMARY_ACCOUNT_USERNAME",
        "SMTP_PRIMARY_ACCOUNT_USERNAME",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("HOME", "/home/tester")
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    imap:\n"
        "      primary:\n"
        "        username: ${oc.env:IMAP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:IMAP_PRIMARY_ACCOUNT_PASSWORD}\n"
        "    smtp:\n"
        "      primary:\n"
        "        username: ${oc.env:SMTP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n"
        "  etc:\n"
        "    home: ${oc.env:HOME}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=keep-me\n"
        "IMAP_PRIMARY_ACCOUNT_USERNAME=imap-user\n"
        "UNRELATED=value\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    assert env_file.read_text(encoding="utf-8") == (
        "# arbiter-imap\n"
        "IMAP_PRIMARY_ACCOUNT_USERNAME=imap-user\n"
        "IMAP_PRIMARY_ACCOUNT_PASSWORD=\n"
        "\n"
        "# arbiter-smtp\n"
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=keep-me\n"
        "SMTP_PRIMARY_ACCOUNT_USERNAME=\n"
        "\n"
        "# miscellaneous\n"
        "UNRELATED=value\n"
    )
    assert capsys.readouterr().out == f"wrote {env_file}\n"


def test_cli_env_bootstrap_reports_noop_when_env_file_is_current(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "# arbiter-smtp\n" "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n",
        encoding="utf-8",
    )
    env_file.chmod(0o644)

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    _assert_posix_mode(env_file, 0o600)
    assert capsys.readouterr().out == f"env file already up to date: {env_file}\n"


def test_cli_env_bootstrap_configures_default_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        username: ${oc.env:SMTP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    assert (tmp_path / "arbiter-server.yaml").read_text(encoding="utf-8") == (
        "arbiter:\n"
        "  env_file: .env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        username: ${oc.env:SMTP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n"
    )
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "# arbiter-smtp\n"
        "SMTP_PRIMARY_ACCOUNT_USERNAME=\n"
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n"
    )
    _assert_posix_mode(tmp_path / "arbiter-server.yaml", 0o640)
    _assert_posix_mode(tmp_path / ".env", 0o600)
    assert capsys.readouterr().out == f"wrote {tmp_path / '.env'}\n"


def test_cli_deploy_docker_init_writes_local_deploy_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_installed_deploy_environment(monkeypatch)
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "compose.yaml").exists()
    compose_text = (deploy_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "pip_log=/tmp/arbiter-pip-install.log" in compose_text
    assert "run_pip_install()" in compose_text
    assert 'if [ -n "$${ARBITER_PIP_VERBOSE:-}" ]; then' in compose_text
    assert (
        '"$$venv_python" -m pip --disable-pip-version-check install "$$@"'
        in compose_text
    )
    assert 'cat "$$pip_log" >&2' in compose_text
    assert (
        "run_pip_install --no-cache-dir --find-links /wheels "
        '-e "$$local_requirement"'
    ) in compose_text
    assert "rm -rf /tmp/arbiter-source" in compose_text
    assert (
        'find /tmp/arbiter-source -type d \\( -name "*.egg-info" -o '
        '-name "__pycache__" \\) -prune -exec rm -rf {} +'
    ) in compose_text
    assert 'cp -a "$$requirement" "$$local_requirement"' in compose_text
    assert "run_pip_install --no-cache-dir -r /tmp/requirements.pinned" in compose_text
    assert (
        "pip --disable-pip-version-check install -q --no-cache-dir" not in compose_text
    )
    assert (
        'grep -Eq "^[[:space:]]*/source/arbiter(/|$)" /requirements.txt' in compose_text
    )
    assert (
        'awk "!/^[[:space:]]*(#|$)/ && '
        '!/^[[:space:]]*\\\\/source\\\\/arbiter(\\\\/|$)/ { print }" '
        "/tmp/requirements.txt > /tmp/requirements.pinned"
    ) in compose_text
    assert (
        'awk "/^[[:space:]]*\\\\/source\\\\/arbiter(\\\\/|$)/ '
        '{ sub(/^[[:space:]]*/, \\"\\"); sub(/[[:space:]]*$/, \\"\\"); print }" '
        "/tmp/requirements.txt > /tmp/requirements.editable"
    ) in compose_text
    assert "${ARBITER_WHEELS_DIR:-./wheels}:/wheels:ro" in compose_text
    assert "${ARBITER_SERVER_DATA_DIR:-./data/server}:/data/server" in compose_text
    assert "${ARBITER_PLUGIN_DATA_DIR:-./data/plugins}:/data/plugins" in compose_text
    assert "container_name: ${ARBITER_CONTAINER_NAME:-arbiter-staging}" in compose_text
    assert "user: ${ARBITER_CONTAINER_USER:-10001:10001}" in compose_text
    assert "ARBITER_SERVER_HOST: 0.0.0.0" in compose_text
    assert "ARBITER_HOST_BIND: ${ARBITER_HOST_BIND:-127.0.0.1}" in compose_text
    assert "ARBITER_HOST_PORT: ${ARBITER_HOST_PORT:-18075}" in compose_text
    assert "ARBITER_PUBLIC_SCHEME: ${ARBITER_PUBLIC_SCHEME:-https}" in compose_text
    assert "ARBITER_PUBLIC_BASE_URL: ${ARBITER_PUBLIC_BASE_URL:-}" in compose_text
    assert "ARBITER_RUNTIME_VENV: ${ARBITER_RUNTIME_VENV:-/tmp/arbiter-venv}" in (
        compose_text
    )
    assert "ARBITER_COLOR: ${ARBITER_COLOR:-}" in compose_text
    assert "ARBITER_PIP_VERBOSE: ${ARBITER_PIP_VERBOSE:-}" in compose_text
    assert 'echo "Updating Python packages..."' in compose_text
    assert "run_pip_install()" in compose_text
    assert 'case "$$runtime_venv" in /tmp/arbiter-*)' in compose_text
    assert 'case "$$runtime_venv" in *..*)' in compose_text
    assert 'case "$$HOME" in /tmp/arbiter-*)' in compose_text
    assert 'case "$$HOME" in *..*)' in compose_text
    assert 'public_host="$${ARBITER_HOST_BIND:-127.0.0.1}"' in compose_text
    assert '"arbiter.server.public.scheme=$${ARBITER_PUBLIC_SCHEME:-https}"' in (
        compose_text
    )
    assert '"arbiter.server.public.host=$$public_host"' in compose_text
    assert '"arbiter.server.public.port=$${ARBITER_HOST_PORT:-18075}"' in compose_text
    assert 'if [ -n "$${ARBITER_PUBLIC_BASE_URL:-}" ]; then' in compose_text
    assert 'python -m venv "$$runtime_venv"' in compose_text
    assert 'config check "$$@"' in compose_text
    assert 'ARBITER_CONFIG_CHECK_LIVE:-0}" = 1' in compose_text
    assert "set -- --live" in compose_text
    assert "config_check_log=/tmp/arbiter-config-check.log" in compose_text
    assert 'config check "$$@" >"$$config_check_log" 2>&1' in compose_text
    assert 'cat "$$config_check_log" >&2' in compose_text
    assert (
        'if ! "$$runtime_venv/bin/arbiter-server" --config-dir /config '
        '--config-name "$$ARBITER_CONFIG_NAME" config check "$$@"' not in compose_text
    )
    assert "ARBITER_CONTAINER_ACTION:-serve" in compose_text
    assert "ARBITER_CONFIG_OVERRIDES_FILE" in compose_text
    assert (
        "configuration check failed; Arbiter will not start until the config passes"
        in compose_text
    )
    assert (
        "run arbiter-docker config check from the deployment directory" in compose_text
    )
    assert (
        'exec "$$runtime_venv/bin/arbiter-server" --config-dir /config '
        '--config-name "$$ARBITER_CONFIG_NAME" serve "$$@"'
    ) in compose_text
    assert (
        '"${ARBITER_HOST_BIND:-127.0.0.1}:'
        '${ARBITER_HOST_PORT:-18075}:${ARBITER_CONTAINER_PORT:-8075}"'
    ) in compose_text
    assert "name: ${ARBITER_DOCKER_NETWORK_NAME:-arbiter-staging}" in compose_text
    assert (
        'com.docker.network.bridge.name: "${ARBITER_DOCKER_BRIDGE_NAME:-arbiter-stg0}"'
        in compose_text
    )
    assert 'subnet: "${ARBITER_DOCKER_SUBNET:-172.31.251.0/24}"' in compose_text
    assert "ARBITER_DEPLOYMENT_SCOPE" not in compose_text
    assert (
        '"arbiter.server.bind.host=$$ARBITER_SERVER_HOST" '
        '"arbiter.server.bind.port=$$ARBITER_CONTAINER_PORT" '
        '"arbiter.server.public.scheme=$${ARBITER_PUBLIC_SCHEME:-https}" '
        '"arbiter.server.public.host=$$public_host" '
        '"arbiter.server.public.port=$${ARBITER_HOST_PORT:-18075}" '
        '"arbiter.storage.server_data_dir=/data/server" '
        '"arbiter.storage.plugin_data_dir=/data/plugins" '
        '"arbiter.deployment_scope=staged"'
    ) in compose_text
    assert not (deploy_dir / "config.yaml").exists()
    assert (deploy_dir / "conf").is_dir()
    assert (deploy_dir / "data" / "server").is_dir()
    _assert_posix_mode(deploy_dir / "data" / "server", 0o700)
    assert (deploy_dir / "data" / "plugins").is_dir()
    _assert_posix_mode(deploy_dir / "data" / "plugins", 0o700)
    assert not (deploy_dir / "conf" / ".env").exists()
    docker_env = (deploy_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_DEPLOYMENT_SCOPE" not in docker_env
    assert "ARBITER_CONTAINER_NAME=arbiter-staging\n" in docker_env
    assert f"ARBITER_CONTAINER_USER={_default_container_user()}\n" in docker_env
    assert "ARBITER_HOST_BIND=127.0.0.1\n" in docker_env
    assert "ARBITER_HOST_PORT=18075\n" in docker_env
    assert "ARBITER_WHEELS_DIR=./wheels\n" in docker_env
    assert "ARBITER_SERVER_DATA_DIR=./data/server\n" in docker_env
    assert "ARBITER_PLUGIN_DATA_DIR=./data/plugins\n" in docker_env
    assert "ARBITER_PUBLIC_SCHEME=https\n" in docker_env
    assert "ARBITER_PUBLIC_BASE_URL=\n" in docker_env
    assert "ARBITER_DOCKER_NETWORK_NAME=arbiter-staging\n" in docker_env
    assert "ARBITER_DOCKER_BRIDGE_NAME=arbiter-stg0\n" in docker_env
    assert "ARBITER_DOCKER_SUBNET=172.31.251.0/24\n" in docker_env
    assert "ARBITER_LOCAL_SOURCE_DIR" not in docker_env
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev2\n"
        "arbiter-imap==0.9.0.dev2\n"
        "arbiter-smtp==0.9.0.dev2\n"
    )
    assert not (deploy_dir / "compose.override.yaml").exists()
    helper = deploy_dir / "arbiter-docker"
    assert helper.exists()
    _assert_posix_executable(helper)
    manifest = json.loads(
        (deploy_dir / ".arbiter-deploy.json").read_text(encoding="utf-8")
    )
    assert manifest["generator"] == "arbiter-server deploy docker"
    assert manifest["arbiter_server_version"] == "0.9.0.dev2"
    assert sorted(manifest["files"]) == [
        "arbiter-docker",
        "bundle-plugins.tsv",
        "compose.yaml",
    ]
    assert not (deploy_dir / "empty-source").exists()
    assert "Next steps:\n" in capsys.readouterr().out


def test_cli_deploy_docker_init_preserves_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    deploy_dir.mkdir()
    config_file = deploy_dir / "conf" / "arbiter-server.yaml"
    config_file.parent.mkdir()
    config_file.write_text("operator config\n", encoding="utf-8")

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )

    assert config_file.read_text(encoding="utf-8") == "operator config\n"
    assert (deploy_dir / "compose.yaml").exists()
    assert (deploy_dir / "arbiter-docker").exists()
    capsys.readouterr()


def test_cli_deploy_docker_init_refuses_existing_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    deploy_dir.mkdir()
    compose_file = deploy_dir / "compose.yaml"
    compose_file.write_text("existing\n", encoding="utf-8")

    assert (
        main(
            [
                "deploy",
                "docker",
                "init",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
            ]
        )
        == 1
    )

    assert compose_file.read_text(encoding="utf-8") == "existing\n"
    assert capsys.readouterr().err == (
        "Arbiter deploy error: refusing to overwrite existing deployment "
        f"file: {compose_file}\n"
        "  use update to refresh generated files\n"
    )


def test_cli_deploy_docker_init_accepts_multiple_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "docker.requirement=arbiter-smtp==1.2.3",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n" "arbiter-smtp==1.2.3\n"
    )
    capsys.readouterr()


def test_cli_deploy_docker_init_expands_meta_package_with_package_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==0.9.0",
                "docker.requirement=arbiter-smtp==0.9.1",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0\n" "arbiter-imap==0.9.0\n" "arbiter-smtp==0.9.1\n"
    )
    capsys.readouterr()


def test_cli_deploy_docker_init_expands_meta_package_from_suite_dependencies(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main._suite_dependency_package_names",
        lambda: ("arbiter-server", "arbiter-smtp"),
    )
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==0.9.0",
                "docker.requirement=arbiter-smtp==0.9.1",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0\n" "arbiter-smtp==0.9.1\n"
    )
    assert (deploy_dir / "bundle-plugins.tsv").read_text(encoding="utf-8") == (
        "# plugin\tpackage\tsuite\tdescription\n"
        "imap\tarbiter-imap\t\tIMAP service plugin for Arbiter\n"
        "smtp\tarbiter-smtp\tsuite\tSMTP service plugin for Arbiter\n"
    )
    capsys.readouterr()


def test_cli_deploy_docker_init_rejects_conflicting_duplicate_package_pins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-smtp==0.9.1",
                "docker.requirement=arbiter-smtp==0.9.2",
                "init",
            ]
        )
        == 2
    )

    assert capsys.readouterr().err == (
        "Arbiter deploy error: conflicting docker.requirement pins for "
        "arbiter-smtp: 0.9.1, 0.9.2\n"
    )
    assert not deploy_dir.exists()


def test_cli_deploy_docker_init_rejects_unpinned_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite",
                "init",
            ]
        )
        == 2
    )

    assert capsys.readouterr().err == (
        "Arbiter deploy error: docker.requirement must be an exact "
        "package pin (name==version) or an absolute container path\n"
        "  value: arbiter-suite\n"
    )
    assert not deploy_dir.exists()


def test_cli_deploy_docker_init_uses_installed_default_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_installed_deploy_environment(
        monkeypatch,
        server_version="0.9.0.dev2",
        plugins=(
            ("custom", "arbiter-custom", "0.9.0.dev2"),
            ("smtp", "arbiter-smtp", "0.9.0.dev2"),
        ),
    )
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 0

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev2\n"
        "arbiter-custom==0.9.0.dev2\n"
        "arbiter-smtp==0.9.0.dev2\n"
    )
    assert not (deploy_dir / "compose.override.yaml").exists()
    capsys.readouterr()


def test_cli_deploy_docker_init_builds_local_installed_wheels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_roots: dict[str, Path] = {}
    for distribution_name in ("arbiter-server", "arbiter-smtp"):
        source_root = tmp_path / "src" / distribution_name
        source_root.mkdir(parents=True)
        (source_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        source_roots[distribution_name] = source_root

    def fake_build_local_source_wheel(source_root: Path, wheel_dir: Path) -> Path:
        wheel_dir.mkdir(parents=True, exist_ok=True)
        wheel_name = {
            source_roots[
                "arbiter-server"
            ]: "arbiter_server-0.9.0.dev2-py3-none-any.whl",
            source_roots["arbiter-smtp"]: "arbiter_smtp-0.9.0.dev2-py3-none-any.whl",
        }[source_root]
        wheel_path = wheel_dir / wheel_name
        wheel_path.write_text("wheel\n", encoding="utf-8")
        return wheel_path

    _patch_installed_deploy_environment(
        monkeypatch,
        plugins=(("smtp", "arbiter-smtp", "0.9.0.dev2"),),
        local_sources=source_roots,
    )
    monkeypatch.setattr(
        "arbiter_server.main._build_local_source_wheel",
        fake_build_local_source_wheel,
    )
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 0

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev2\n" "arbiter-smtp==0.9.0.dev2\n"
    )
    assert sorted(path.name for path in (deploy_dir / "wheels").glob("*.whl")) == [
        "arbiter_server-0.9.0.dev2-py3-none-any.whl",
        "arbiter_smtp-0.9.0.dev2-py3-none-any.whl",
    ]
    assert not (deploy_dir / "compose.override.yaml").exists()
    capsys.readouterr()


def test_cli_deploy_docker_update_force_refreshes_explicit_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.0.0",
                "update",
                "--force",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.0.0\n"
    )
    assert f"force updating requirements file: {deploy_dir / 'requirements.txt'}\n" in (
        capsys.readouterr().out
    )


def test_cli_deploy_docker_update_force_refreshes_installed_local_package_pins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_roots: dict[str, Path] = {}
    for distribution_name in ("arbiter-server", "arbiter-smtp"):
        source_root = tmp_path / "src" / distribution_name
        source_root.mkdir(parents=True)
        (source_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        source_roots[distribution_name] = source_root
    wheel_versions = {
        source_roots["arbiter-server"]: "0.9.0.dev2",
        source_roots["arbiter-smtp"]: "0.9.0.dev2",
    }

    def fake_build_local_source_wheel(source_root: Path, wheel_dir: Path) -> Path:
        wheel_dir.mkdir(parents=True, exist_ok=True)
        distribution_slug = {
            source_roots["arbiter-server"]: "arbiter_server",
            source_roots["arbiter-smtp"]: "arbiter_smtp",
        }[source_root]
        wheel_path = (
            wheel_dir
            / f"{distribution_slug}-{wheel_versions[source_root]}-py3-none-any.whl"
        )
        wheel_path.write_text("wheel\n", encoding="utf-8")
        return wheel_path

    _patch_installed_deploy_environment(
        monkeypatch,
        server_version="0.9.0.dev2",
        plugins=(("smtp", "arbiter-smtp", "0.9.0.dev2"),),
        local_sources=source_roots,
    )
    monkeypatch.setattr(
        "arbiter_server.main._build_local_source_wheel",
        fake_build_local_source_wheel,
    )
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 0
    capsys.readouterr()
    wheel_versions[source_roots["arbiter-server"]] = "0.9.0.dev3"
    wheel_versions[source_roots["arbiter-smtp"]] = "0.9.0.dev3"
    _patch_installed_deploy_environment(
        monkeypatch,
        server_version="0.9.0.dev3",
        plugins=(("smtp", "arbiter-smtp", "0.9.0.dev3"),),
        local_sources=source_roots,
    )

    assert (
        main(["deploy", "docker", f"docker.dir={deploy_dir}", "update", "--force"]) == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev3\n" "arbiter-smtp==0.9.0.dev3\n"
    )
    assert sorted(path.name for path in (deploy_dir / "wheels").glob("*.whl")) == [
        "arbiter_server-0.9.0.dev2-py3-none-any.whl",
        "arbiter_server-0.9.0.dev3-py3-none-any.whl",
        "arbiter_smtp-0.9.0.dev2-py3-none-any.whl",
        "arbiter_smtp-0.9.0.dev3-py3-none-any.whl",
    ]
    assert f"force updating requirements file: {deploy_dir / 'requirements.txt'}\n" in (
        capsys.readouterr().out
    )


def test_build_local_source_wheel_returns_wheel_from_current_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    (wheel_dir / "stale-9.9.9-py3-none-any.whl").write_text(
        "stale\n",
        encoding="utf-8",
    )

    def fake_run(
        args: Sequence[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> SimpleNamespace:
        assert check is False
        assert text is True
        assert capture_output is True
        assert "--no-build-isolation" in args
        temporary_wheel_dir = Path(args[args.index("--wheel-dir") + 1])
        (temporary_wheel_dir / "current-1.2.3-py3-none-any.whl").write_text(
            "current\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("arbiter_server.main.subprocess.run", fake_run)

    wheel = _build_local_source_wheel(source_root, wheel_dir)

    assert wheel is not None
    assert wheel == wheel_dir / "current-1.2.3-py3-none-any.whl"
    assert wheel.read_text(encoding="utf-8") == "current\n"
    assert (wheel_dir / "stale-9.9.9-py3-none-any.whl").exists()


def test_cli_deploy_docker_init_rejects_unknown_installed_default_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbiter_server.main.arbiter_server_version", lambda: "unknown")
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 2

    assert capsys.readouterr().err == (
        "Arbiter deploy error: cannot infer default docker requirements\n"
        "  install Arbiter packages in the current Python environment so "
        "the generator can pin them\n"
        "  or pass docker.requirement=arbiter-suite==VERSION for the all-in-one "
        "meta package\n"
        "  or pass one or more docker.requirement=PACKAGE==VERSION entries "
        "for another meta package or explicit packages\n"
        "  for local checkout testing, pass absolute container source paths\n"
    )
    assert not deploy_dir.exists()


def test_cli_deploy_docker_init_accepts_absolute_path_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/source/arbiter/server",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/arbiter/server\n"
    )
    capsys.readouterr()


def test_cli_deploy_docker_init_accepts_wheelhouse_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/wheels/arbiter_server-1.2.3-py3-none-any.whl",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/wheels/arbiter_server-1.2.3-py3-none-any.whl\n"
    )
    compose_text = (deploy_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "--no-index --find-links /wheels -r /requirements.txt" in compose_text
    assert (deploy_dir / "wheels").is_dir()
    capsys.readouterr()


def test_cli_deploy_docker_generated_helper_bundle_lists_root_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "docker.requirement=arbiter-smtp==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "list"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        "root\tarbiter-server==1.2.3\n" "root\tarbiter-smtp==1.2.3\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_lists_supported_plugins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "list-plugins"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        "imap\tIMAP service plugin for Arbiter\n"
        "smtp\tSMTP service plugin for Arbiter\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_adds_and_removes_plugin_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    add_imap = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add", "imap"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    add_smtp = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add", "smtp"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    remove_smtp = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "remove", "smtp"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert add_imap.returncode == 0
    assert add_smtp.returncode == 0
    assert remove_smtp.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n" "arbiter-imap==1.2.3\n"
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "list"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        "root\tarbiter-server==1.2.3\n" "root\tarbiter-imap==1.2.3\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_adds_external_plugins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    wheel = tmp_path / "dist" / "acme_mail-2.0.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_text("wheel\n", encoding="utf-8")
    source_dir = tmp_path / "acme-source"
    source_dir.mkdir()
    (source_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env sh\n"
        "wheel_dir=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--wheel-dir" ]; then shift; wheel_dir="$1"; fi\n'
        "  shift || true\n"
        "done\n"
        'mkdir -p "$wheel_dir"\n'
        "printf 'built\\n' > \"$wheel_dir/acme_source-2.0.0-py3-none-any.whl\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    add_package = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add-package", "acme-mail==2.0.0"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    add_wheel = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add-wheel", str(wheel)],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    add_source = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add-source", str(source_dir)],
        check=False,
        cwd=tmp_path,
        env={**os.environ, "ARBITER_PYTHON": str(fake_python)},
        text=True,
        capture_output=True,
    )

    assert add_package.returncode == 0
    assert add_wheel.returncode == 0
    assert add_source.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n"
        "acme-mail==2.0.0\n"
        "/wheels/acme_mail-2.0.0-py3-none-any.whl\n"
        "/wheels/acme_source-2.0.0-py3-none-any.whl\n"
    )
    assert (deploy_dir / "wheels" / wheel.name).read_text(encoding="utf-8") == "wheel\n"
    assert (deploy_dir / "wheels" / "acme_source-2.0.0-py3-none-any.whl").read_text(
        encoding="utf-8"
    ) == "built\n"


def test_cli_deploy_docker_generated_helper_bundle_adds_and_removes_meta_package_plugins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    add_suite = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "add", "arbiter-suite"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert add_suite.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n" "arbiter-imap==1.2.3\n" "arbiter-smtp==1.2.3\n"
    )

    remove_suite = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "remove", "arbiter-suite"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert remove_suite.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_remove_expands_suite_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "remove", "smtp"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n" "arbiter-imap==1.2.3\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_remove_meta_package_from_suite_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "remove", "arbiter-suite"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.3\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_lists_all_wheelhouse_packages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/wheels/arbiter_server-1.2.3-py3-none-any.whl",
                "docker.requirement=arbiter-smtp==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    wheels_dir = deploy_dir / "wheels"
    for wheel_name in (
        "arbiter_server-1.2.3-py3-none-any.whl",
        "arbiter_smtp-1.2.3-py3-none-any.whl",
        "antlr4_python3_runtime-4.9.3-py3-none-any.whl",
        "hydra_core-1.3.2-py3-none-any.whl",
    ):
        (wheels_dir / wheel_name).write_text("wheel\n", encoding="utf-8")

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "list", "all"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        "root\tarbiter-server==1.2.3\n"
        "root\tarbiter-smtp==1.2.3\n"
        "transitive\tantlr4-python3-runtime==4.9.3\n"
        "transitive\thydra-core==1.3.2\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_list_all_rejects_empty_wheelhouse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "list", "all"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    wheels_dir = deploy_dir / "wheels"
    assert f"error: wheelhouse is empty: {wheels_dir}\n" in result.stderr
    assert (
        f"run {deploy_dir / 'arbiter-docker'} bundle prepare to build "
        "the dependency wheelhouse\n"
    ) in result.stderr


def test_cli_deploy_docker_generated_helper_bundle_upgrade_updates_roots_and_prepares(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.0.0",
                "docker.requirement=arbiter-smtp==1.0.0",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "conf" / "arbiter-server.yaml").write_text(
        "arbiter: {}\n",
        encoding="utf-8",
    )
    (deploy_dir / "conf" / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    upgrade_input = tmp_path / "upgrade-input"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        f'    cp "$work/requirements.in" "{upgrade_input}"\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"1.2.0"}},'
        '{"metadata":{"name":"arbiter-smtp","version":"1.1.0"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==1.2.0\\narbiter-smtp==1.1.0\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["TERM"] = "dumb"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "upgrade"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.2.0\n" "arbiter-smtp==1.1.0\n"
    )
    assert upgrade_input.read_text(encoding="utf-8") == (
        "arbiter-server>=1.0.0\n" "arbiter-smtp>=1.0.0\n"
    )
    assert result.stdout == (
        f"bundle upgrade complete: {deploy_dir}\n"
        "root:\n"
        "  arbiter-server 1.0.0 -> 1.2.0\n"
        "  arbiter-smtp 1.0.0 -> 1.1.0\n"
        "transitive:\n"
        "  no changes\n"
    )
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert f"-v {deploy_dir / 'wheels'}:/wheels:ro" in docker_call_text
    assert (
        "--dry-run --ignore-installed --find-links /wheels "
        "--report /work/report.json"
    ) in docker_call_text
    assert "--find-links /wheels --wheel-dir /wheelhouse -r /requirements.txt" in (
        docker_call_text
    )


def test_cli_deploy_docker_generated_helper_bundle_upgrade_builds_repo_wheels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for project_dir in ("server", "plugins/imap", "plugins/smtp"):
        source_dir = tmp_path / project_dir
        source_dir.mkdir(parents=True)
        (source_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.0.0",
                "docker.requirement=arbiter-smtp==1.0.0",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "conf" / "arbiter-server.yaml").write_text(
        "arbiter: {}\n",
        encoding="utf-8",
    )
    (deploy_dir / "conf" / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    python_calls = tmp_path / "python-calls"
    upgrade_input = tmp_path / "upgrade-input"
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{python_calls}"\n'
        'wheel_dir=""\n'
        'last=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--wheel-dir" ]; then\n'
        "    shift\n"
        '    wheel_dir="$1"\n'
        "  fi\n"
        '  last="$1"\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$wheel_dir"\n'
        'case "$last" in\n'
        '  */server) touch "$wheel_dir/arbiter_server-1.2.0-py3-none-any.whl" ;;\n'
        '  */smtp) touch "$wheel_dir/arbiter_smtp-1.1.0-py3-none-any.whl" ;;\n'
        "  *) exit 3 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (venv_bin / "python").chmod(0o755)
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        f'    cp "$work/requirements.in" "{upgrade_input}"\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"1.2.0"}},'
        '{"metadata":{"name":"arbiter-smtp","version":"1.1.0"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==1.2.0\\narbiter-smtp==1.1.0\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "upgrade"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    python_call_text = python_calls.read_text(encoding="utf-8")
    assert "--no-build-isolation" in python_call_text
    assert f"{tmp_path / 'server'}" in python_call_text
    assert f"{tmp_path / 'plugins' / 'smtp'}" in python_call_text
    assert upgrade_input.read_text(encoding="utf-8") == (
        "arbiter-server>=1.0.0\n" "arbiter-smtp>=1.0.0\n"
    )
    assert f"-v {deploy_dir / 'wheels'}:/wheels:ro" in docker_calls.read_text(
        encoding="utf-8"
    )


def test_cli_deploy_docker_generated_helper_bundle_upgrade_pypi_only_skips_repo_wheels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for project_dir in ("server", "plugins/imap", "plugins/smtp"):
        source_dir = tmp_path / project_dir
        source_dir.mkdir(parents=True)
        (source_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.0.0",
                "docker.requirement=arbiter-smtp==1.0.0",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    python_calls = tmp_path / "python-calls"
    upgrade_input = tmp_path / "upgrade-input"
    (fake_bin / "python").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{python_calls}"\n' "exit 13\n",
        encoding="utf-8",
    )
    (fake_bin / "python").chmod(0o755)
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        f'    cp "$work/requirements.in" "{upgrade_input}"\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"1.2.0"}},'
        '{"metadata":{"name":"arbiter-smtp","version":"1.1.0"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==1.2.0\\narbiter-smtp==1.1.0\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "upgrade", "--pypi-only"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert not python_calls.exists()
    assert upgrade_input.read_text(encoding="utf-8") == (
        "arbiter-server>=1.0.0\n" "arbiter-smtp>=1.0.0\n"
    )
    resolve_call = next(
        line
        for line in docker_calls.read_text(encoding="utf-8").splitlines()
        if "--report /work/report.json" in line
    )
    assert "--find-links /wheels" not in resolve_call
    assert f"-v {deploy_dir / 'wheels'}:/wheels:ro" not in resolve_call


def test_cli_deploy_docker_generated_helper_bundle_upgrade_package_keeps_lower_bound(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==1.0.0",
                "docker.requirement=arbiter-smtp==1.0.0",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    upgrade_input = tmp_path / "upgrade-input"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        f'    cp "$work/requirements.in" "{upgrade_input}"\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"1.0.0"}},'
        '{"metadata":{"name":"arbiter-smtp","version":"1.1.0"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==1.0.0\\narbiter-smtp==1.1.0\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "upgrade", "arbiter-smtp"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert upgrade_input.read_text(encoding="utf-8") == (
        "arbiter-server==1.0.0\n" "arbiter-smtp>=1.0.0\n"
    )
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==1.0.0\n" "arbiter-smtp==1.1.0\n"
    )


def test_cli_deploy_docker_generated_helper_bundle_upgrade_refreshes_wheel_roots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/wheels/arbiter_server-1.2.3-py3-none-any.whl",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "upgrade"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        f"bundle upgrade complete: {deploy_dir}\n"
        "root:\n"
        "  no changes\n"
        "transitive:\n"
        "  no changes\n"
    )
    docker_call_lines = [
        line
        for line in docker_calls.read_text(encoding="utf-8").splitlines()
        if line == "info" or line.startswith("run --rm")
    ]
    assert len(docker_call_lines) == 7
    assert docker_call_lines[0] == "info"
    assert docker_call_lines[2] == "info"
    assert (
        "--dry-run --ignore-installed --no-index --find-links /wheels"
        in docker_call_lines[3]
    )
    assert "--report /work/report.json" in docker_call_lines[3]
    assert " python -c " in docker_call_lines[4]
    assert docker_call_lines[5] == "info"


def test_cli_deploy_docker_generated_helper_bundle_check_validates_without_prepare(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "check"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_user = _default_container_user()
    assert docker_calls.read_text(encoding="utf-8") == (
        "info\n"
        f"run --rm --user {docker_user} "
        f"-v {deploy_dir / 'requirements.txt'}:/requirements.txt:ro "
        f"-v {deploy_dir / 'wheels'}:/wheels:ro "
        "python:3.11-slim python -m pip --disable-pip-version-check "
        "install --no-cache-dir "
        "--target /tmp/arbiter-wheelhouse-check --no-index --find-links /wheels "
        "-r /requirements.txt\n"
    )
    assert result.stdout == f"bundle check passed: {deploy_dir}\n"


def test_cli_deploy_docker_generated_helper_bundle_prepare_builds_wheelhouse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    stale_wheel = deploy_dir / "wheels" / "stale-9.9.9-py3-none-any.whl"
    stale_wheel.write_text("stale\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'wheelhouse=""\n'
        'wheels=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/wheelhouse) wheelhouse="${arg%:/wheelhouse}" ;;\n'
        '    *:/wheels) wheels="${arg%:/wheels}" ;;\n'
        '    *:/wheels:ro) wheels="${arg%:/wheels:ro}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *" pip --disable-pip-version-check wheel "*)\n'
        '    printf "wheel\\n" > "$wheelhouse/arbiter_suite-1.2.3-py3-none-any.whl"\n'
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    rm -f "$wheels/stale-9.9.9-py3-none-any.whl"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "prepare"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_user = _default_container_user()
    docker_call_lines = [
        line
        for line in docker_calls.read_text(encoding="utf-8").splitlines()
        if line == "info" or line.startswith("run --rm")
    ]
    assert len(docker_call_lines) == 7
    assert docker_call_lines[0] == "info"
    assert docker_call_lines[1].startswith(
        f"run --rm --user {docker_user} "
        f"-v {deploy_dir / 'requirements.txt'}:/requirements.txt:ro "
        f"-v {deploy_dir / 'wheels'}:/wheels:ro "
        "-v "
    )
    assert "arbiter-wheelhouse." in docker_call_lines[1]
    assert docker_call_lines[1].endswith(
        ":/wheelhouse python:3.11-slim python -m pip "
        "--disable-pip-version-check wheel --no-cache-dir "
        "--find-links /wheels --wheel-dir /wheelhouse -r /requirements.txt"
    )
    assert docker_call_lines[2] == "info"
    assert (
        "--dry-run --ignore-installed --no-index --find-links /wheels"
        in docker_call_lines[3]
    )
    assert "--report /work/report.json" in docker_call_lines[3]
    assert " python -c " in docker_call_lines[4]
    assert docker_call_lines[5] == "info"
    assert docker_call_lines[6] == (
        f"run --rm --user {docker_user} "
        f"-v {deploy_dir / 'requirements.txt'}:/requirements.txt:ro "
        f"-v {deploy_dir / 'wheels'}:/wheels:ro "
        "python:3.11-slim python -m pip --disable-pip-version-check "
        "install --no-cache-dir "
        "--target /tmp/arbiter-wheelhouse-check --no-index --find-links /wheels "
        "-r /requirements.txt"
    )
    assert result.stdout == (
        "preparing bundle: arbiter-suite==1.2.3\n"
        f"bundle prepare complete: {deploy_dir / 'wheels'}\n"
    )
    assert not stale_wheel.exists()
    assert (deploy_dir / "wheels" / "arbiter_suite-1.2.3-py3-none-any.whl").exists()


def test_cli_deploy_docker_generated_helper_prepare_refreshes_repo_wheels(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    deploy_dir = repo_dir / "arbiter-docker"
    for source_dir in ("server", "plugins/imap", "plugins/smtp"):
        package_dir = repo_dir / source_dir
        package_dir.mkdir(parents=True)
        (package_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "docker.requirement=arbiter-smtp==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    wheels_dir = deploy_dir / "wheels"
    (wheels_dir / "arbiter_server-0.9.0.dev2-py3-none-any.whl").write_text(
        "stale server\n",
        encoding="utf-8",
    )
    (wheels_dir / "arbiter_smtp-0.9.0.dev2-py3-none-any.whl").write_text(
        "stale smtp\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python_calls = tmp_path / "python-calls"
    (fake_bin / "python").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{python_calls}"\n'
        'wheel_dir=""\n'
        'source_dir=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--wheel-dir" ]; then shift; wheel_dir="$1"; fi\n'
        '  source_dir="$1"\n'
        "  shift\n"
        "done\n"
        'case "$source_dir" in\n'
        '  */server) printf "fresh server\\n" > "$wheel_dir/arbiter_server-0.9.0.dev2-py3-none-any.whl" ;;\n'
        '  */smtp) printf "fresh smtp\\n" > "$wheel_dir/arbiter_smtp-0.9.0.dev2-py3-none-any.whl" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "python").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "prepare"],
        check=False,
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (wheels_dir / "arbiter_server-0.9.0.dev2-py3-none-any.whl").read_text(
        encoding="utf-8"
    ) == "fresh server\n"
    assert (wheels_dir / "arbiter_smtp-0.9.0.dev2-py3-none-any.whl").read_text(
        encoding="utf-8"
    ) == "fresh smtp\n"
    assert " pip --disable-pip-version-check wheel " in python_calls.read_text(
        encoding="utf-8"
    )
    assert result.stdout == (
        "preparing bundle: arbiter-server==0.9.0.dev2 arbiter-smtp==0.9.0.dev2\n"
        f"bundle prepare complete: {wheels_dir}\n"
    )


def test_cli_deploy_docker_generated_helper_prepare_uses_env_repo_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    deploy_dir = tmp_path / "scratch" / "arbiter-docker"
    package_dir = repo_dir / "server"
    package_dir.mkdir(parents=True)
    (repo_dir / "plugins").mkdir()
    (package_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    wheels_dir = deploy_dir / "wheels"
    (wheels_dir / "arbiter_server-0.9.0.dev2-py3-none-any.whl").write_text(
        "stale server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python_calls = tmp_path / "python-calls"
    (fake_bin / "python").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{python_calls}"\n'
        'wheel_dir=""\n'
        'source_dir=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--wheel-dir" ]; then shift; wheel_dir="$1"; fi\n'
        '  source_dir="$1"\n'
        "  shift\n"
        "done\n"
        'case "$source_dir" in\n'
        '  */server) printf "fresh server\\n" > "$wheel_dir/arbiter_server-0.9.0.dev2-py3-none-any.whl" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "python").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_REPO_ROOT"] = str(repo_dir)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", "prepare"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (wheels_dir / "arbiter_server-0.9.0.dev2-py3-none-any.whl").read_text(
        encoding="utf-8"
    ) == "fresh server\n"
    assert " pip --disable-pip-version-check wheel " in python_calls.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("bundle_command", ["prepare", "check"])
def test_cli_deploy_docker_generated_helper_rejects_local_source_requirements_for_wheelhouse_commands(
    bundle_command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "requirements.txt").write_text(
        "/source/arbiter/server\n"
        "/source/arbiter/plugins/imap\n"
        "/source/arbiter/plugins/smtp\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "bundle", bundle_command],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert (
        f"error: bundle {bundle_command} does not support local checkout requirements: "
        f"{deploy_dir / 'requirements.txt'}\n"
    ) in result.stderr
    assert (
        "/source/arbiter/... requirements are installed by Docker Compose "
        "at container startup\n"
    ) in result.stderr
    assert (
        f"run {deploy_dir / 'arbiter-docker'} restart to recreate staging "
        "with the local source mount\n"
    ) in result.stderr
    assert "Invalid requirement" not in result.stderr


def test_cli_deploy_docker_generated_helper_prepare_pypi_only_resolves_index_pins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "docker.requirement=arbiter-smtp==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    resolve_input = tmp_path / "resolve-input"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        f'    cp "$work/requirements.in" "{resolve_input}"\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"0.9.0.dev1"}},'
        '{"metadata":{"name":"arbiter-smtp","version":"0.9.0.dev1"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==0.9.0.dev1\\narbiter-smtp==0.9.0.dev1\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "prepare", "--pypi-only"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_user = _default_container_user()
    assert resolve_input.read_text(encoding="utf-8") == (
        "arbiter-server\n" "arbiter-smtp\n"
    )
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev1\n" "arbiter-smtp==0.9.0.dev1\n"
    )
    docker_call_lines = [
        line
        for line in docker_calls.read_text(encoding="utf-8").splitlines()
        if line == "info" or line.startswith("run --rm")
    ]
    assert len(docker_call_lines) == 10
    assert docker_call_lines[0] == "info"
    assert "--report /work/report.json" in docker_call_lines[1]
    assert "--pre" in docker_call_lines[1]
    assert f"-v {deploy_dir / 'wheels'}:/wheels:ro" not in docker_call_lines[1]
    assert "--find-links /wheels" not in docker_call_lines[1]
    assert " python -c " in docker_call_lines[2]
    assert docker_call_lines[3] == "info"
    assert docker_call_lines[4].startswith(f"run --rm --user {docker_user} " "-v ")
    assert "arbiter-pypi-prepare-transaction." in docker_call_lines[4]
    assert ":/requirements.txt:ro -v " in docker_call_lines[4]
    assert "arbiter-wheelhouse." in docker_call_lines[4]
    assert docker_call_lines[4].endswith(
        ":/wheelhouse python:3.11-slim python -m pip "
        "--disable-pip-version-check wheel --no-cache-dir "
        "--wheel-dir /wheelhouse -r /requirements.txt"
    )
    assert f"-v {deploy_dir / 'wheels'}:/wheels:ro" not in docker_call_lines[4]
    assert "--find-links /wheels" not in docker_call_lines[4]
    assert docker_call_lines[5] == "info"
    assert (
        "--dry-run --ignore-installed --no-index --find-links /wheels"
        in docker_call_lines[6]
    )
    assert "--report /work/report.json" in docker_call_lines[6]
    assert " python -c " in docker_call_lines[7]
    assert docker_call_lines[8] == "info"
    assert docker_call_lines[9].startswith(f"run --rm --user {docker_user} " "-v ")
    assert "arbiter-pypi-prepare-transaction." in docker_call_lines[9]
    assert ":/requirements.txt:ro -v " in docker_call_lines[9]
    assert docker_call_lines[9].endswith(
        "/wheels:/wheels:ro python:3.11-slim python -m pip "
        "--disable-pip-version-check install --no-cache-dir "
        "--target /tmp/arbiter-wheelhouse-check --no-index --find-links /wheels "
        "-r /requirements.txt"
    )
    assert "prepared dependency wheelhouse" not in result.stdout
    assert "validated dependency wheelhouse" not in result.stdout
    assert (
        "preparing bundle: arbiter-server==0.9.0.dev2 " "arbiter-smtp==0.9.0.dev2\n"
    ) in result.stdout
    assert f"bundle prepare complete: {deploy_dir / 'wheels'}\n" in result.stdout


def test_cli_deploy_docker_generated_helper_prepare_pypi_only_is_transactional(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    old_wheel = deploy_dir / "wheels" / "old.whl"
    old_wheel.write_text("old wheel\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        'work=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    *:/work) work="${arg%:/work}" ;;\n'
        "  esac\n"
        "done\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        "    cat > \"$work/report.json\" <<'JSON'\n"
        '{"install":[{"metadata":{"name":"arbiter-server","version":"0.9.0.dev1"}}]}\n'
        "JSON\n"
        "    exit 0\n"
        "    ;;\n"
        '  *" python -c "*)\n'
        '    printf "arbiter-server==0.9.0.dev1\\n" > "$work/requirements.out"\n'
        "    exit 0\n"
        "    ;;\n"
        '  *" pip --disable-pip-version-check wheel "*)\n'
        '    printf "wheel build failed\\n" >&2\n'
        "    exit 7\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "prepare", "--pypi-only"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev2\n"
    )
    assert old_wheel.read_text(encoding="utf-8") == "old wheel\n"


def test_cli_deploy_docker_generated_helper_prepare_pypi_only_reports_prepare_resolve_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-server==0.9.0.dev2",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        'case "$*" in\n'
        '  *"--report /work/report.json"*)\n'
        '    printf "resolve failed\\n" >&2\n'
        "    exit 7\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "prepare", "--pypi-only"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "error: failed to resolve package-index preparation" in result.stderr


def test_cli_deploy_docker_generated_helper_prepare_pypi_only_rejects_wheel_roots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "requirements.txt").write_text(
        "/wheels/arbiter_server-1.2.3-py3-none-any.whl\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "prepare", "--pypi-only"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "error: prepare --pypi-only requires package pins, "
        "but requirements.txt contains absolute paths"
    ) in result.stderr


def test_cli_deploy_docker_generated_helper_up_reports_docker_permission_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then\n'
        "  printf 'permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 9\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["TERM"] = "dumb"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert docker_calls.read_text(encoding="utf-8") == "info\n"
    assert "error: Docker daemon is not accessible by user" in result.stderr
    assert "sudo usermod -aG docker" in result.stderr
    assert "log out and back in" in result.stderr
    assert "docker said: permission denied" in result.stderr


def test_cli_deploy_docker_generated_helper_up_creates_plugin_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    shutil.rmtree(deploy_dir / "data")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then\n'
        f'  printf "ARBITER_PIP_VERBOSE=%s\\n" "${{ARBITER_PIP_VERBOSE:-}}" >> "{docker_calls}"\n'
        '  case " $* " in *" run --rm --no-deps "*) printf "server: pass\\n";; esac\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (deploy_dir / "data" / "plugins").is_dir()
    _assert_posix_mode(deploy_dir / "data" / "plugins", 0o700)
    assert "compose --env-file" in docker_calls.read_text(encoding="utf-8")


def test_cli_deploy_docker_generated_helper_up_rejects_unwritable_plugin_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            f"ARBITER_CONTAINER_USER={_default_container_user()}\n",
            "ARBITER_CONTAINER_USER=12345:12345\n",
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    fake_stat = fake_bin / "stat"
    fake_stat.write_text(
        "#!/usr/bin/env sh\n"
        'case "$*" in\n'
        f'  *"{deploy_dir / "data/server"}"*)\n'
        '    if [ "$1" = -c ] && [ "$2" = "%u %g %a" ]; then '
        'printf "12345 12345 700\\n"; exit 0; fi\n'
        "    ;;\n"
        "esac\n"
        'exec /usr/bin/stat "$@"\n',
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "error: plugin data directory is not writable by container user: 12345:12345"
        in result.stderr
    )
    assert not docker_calls.exists()


def test_cli_deploy_docker_generated_helper_up_prints_url(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_HOST_BIND=127.0.0.1\n", "ARBITER_HOST_BIND=0.0.0.0\n"
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then\n'
        f'  printf "ARBITER_PIP_VERBOSE=%s\\n" "${{ARBITER_PIP_VERBOSE:-}}" >> "{docker_calls}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        " ✔ Staging port: 8075 -> 18075 to prevent collision\n"
        " ✔ URL: https://127.0.0.1:18075\n"
    )
    assert result.stderr == ""

    color_env = {**env, "ARBITER_COLOR": "always"}
    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=color_env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        " \033[32m✔\033[0m Staging port: 8075 -> 18075 to prevent collision\n"
        " \033[32m✔\033[0m URL: "
        "\033[94mhttps://127.0.0.1:18075\033[0m\n"
    )
    assert result.stderr == ""

    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_HOST_PORT=18075\n",
            "ARBITER_HOST_PORT=8075\n",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ URL: https://127.0.0.1:8075\n"
    assert result.stderr == ""

    docker_calls.write_text("", encoding="utf-8")
    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up", "--verbose"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ URL: https://127.0.0.1:8075\n"
    assert result.stderr == ""
    assert "ARBITER_PIP_VERBOSE=1\n" in docker_calls.read_text(encoding="utf-8")

    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "info\n" in docker_call_text
    assert "inspect arbiter-staging --format" in docker_call_text
    assert "compose --env-file" in docker_call_text
    assert "up -d\n" in docker_call_text


def test_cli_deploy_docker_generated_helper_test_probes_https_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_calls = tmp_path / "curl-calls"
    fake_curl = fake_bin / "curl"
    fake_curl_count = tmp_path / "curl-count"
    fake_curl.write_text(
        "#!/usr/bin/env sh\n"
        "last_arg=\n"
        'for arg do last_arg="$arg"; done\n'
        f'printf "%s\\n" "$last_arg" >> "{curl_calls}"\n'
        "count=0\n"
        f'if [ -f "{fake_curl_count}" ]; then count="$(cat "{fake_curl_count}")"; fi\n'
        f'printf "%s\\n" "$((count + 1))" > "{fake_curl_count}"\n'
        'if [ "$count" -lt "${ARBITER_TEST_CONNECT_FAILURES:-0}" ]; then\n'
        "  printf 'curl: could not connect\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        'if [ "$count" -lt "${ARBITER_TEST_TRANSIENT_FAILURES:-0}" ]; then\n'
        "  printf 'transient server startup error\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        'exit "${ARBITER_TEST_STATUS:-0}"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ Server test: https://127.0.0.1:18075\n"
    assert result.stderr == ""
    assert curl_calls.read_text(encoding="utf-8") == (
        "https://127.0.0.1:18075/_health_\n"
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env={**env, "ARBITER_TEST_CONNECT_FAILURES": "2"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ Server test: https://127.0.0.1:18075\n"
    assert result.stderr == ""
    assert fake_curl_count.read_text(encoding="utf-8") == "3\n"

    fake_curl_count.write_text("0\n", encoding="utf-8")
    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env={**env, "ARBITER_TEST_TRANSIENT_FAILURES": "2"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ Server test: https://127.0.0.1:18075\n"
    assert result.stderr == ""
    assert fake_curl_count.read_text(encoding="utf-8") == "3\n"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env={
            **env,
            "ARBITER_COLOR": "always",
            "ARBITER_TEST_STATUS": "7",
            "ARBITER_TEST_TIMEOUT": "0",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout == (
        " \033[31m✘\033[0m Server test: " "\033[94mhttps://127.0.0.1:18075\033[0m\n"
    )
    assert result.stderr == ""


def test_cli_deploy_docker_generated_helper_config_check_uses_one_shot_container(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = compose ]; then\n'
        f'  printf "ARBITER_PIP_VERBOSE=%s\\n" "${{ARBITER_PIP_VERBOSE:-}}" >> "{docker_calls}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "info\n" in docker_call_text
    assert (
        f"compose --env-file {deploy_dir / 'docker.env'} "
        f"-f {deploy_dir / 'compose.yaml'} --progress quiet run --rm --no-deps "
    ) in docker_call_text
    assert "-e ARBITER_CONTAINER_ACTION=config-check " in docker_call_text
    assert (
        "-e ARBITER_CONFIG_OVERRIDES_FILE=/tmp/arbiter-config-overrides "
        in docker_call_text
    )
    assert " -v " in docker_call_text
    assert "--network" not in docker_call_text
    assert ":/tmp/arbiter-config-overrides:ro arbiter\n" in docker_call_text
    assert "ARBITER_PIP_VERBOSE=\n" in docker_call_text
    assert result.stdout == ""
    assert result.stderr == ""

    color_env = {**env, "ARBITER_COLOR": "always"}
    docker_calls.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=color_env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "-e ARBITER_COLOR=always " in docker_call_text

    term_color_env = {**env, "TERM": "xterm-256color"}
    term_color_env.pop("NO_COLOR", None)
    docker_calls.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=term_color_env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "-e ARBITER_COLOR=always " in docker_call_text

    docker_calls.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "--verbose",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "ARBITER_PIP_VERBOSE=1\n" in docker_call_text
    assert "--verbose" not in docker_call_text
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_deploy_docker_generated_helper_live_config_check_uses_one_shot_container(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = compose ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["TERM"] = "dumb"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "--live",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "info\n" in docker_call_text
    assert (
        f"compose --env-file {deploy_dir / 'docker.env'} "
        f"-f {deploy_dir / 'compose.yaml'} --progress quiet run --rm --no-deps "
    ) in docker_call_text
    assert "-e ARBITER_CONTAINER_ACTION=config-check " in docker_call_text
    assert "-e ARBITER_CONFIG_CHECK_LIVE=1 " in docker_call_text
    assert " -T arbiter sh -lc " not in docker_call_text
    assert "exec " not in docker_call_text
    assert ":/tmp/arbiter-config-overrides:ro arbiter\n" in docker_call_text
    assert result.stdout == ""
    assert result.stderr == ""

    color_env = {**env, "ARBITER_COLOR": "always"}
    docker_calls.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "config",
            "check",
            "--live",
            "arbiter.server.bind.port=9000",
        ],
        check=False,
        cwd=tmp_path,
        env=color_env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "-e ARBITER_COLOR=always " in docker_call_text
    assert "-e ARBITER_CONFIG_CHECK_LIVE=1 " in docker_call_text


def test_cli_deploy_docker_generated_helper_config_check_does_not_rewrite_subnet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = compose ]; then\n'
        "  printf 'Network arbiter-staging Creating\\n'\n"
        "  printf 'failed to create network arbiter-staging: Error response from daemon: invalid pool request: Pool overlaps with other one on this address space\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "config", "check"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "read-only command" in result.stderr
    assert f"This command will not rewrite {deploy_dir / 'docker.env'}" in result.stderr
    assert "updated staging Docker subnet" not in result.stdout
    assert "ARBITER_DOCKER_SUBNET=172.31.251.0/24\n" in (
        deploy_dir / "docker.env"
    ).read_text(encoding="utf-8")
    assert " network " not in docker_calls.read_text(encoding="utf-8")


def test_cli_deploy_docker_generated_helper_test_uses_curl_before_client(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "arbiter-docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_calls = tmp_path / "curl-calls"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env sh\n"
        "last_arg=\n"
        'for arg do last_arg="$arg"; done\n'
        f'printf "%s\\n" "$last_arg" >> "{curl_calls}"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_arbiter = fake_bin / "arbiter"
    fake_arbiter.write_text(
        "#!/usr/bin/env sh\n"
        "printf 'unexpected Arbiter client call\\n' >&2\n"
        "exit 9\n",
        encoding="utf-8",
    )
    fake_arbiter.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout == " ✔ Server test: https://127.0.0.1:18075\n"
    assert result.stderr == ""
    assert curl_calls.read_text(encoding="utf-8") == (
        "https://127.0.0.1:18075/_health_\n"
    )


def test_cli_deploy_docker_generated_helper_up_auto_selects_staging_subnet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = network ] && [ "$2" = ls ] && [ "${3:-}" = -q ]; then\n'
        "  printf 'network-id\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = network ] && [ "$2" = inspect ]; then\n'
        "  printf 'existing-network 172.31.251.0/24 \\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "updated staging Docker subnet: 172.31.251.0/24 -> 10.213.200.0/24\n"
        in result.stdout
    )
    assert " ✔ Staging port: 8075 -> 18075 to prevent collision\n" in result.stdout
    assert " ✔ URL: https://127.0.0.1:18075\n" in result.stdout
    assert result.stderr == ""
    assert "ARBITER_DOCKER_SUBNET=10.213.200.0/24\n" in (
        deploy_dir / "docker.env"
    ).read_text(encoding="utf-8")
    assert "compose --env-file" in docker_calls.read_text(encoding="utf-8")


def test_cli_deploy_docker_generated_helper_up_retries_docker_pool_overlap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = network ] && [ "$2" = ls ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then\n'
        f'  if grep -q "ARBITER_DOCKER_SUBNET=172.31.251.0/24" "{deploy_dir / "docker.env"}"; then\n'
        "    printf 'Network arbiter-staging Creating\\n'\n"
        "    printf 'failed to create network arbiter-staging: Error response from daemon: invalid pool request: Pool overlaps with other one on this address space\\n' >&2\n"
        "    exit 1\n"
        "  fi\n"
        "  printf 'Container arbiter-staging Started\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Container arbiter-staging Started\n" in result.stdout
    assert " ✔ Staging port: 8075 -> 18075 to prevent collision\n" in result.stdout
    assert " ✔ URL: https://127.0.0.1:18075\n" in result.stdout
    assert result.stderr == ""
    assert "Pool overlaps with other one on this address space" not in result.stdout
    assert "ARBITER_DOCKER_SUBNET=10.213.200.0/24\n" in (
        deploy_dir / "docker.env"
    ).read_text(encoding="utf-8")
    assert docker_calls.read_text(encoding="utf-8").count("compose --env-file") == 3


def test_cli_deploy_docker_generated_helper_up_explains_docker_pool_overlap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = network ] && [ "$2" = ls ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then\n'
        "  printf 'Network arbiter-staging Creating\\n'\n"
        "  printf 'failed to create network arbiter-staging: Error response from daemon: invalid pool request: Pool overlaps with other one on this address space\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "error: Docker could not create staging network arbiter-staging" in (
        result.stderr
    )
    assert "Docker rejected subnet 10.213.205.0/24" in result.stderr
    assert "retried alternate staging subnets" in result.stderr
    assert f"Edit {deploy_dir / 'docker.env'}" in result.stderr
    assert "ARBITER_DOCKER_SUBNET=10.214.200.0/24" in result.stderr
    assert "./arbiter-docker up" in result.stderr
    assert "docker network ls" in result.stderr
    assert "Docker said:" in result.stderr
    assert docker_calls.read_text(encoding="utf-8").count("compose --env-file") == 7


def test_cli_deploy_docker_generated_helper_up_reports_other_deployment_owner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "staged-arbiter"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = inspect ] && [ "$2" = arbiter-staging ]; then\n'
        "  cat <<'EOF'\n"
        "name=/arbiter-staging\n"
        "project=other-staging\n"
        "service=arbiter\n"
        "config_files=/tmp/other-staging/compose.yaml\n"
        "working_dir=/tmp/other-staging\n"
        "oneoff=False\n"
        "image=python:3.11-slim\n"
        "created=2026-06-03T06:15:38Z\n"
        "status=exited\n"
        "restart=on-failure\n"
        "EOF\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ]; then\n'
        "  exit 9\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "compose up" not in docker_calls.read_text(encoding="utf-8")
    assert (
        "error: container name is already owned by another deployment: arbiter-staging"
        in result.stderr
    )
    assert f"this deployment dir: {deploy_dir}" in result.stderr
    assert "owner compose project: other-staging" in result.stderr
    assert "owner deployment dir: /tmp/other-staging" in result.stderr
    assert "owner compose file: /tmp/other-staging/compose.yaml" in result.stderr
    assert "status: exited" in result.stderr
    assert "docker ps shows only running containers" in result.stderr
    assert "docker ps -a --filter name=^/arbiter-staging$" in result.stderr
    assert f"set ARBITER_CONTAINER_NAME in {deploy_dir / 'docker.env'}" in result.stderr


def test_cli_deploy_docker_generated_helper_up_fails_before_compose_up_on_config_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("bad config\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = network ] && [ "$2" = ls ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then exit 1; fi\n'
        'if [ "$1" = compose ]; then\n'
        '  for arg in "$@"; do\n'
        '    if [ "$arg" = run ]; then\n'
        "      printf 'config composition failed\\n' >&2\n"
        "      exit 78\n"
        "    fi\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "up"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 78
    assert "config composition failed\n" in result.stderr
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert " run --rm --no-deps " in docker_call_text
    assert " up -d\n" not in docker_call_text
    assert result.stdout == ""


def test_cli_deploy_docker_generated_helper_info_reports_other_deployment_owner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "staged-arbiter"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = inspect ] && [ "$2" = arbiter-staging ]; then\n'
        "  cat <<'EOF'\n"
        "name=/arbiter-staging\n"
        "project=other-staging\n"
        "service=arbiter\n"
        "config_files=/tmp/other-staging/compose.yaml\n"
        "working_dir=/tmp/other-staging\n"
        "oneoff=False\n"
        "image=python:3.11-slim\n"
        "created=2026-06-03T06:15:38Z\n"
        "status=exited\n"
        "restart=on-failure\n"
        "EOF\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "info"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert f"deploy dir: {deploy_dir}" in result.stdout
    assert "compose project: staged-arbiter" in result.stdout
    assert "container name: arbiter-staging" in result.stdout
    assert (
        "container name in use by another deployment: arbiter-staging" in result.stdout
    )
    assert "owner deployment dir: /tmp/other-staging" in result.stdout
    assert "owner compose file: /tmp/other-staging/compose.yaml" in result.stdout
    assert "status: exited" in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_rejects_unpinned_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    valid_result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert valid_result.returncode == 0
    assert (
        "ok: requirements file entries are syntactically valid" in valid_result.stdout
    )

    (deploy_dir / "requirements.txt").write_text("arbiter-suite\n", encoding="utf-8")
    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "requirement must be an exact package pin (name==version) or an "
        "absolute container path"
    ) in result.stdout
    assert (
        "fail: requirements file contains unpinned package requirements"
        in result.stdout
    )
    assert "\033[" not in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_rejects_raw_meta_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "requirements.txt").write_text(
        "arbiter-suite==0.9.0\n" "arbiter-smtp==0.9.1\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "arbiter-suite meta package cannot be combined directly with "
        "component package pins"
    ) in result.stdout
    assert (
        "fail: requirements file contains unpinned package requirements"
        in result.stdout
    )


def test_cli_deploy_docker_generated_helper_doctor_rejects_missing_plugin_data_mount(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    compose_file = deploy_dir / "compose.yaml"
    compose_file.write_text(
        compose_file.read_text(encoding="utf-8")
        .replace(
            "      - ${ARBITER_PLUGIN_DATA_DIR:-./data/plugins}:/data/plugins\n",
            "",
        )
        .replace(
            ' "arbiter.storage.plugin_data_dir=/data/plugins"',
            "",
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: compose file does not mount plugin data directory at /data/plugins"
        in result.stdout
    )
    assert (
        "fail: compose file does not configure "
        "arbiter.storage.plugin_data_dir=/data/plugins"
    ) in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_unwritable_plugin_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            f"ARBITER_CONTAINER_USER={_default_container_user()}\n",
            "ARBITER_CONTAINER_USER=12345:12345\n",
        ),
        encoding="utf-8",
    )
    (deploy_dir / "data" / "plugins").chmod(0o755)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: plugin data directory is not writable by container user: " "12345:12345"
    ) in result.stdout
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_open_plugin_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "data" / "plugins").chmod(0o755)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: plugin data directory is accessible outside its owner:" in result.stdout
    )
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_can_color_status_prefixes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "requirements.txt").write_text("arbiter-suite\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_COLOR"] = "always"
    env["NO_COLOR"] = "1"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "\033[32mok\033[0m:" in result.stdout
    assert "\033[33mwarn\033[0m:" in result.stdout
    assert "\033[31mfail\033[0m:" in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_can_disable_color(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_COLOR"] = "never"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "\033[" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_skips_docker_checks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Docker Compose" not in result.stdout
    assert f"ok: container user is non-root: {_default_container_user()}\n" in (
        result.stdout
    )
    assert "ok: preinstall checks passed\n" in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_root_container_user(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            f"ARBITER_CONTAINER_USER={_default_container_user()}\n",
            "ARBITER_CONTAINER_USER=0:0\n",
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "fail: ARBITER_CONTAINER_USER must not be root: 0:0\n" in result.stdout
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_rejects_unsafe_config_permissions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o644)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o640)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "fail: config file is world-readable or world-writable:" in result.stdout
    assert (
        "fail: app env file is readable or writable outside its owner:" in result.stdout
    )
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_checks_wheel_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/wheels/arbiter_server-1.2.3-py3-none-any.whl",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)

    missing_result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert missing_result.returncode == 1
    assert (
        "fail: wheel requirement is missing from deployment wheelhouse: "
        f"{deploy_dir / 'wheels' / 'arbiter_server-1.2.3-py3-none-any.whl'}\n"
    ) in missing_result.stdout

    wheels_dir = deploy_dir / "wheels"
    wheels_dir.mkdir(exist_ok=True)
    (wheels_dir / "arbiter_server-1.2.3-py3-none-any.whl").write_text(
        "wheel\n",
        encoding="utf-8",
    )
    valid_result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert valid_result.returncode == 0
    assert (
        "ok: wheel requirement exists: "
        f"{deploy_dir / 'wheels' / 'arbiter_server-1.2.3-py3-none-any.whl'}\n"
    ) in valid_result.stdout
    assert "ok: preinstall checks passed\n" in valid_result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_absolute_runtime_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_WHEELS_DIR=./wheels\n",
            f"ARBITER_WHEELS_DIR={tmp_path / 'external-wheels'}\n",
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: wheels directory uses an absolute host path for install: "
        f"ARBITER_WHEELS_DIR={tmp_path / 'external-wheels'}\n"
    ) in result.stdout
    assert "edit docker.env to use a path relative to the deployment directory\n" in (
        result.stdout
    )
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_runtime_path_traversal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_WHEELS_DIR=./wheels\n",
            "ARBITER_WHEELS_DIR=../external-wheels\n",
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        f"fail: wheels directory is outside deployment directory: "
        f"{deploy_dir / '../external-wheels'}\n"
    ) in result.stdout
    assert "edit docker.env to use a path inside the deployment directory\n" in (
        result.stdout
    )
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_deploy_root_runtime_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (deploy_dir / "arbiter-server.yaml").chmod(0o640)
    config_dir = deploy_dir / "conf"
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    docker_env = deploy_dir / "docker.env"
    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_CONFIG_DIR=./conf\n",
            "ARBITER_CONFIG_DIR=.\n",
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: config directory resolves to the deployment directory root: "
        "ARBITER_CONFIG_DIR=.\n"
    ) in result.stdout
    assert (
        "edit docker.env to use a dedicated path inside the deployment directory\n"
        in (result.stdout)
    )
    assert "ok: preinstall checks passed\n" not in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_notes_local_checkout_packages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  arbiter:\n"
        "    volumes:\n"
        "      - /home/example/arbiter:/source/arbiter:ro\n",
        encoding="utf-8",
    )
    local_checkout_requirements = (
        "\n".join(
            [
                "/source/arbiter/server",
                "/source/arbiter/plugins/imap",
            ]
        )
        + "\n"
    )
    (deploy_dir / "requirements.txt").write_text(
        local_checkout_requirements,
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "note: package wheels will be built from the local checkout: "
        "arbiter-server, arbiter-imap\n"
    ) in result.stdout
    assert (
        "note: install target will not copy the /source/arbiter source mount\n"
    ) not in result.stdout
    assert "note: local checkout requirements will be wheel-backed" not in result.stdout
    assert "warn: staging uses local checkout" not in result.stdout
    assert "ok: preinstall checks passed\n" in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_notes_source_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  arbiter:\n"
        "    volumes:\n"
        "      - /home/example/arbiter:/source/arbiter:ro\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor", "--preinstall"],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "note: install target will not copy the /source/arbiter source mount\n"
    ) in result.stdout
    assert "note: package wheels will be built from the local checkout:" not in (
        result.stdout
    )
    assert (
        "warn: preinstall found local checkout compose override:" not in result.stdout
    )
    assert "ok: preinstall checks passed\n" in result.stdout


def test_cli_deploy_docker_generated_helper_install_keeps_source_checkout_staging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    deploy_dir = repo_dir / "arbiter-docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    package_dir = repo_dir / "server"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    for plugin_dir in (repo_dir / "plugins" / "imap", repo_dir / "plugins" / "smtp"):
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=/source/arbiter/server",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/arbiter/server\n"
    )
    (deploy_dir / "requirements.txt").write_text(
        "/source/arbiter/server\n/source/arbiter/plugins/imap\n",
        encoding="utf-8",
    )
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  arbiter:\n"
        "    volumes:\n"
        f"      - {repo_dir}:/source/arbiter:ro\n",
        encoding="utf-8",
    )
    assert "/source/arbiter" in (deploy_dir / "compose.override.yaml").read_text(
        encoding="utf-8"
    )
    plugin_data_dir = deploy_dir / "data" / "plugins"
    smtp_state_dir = plugin_data_dir / "smtp"
    smtp_state_dir.mkdir(parents=True)
    (smtp_state_dir / "idempotency.sqlite").write_text("state\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = cat ] && [ "$2" = docker.service ]; then exit 1; fi\n'
        'if [ "$1" = is-active ]; then exit 3; fi\n'
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    python_calls = tmp_path / "python-calls"
    (fake_bin / "python").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{python_calls}"\n'
        'wheel_dir=""\n'
        'source_dir=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--wheel-dir" ]; then\n'
        "    shift\n"
        '    wheel_dir="$1"\n'
        "  else\n"
        '    source_dir="$1"\n'
        "  fi\n"
        "  shift\n"
        "done\n"
        'case "$source_dir" in\n'
        '  */plugins/imap) wheel_name="arbiter_imap-0.9.0.dev2-py3-none-any.whl" ;;\n'
        '  *) wheel_name="arbiter_server-0.9.0.dev2-py3-none-any.whl" ;;\n'
        "esac\n"
        'printf "wheel\\n" > "$wheel_dir/$wheel_name"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "arbiter").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in (
        "id",
        "getent",
        "docker",
        "systemctl",
        "python",
        "arbiter",
        "chown",
    ):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["ARBITER_INSTALL_PROGRESS"] = "always"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "prepared wheel-backed install requirements from local checkout:" not in (
        result.stdout
    )
    assert "note: package wheels will be built from the local checkout:" not in (
        result.stdout
    )
    assert "installed wheel-backed requirements without changing staging:" not in (
        result.stdout
    )
    assert "omitted local checkout compose override from install target:" not in (
        result.stdout
    )
    assert "preparing: arbiter-server" in result.stdout
    assert "preparing: arbiter-imap" in result.stdout
    assert "preparing wheelhouse" in result.stdout
    assert "preparing dependencies for arbiter-" not in result.stdout
    assert "preparing dependency wheelhouse" not in result.stdout
    assert "preparing: dependency wheelhouse" not in result.stdout
    assert "Installing server into " in result.stdout
    assert ": done\n" not in result.stdout
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/arbiter/server\n/source/arbiter/plugins/imap\n"
    )
    assert (deploy_dir / "compose.override.yaml").exists()
    assert not (deploy_dir / "compose.override.yaml.local-source.bak").exists()
    assert (install_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/wheels/arbiter_server-0.9.0.dev2-py3-none-any.whl\n"
        "/wheels/arbiter_imap-0.9.0.dev2-py3-none-any.whl\n"
    )
    assert not (install_dir / "compose.override.yaml").exists()
    unit_text = (systemd_dir / "arbiter.service").read_text(encoding="utf-8")
    assert f"-f {install_dir / 'compose.yaml'}" in unit_text
    assert f"-f {install_dir / 'compose.override.yaml'}" not in unit_text
    assert (
        install_dir / "wheels" / "arbiter_server-0.9.0.dev2-py3-none-any.whl"
    ).exists()
    assert (
        install_dir / "wheels" / "arbiter_imap-0.9.0.dev2-py3-none-any.whl"
    ).exists()
    _assert_posix_mode(install_dir / "data" / "plugins", 0o700)
    _assert_posix_mode(install_dir / "data" / "plugins" / "smtp", 0o700)
    _assert_posix_mode(
        install_dir / "data" / "plugins" / "smtp" / "idempotency.sqlite",
        0o600,
    )
    assert " pip --disable-pip-version-check wheel " in python_calls.read_text(
        encoding="utf-8"
    )
    assert f"Installed Arbiter to {install_dir}. Running as arbiter:arbiter\n" in (
        result.stdout
    )
    assert "service: arbiter.service" not in result.stdout


def test_cli_deploy_docker_generated_helper_install_disables_source_override_without_source_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "arbiter-docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  arbiter:\n"
        "    volumes:\n"
        "      - /tmp/arbiter:/source/arbiter:ro\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "arbiter").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in (
        "id",
        "getent",
        "docker",
        "systemctl",
        "arbiter",
        "chown",
    ):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["ARBITER_PYTHON"] = str(tmp_path / "missing-python")

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-suite==1.2.3\n"
    )
    assert not (install_dir / "compose.override.yaml").exists()
    assert (install_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-suite==1.2.3\n"
    )
    assert "promoted local checkout requirements to wheels" not in result.stdout
    assert "omitted local checkout compose override from install target:" not in (
        result.stdout
    )
    assert (deploy_dir / "compose.override.yaml").exists()
    assert not (deploy_dir / "compose.override.yaml.local-source.bak").exists()


def test_cli_deploy_docker_generated_helper_install_dry_run_plans_promotion(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--dry-run",
            "--to",
            "/opt/arbiter",
            "--user",
            "arbiter",
        ],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "installing Arbiter" not in result.stdout
    assert "ok: preinstall checks passed\n" not in result.stdout
    assert f"would copy deployment: {deploy_dir} -> /opt/arbiter\n" in result.stdout
    assert "would preserve installed config and env if present: /opt/arbiter\n" in (
        result.stdout
    )
    assert "would create system group if missing: arbiter\n" in result.stdout
    assert "would create system user if missing: arbiter\n" in result.stdout
    assert (
        "would set deployment scope in compose.yaml: "
        "arbiter.deployment_scope=installed\n"
    ) in result.stdout
    assert (
        "would set installed Docker identity and container user in docker.env\n"
        in result.stdout
    )
    assert "would write systemd unit: /etc/systemd/system/arbiter.service\n" in (
        result.stdout
    )
    assert (
        "would check candidate config before install: installed config if present\n"
        in result.stdout
    )
    assert "would run: systemctl restart arbiter.service\n" in result.stdout
    assert "would run: /opt/arbiter/arbiter-docker config check --live\n" in (
        result.stdout
    )
    static_index = result.stdout.index(
        "would check candidate config before install: installed config if present\n"
    )
    copy_index = result.stdout.index(
        f"would copy deployment: {deploy_dir} -> /opt/arbiter\n"
    )
    restart_index = result.stdout.index(
        "would run: systemctl restart arbiter.service\n"
    )
    live_index = result.stdout.index(
        "would run: /opt/arbiter/arbiter-docker config check --live\n"
    )
    assert static_index < copy_index < restart_index < live_index


def test_cli_deploy_docker_generated_helper_install_updates_unit_when_docker_down(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    container_uid, container_gid = _default_container_user().split(":", 1)
    (fake_bin / "stat").write_text(
        "#!/usr/bin/env sh\n"
        'case "$*" in\n'
        f'  *"{deploy_dir / "data/plugins"}"*)\n'
        '    if [ "$1" = -c ] && [ "$2" = "%u %g %a" ]; then '
        f'printf "{container_uid} {container_gid} 700\\n"; exit 0; fi\n'
        '    if [ "$1" = -c ] && [ "$2" = "%a" ]; then '
        'printf "700\\n"; exit 0; fi\n'
        "    ;;\n"
        "esac\n"
        'exec /usr/bin/stat "$@"\n',
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then printf "docker is down\\n" >&2; exit 1; fi\n'
        "exit 99\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = cat ] && [ "$2" = docker.service ]; then exit 1; fi\n'
        'if [ "$1" = is-active ]; then exit 3; fi\n'
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "stat", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["WSL_DISTRO_NAME"] = "Ubuntu"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "warn: Docker daemon is unavailable; Docker-backed checks and restart "
        "will be skipped\n"
    ) in result.stdout
    assert (
        "warn: install will update files and "
        "systemd unit without Docker-backed checks or restart\n"
    ) in result.stdout
    assert "Deployment files: unchanged" not in result.stdout
    assert "warn: Docker daemon is unavailable; skipping static config check\n" in (
        result.stdout
    )
    assert (
        "warn: Docker daemon is unavailable; skipping installed wheelhouse "
        "validation\n"
    ) in result.stdout
    assert (
        "warn: Docker daemon is unavailable; skipping service restart, server "
        "test, and live config check\n"
    ) in result.stdout
    assert f"systemd unit: {systemd_dir / 'arbiter.service'}\n" in result.stdout
    assert "Config check: skipped (Docker unavailable)\n" in result.stdout
    assert "Service restart: skipped (Docker unavailable)\n" in result.stdout
    assert (
        "Start after Docker is ready: sudo systemctl restart arbiter.service\n"
        in result.stdout
    )
    assert "Config check: passed\n" not in result.stdout
    assert (install_dir / "compose.yaml").is_file()
    unit_text = (systemd_dir / "arbiter.service").read_text(encoding="utf-8")
    assert 'ExecStartPre=/bin/sh -c \'i=0; while [ "$i" -lt 120 ];' in unit_text
    assert docker_calls.read_text(encoding="utf-8") == "info\n"
    assert systemctl_calls.read_text(encoding="utf-8") == (
        "daemon-reload\n" "enable arbiter.service\n"
    )


def test_cli_deploy_docker_generated_helper_install_unit_only_when_docker_down_and_service_active(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    install_dir.mkdir(parents=True)
    old_compose = "services:\n  old-arbiter:\n    image: old\n"
    old_env = "ARBITER_HOST_BIND=127.0.0.1\nARBITER_HOST_PORT=18075\n"
    (install_dir / "compose.yaml").write_text(old_compose, encoding="utf-8")
    (install_dir / "docker.env").write_text(old_env, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = info ]; then printf "docker is down\\n" >&2; exit 1; fi\n'
        "exit 99\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = cat ] && [ "$2" = docker.service ]; then exit 1; fi\n'
        'if [ "$1" = is-active ]; then exit 0; fi\n'
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    for fake_command in ("id", "docker", "systemctl"):
        (fake_bin / fake_command).chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["WSL_DISTRO_NAME"] = "Ubuntu"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (
        "warn: existing service is active; updating only the systemd unit so "
        "running deployment files are not replaced\n"
    ) in result.stdout
    assert (
        f"Updated Arbiter systemd unit for existing install: {install_dir}\n"
        in result.stdout
    )
    assert (
        "Deployment files: unchanged (Docker unavailable and service active)\n"
        in result.stdout
    )
    assert (
        "warn: Docker daemon is unavailable; skipping installed wheelhouse validation"
        not in result.stdout
    )
    assert (install_dir / "compose.yaml").read_text(encoding="utf-8") == old_compose
    assert (install_dir / "docker.env").read_text(encoding="utf-8") == old_env
    unit_text = (systemd_dir / "arbiter.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={install_dir}\n" in unit_text
    assert 'ExecStartPre=/bin/sh -c \'i=0; while [ "$i" -lt 120 ];' in unit_text
    assert docker_calls.read_text(encoding="utf-8") == "info\n"
    assert systemctl_calls.read_text(encoding="utf-8") == (
        "daemon-reload\n" "enable arbiter.service\n"
    )


def test_cli_deploy_docker_generated_helper_install_replace_env_requires_replace_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--dry-run",
            "--to",
            "/opt/arbiter",
            "--user",
            "arbiter",
            "--replace-env",
        ],
        check=False,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stderr == "error: --replace-env requires --replace-config\n"


def test_cli_deploy_docker_generated_helper_install_preflights_replacement_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "bad staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)
    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    installed_config = installed_config_dir / "arbiter-server.yaml"
    installed_config.write_text("installed config\n", encoding="utf-8")
    installed_env = installed_config_dir / ".env"
    installed_env.write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=arbiter-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    candidate_env = tmp_path / "candidate-env"
    candidate_compose = tmp_path / "candidate-compose.yaml"
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'command="$1"\n'
        'env_file=""\n'
        'compose_file=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--env-file" ]; then shift; env_file="$1"; fi\n'
        '  if [ "$1" = "-f" ]; then shift; compose_file="$1"; fi\n'
        "  shift\n"
        "done\n"
        f'[ -n "$env_file" ] && cat "$env_file" > "{candidate_env}"\n'
        f'[ -n "$compose_file" ] && cat "$compose_file" > "{candidate_compose}"\n'
        'if [ "$command" = info ]; then exit 0; fi\n'
        'printf "candidate config failed\\n" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    for fake_command in ("id", "docker", "systemctl"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--replace-config",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "candidate config failed\n" in result.stderr
    assert installed_config.read_text(encoding="utf-8") == "installed config\n"
    assert installed_env.read_text(encoding="utf-8") == "SECRET=installed\n"
    assert "ARBITER_CONFIG_DIR=./conf\n" in (install_dir / "docker.env").read_text(
        encoding="utf-8"
    )
    assert not systemctl_calls.exists()
    candidate_env_text = candidate_env.read_text(encoding="utf-8")
    assert f"ARBITER_CONFIG_DIR={staging_config_dir}\n" in candidate_env_text
    assert f"ARBITER_APP_ENV_FILE={installed_env}\n" in candidate_env_text
    candidate_compose_text = candidate_compose.read_text(encoding="utf-8")
    assert "networks:\n  arbiter:\n" in candidate_compose_text
    assert "ARBITER_DOCKER_NETWORK_NAME" not in candidate_compose_text
    assert "ARBITER_DOCKER_BRIDGE_NAME" not in candidate_compose_text
    assert "ARBITER_DOCKER_SUBNET" not in candidate_compose_text


@pytest.mark.parametrize(
    ("wsl_env", "docker_unit_exists", "expect_docker_unit_warning"),
    [(True, False, False), (False, False, True), (False, True, False)],
)
def test_cli_deploy_docker_generated_helper_install_handles_docker_unit_availability(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wsl_env: bool,
    docker_unit_exists: bool,
    expect_docker_unit_warning: bool,
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "grep").write_text(
        "#!/usr/bin/env sh\n"
        'case " $* " in\n'
        '  *" microsoft "*) exit 1 ;;\n'
        "esac\n"
        'exec /usr/bin/grep "$@"\n',
        encoding="utf-8",
    )
    (fake_bin / "stat").write_text(
        "#!/usr/bin/env sh\n"
        'case "$*" in\n'
        f'  *"{install_dir / "data/plugins"}"*)\n'
        '    if [ "$1" = -c ] && [ "$2" = "%u %g %a" ]; then '
        'printf "123 123 700\\n"; exit 0; fi\n'
        '    if [ "$1" = -c ] && [ "$2" = "%a" ]; then '
        'printf "700\\n"; exit 0; fi\n'
        "    ;;\n"
        "esac\n"
        'exec /usr/bin/stat "$@"\n',
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = compose ]; then\n'
        '  printf "Looking in links: /wheels\\n"\n'
        '  printf "server: pass\\n"\n'
        '  printf "result | plugin | account | policy | message\\n"\n'
        '  printf "time=\\"2026-06-15T12:10:47+08:00\\" level=warning msg=\\"Compose chatter\\"\\n" >&2\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = cat ] && [ "$2" = docker.service ]; then '
        f"exit {0 if docker_unit_exists else 1}; fi\n"
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "arbiter").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in (
        "id",
        "getent",
        "grep",
        "stat",
        "docker",
        "systemctl",
        "arbiter",
        "chown",
    ):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["ARBITER_COLOR"] = "always"
    env["ARBITER_INSTALL_PROGRESS"] = "always"
    for wsl_key in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
        env.pop(wsl_key, None)
    if wsl_env:
        env["WSL_DISTRO_NAME"] = "Ubuntu"

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    docker_unit_warning = (
        "\033[33mwarn\033[0m: docker.service not found; generated unit will "
        "wait for Docker API readiness without service ordering\n"
    )
    if expect_docker_unit_warning:
        assert docker_unit_warning in result.stdout
    else:
        assert docker_unit_warning not in result.stdout
        assert "docker.service not found" not in result.stdout
    assert "Installing server into " in result.stdout
    static_index = result.stdout.index("Performing static config check")
    restart_index = result.stdout.index("Restarting Arbiter service")
    server_test_index = result.stdout.index("Testing server URL")
    live_index = result.stdout.index("Performing live config check")
    assert static_index < restart_index < server_test_index < live_index
    assert ": done\n" not in result.stdout
    assert "success: installed Arbiter Docker deployment\n" not in result.stdout
    assert f"installed to: {install_dir}\n" not in result.stdout
    assert f"Installed Arbiter to {install_dir}. Running as arbiter:arbiter\n" in (
        result.stdout
    )
    assert f"systemd unit: {systemd_dir / 'arbiter.service'}\n" in result.stdout
    assert "service: arbiter.service" not in result.stdout
    assert "Config check: passed\n" in result.stdout
    assert "Server test:" not in result.stdout
    assert "Server URL: \033[94mhttps://127.0.0.1:8075\033[0m\n" in result.stdout
    assert "Client config:" not in result.stdout
    assert "Looking in links:" not in result.stdout
    assert "server: pass" not in result.stdout
    assert "result | plugin" not in result.stdout
    assert "level=warning" not in result.stderr
    assert result.stderr == ""
    installed_compose = (install_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "arbiter.deployment_scope=installed" in installed_compose
    assert "ARBITER_CONTAINER_NAME:-arbiter-staging" not in installed_compose
    assert "ARBITER_HOST_PORT:-18075" not in installed_compose
    assert "ARBITER_DOCKER_NETWORK_NAME:-arbiter-staging" not in installed_compose
    assert "ARBITER_DOCKER_BRIDGE_NAME:-arbiter-stg0" not in installed_compose
    assert "ARBITER_DOCKER_SUBNET:-172.31.251.0/24" not in installed_compose
    assert "ARBITER_CONTAINER_NAME:-arbiter" in installed_compose
    assert "ARBITER_HOST_BIND:-127.0.0.1" in installed_compose
    assert "ARBITER_HOST_PORT:-8075" in installed_compose
    assert "ARBITER_DOCKER_NETWORK_NAME:-arbiter" in installed_compose
    assert "ARBITER_DOCKER_BRIDGE_NAME:-arbiter0" in installed_compose
    assert "ARBITER_DOCKER_SUBNET:-172.31.250.0/24" in installed_compose
    assert "ARBITER_DEPLOYMENT_SCOPE" not in (install_dir / "docker.env").read_text(
        encoding="utf-8"
    )
    installed_docker_env = (install_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_CONTAINER_NAME=arbiter\n" in installed_docker_env
    assert "ARBITER_CONTAINER_USER=123:123\n" in installed_docker_env
    assert "ARBITER_HOST_BIND=127.0.0.1\n" in installed_docker_env
    assert "ARBITER_HOST_PORT=8075\n" in installed_docker_env
    assert "ARBITER_SERVER_DATA_DIR=./data/server\n" in installed_docker_env
    assert "ARBITER_PLUGIN_DATA_DIR=./data/plugins\n" in installed_docker_env
    assert "ARBITER_DOCKER_NETWORK_NAME=arbiter\n" in installed_docker_env
    assert "ARBITER_DOCKER_BRIDGE_NAME=arbiter0\n" in installed_docker_env
    assert "ARBITER_DOCKER_SUBNET=172.31.250.0/24\n" in installed_docker_env
    unit_text = (systemd_dir / "arbiter.service").read_text(encoding="utf-8")
    if docker_unit_exists:
        assert "Requires=docker.service\n" in unit_text
        assert "After=docker.service\n" in unit_text
    else:
        assert "Requires=docker.service\n" not in unit_text
        assert "After=docker.service\n" not in unit_text
    assert (
        'ExecStartPre=/bin/sh -c \'i=0; while [ "$i" -lt 120 ]; '
        'do [ -x "$1" ] && "$1" info >/dev/null 2>&1 && exit 0; '
        "i=$((i + 1)); sleep 1; done; "
        'echo "error: Docker API did not become ready for Arbiter" >&2; '
        "exit 1' "
        f"arbiter-docker-ready {fake_bin / 'docker'}\n"
    ) in unit_text
    assert "Restart=on-failure\n" in unit_text
    assert "RestartSec=10\n" in unit_text
    assert "TimeoutStartSec=180\n" in unit_text
    assert f"WorkingDirectory={install_dir}\n" in unit_text
    docker_call_text = docker_calls.read_text(encoding="utf-8")
    static_check_index = docker_call_text.index("compose --env-file ")
    static_check_line = docker_call_text[static_check_index:].splitlines()[0]
    assert "arbiter-install-check." in static_check_line
    wheelhouse_check_index = docker_call_text.index(
        "run --rm --user 123:123 "
        f"-v {install_dir / 'requirements.txt'}:/requirements.txt:ro "
        f"-v {install_dir / 'wheels'}:/wheels:ro "
        "python:3.11-slim python -m pip --disable-pip-version-check "
        "install --no-cache-dir "
        "--target /tmp/arbiter-wheelhouse-check --no-index --find-links /wheels "
        "-r /requirements.txt\n"
    )
    compose_down_index = docker_call_text.index(
        f"compose --env-file {install_dir / 'docker.env'} "
        f"-f {install_dir / 'compose.yaml'} down --remove-orphans\n"
    )
    live_check_index = docker_call_text.index(
        f"compose --env-file {install_dir / 'docker.env'} "
        f"-f {install_dir / 'compose.yaml'} --progress quiet run --rm --no-deps "
        "-e ARBITER_CONTAINER_ACTION=config-check -e ARBITER_COLOR=always "
        "-e ARBITER_CONFIG_CHECK_LIVE=1 arbiter\n"
    )
    assert "-e ARBITER_CONTAINER_ACTION=config-check " in docker_call_text
    assert "-e ARBITER_COLOR=always " in docker_call_text
    assert (
        static_check_index
        < wheelhouse_check_index
        < compose_down_index
        < live_check_index
    )
    assert systemctl_calls.read_text(encoding="utf-8") == (
        "daemon-reload\n"
        "enable arbiter.service\n"
        "stop arbiter.service\n"
        "reset-failed arbiter.service\n"
        "restart arbiter.service\n"
    )


def test_cli_deploy_docker_generated_helper_install_verbose_prints_wheel_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'if [ "$1" = compose ]; then\n'
        f'  printf "ARBITER_PIP_VERBOSE=%s\\n" "${{ARBITER_PIP_VERBOSE:-}}" >> "{docker_calls}"\n'
        '  printf "Looking in links: /wheels\\n"\n'
        '  printf "Processing /wheels/arbiter.whl\\n"\n'
        '  printf "server: pass\\n"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "arbiter").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in (
        "id",
        "getent",
        "docker",
        "systemctl",
        "arbiter",
        "chown",
    ):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--verbose",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Looking in links: /wheels" in result.stdout
    assert "Processing /wheels/arbiter.whl" in result.stdout
    assert "ARBITER_PIP_VERBOSE=1\n" in docker_calls.read_text(encoding="utf-8")


def test_cli_deploy_docker_generated_helper_install_preserves_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "installed-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / ".env").write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=installed-server\n"
        "ARBITER_SERVER_DATA_DIR=./state/server\n"
        "ARBITER_PLUGIN_DATA_DIR=./state/plugins\n",
        encoding="utf-8",
    )
    (install_dir / "state" / "server").mkdir(parents=True)
    (install_dir / "state" / "plugins").mkdir(parents=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (installed_config_dir / "installed-server.yaml").read_text(
        encoding="utf-8"
    ) == "installed config\n"
    assert not (installed_config_dir / "arbiter-server.yaml").exists()
    assert (installed_config_dir / ".env").read_text(encoding="utf-8") == (
        "SECRET=installed\n"
    )
    installed_docker_env = (install_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_CONFIG_DIR=./conf\n" in installed_docker_env
    assert "ARBITER_APP_ENV_FILE=./conf/.env\n" in installed_docker_env
    assert "ARBITER_CONFIG_NAME=installed-server\n" in installed_docker_env
    assert "ARBITER_SERVER_DATA_DIR=./state/server\n" in installed_docker_env
    assert "ARBITER_PLUGIN_DATA_DIR=./state/plugins\n" in installed_docker_env


@pytest.mark.parametrize("replace_args", [(), ("--replace-config",)])
def test_cli_deploy_docker_generated_helper_install_does_not_stage_env_in_tmp(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    replace_args: tuple[str, ...],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "arbiter-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / ".env").write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=arbiter-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    mktemp_calls = tmp_path / "mktemp-calls"
    (fake_bin / "mktemp").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{mktemp_calls}"\n'
        'exec /usr/bin/mktemp "$@"\n',
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "mktemp", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["TMPDIR"] = str(tmpdir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            *replace_args,
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    mktemp_text = mktemp_calls.read_text(encoding="utf-8")
    assert f"{tmpdir}/arbiter-install-config." not in mktemp_text
    assert f"{install_dir / 'backup' / '.preserve'}." in mktemp_text
    for leaked_file in tmpdir.rglob("*"):
        if leaked_file.is_file():
            leaked_text = leaked_file.read_text(encoding="utf-8", errors="ignore")
            assert "SECRET=installed" not in leaked_text
            assert "SECRET=staging" not in leaked_text


def test_cli_deploy_docker_generated_helper_install_symlink_failure_keeps_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)
    try:
        (deploy_dir / "bad-link").symlink_to("requirements.txt")
    except OSError as exc:
        pytest.skip(f"symlinks are not available: {exc}")

    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "installed-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / ".env").write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=installed-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "error: install does not support symlinks in deployment tree:" in (
        result.stderr
    )
    assert (installed_config_dir / "installed-server.yaml").read_text(
        encoding="utf-8"
    ) == "installed config\n"
    assert (installed_config_dir / ".env").read_text(encoding="utf-8") == (
        "SECRET=installed\n"
    )
    assert not (install_dir / "backup").exists()
    assert not (systemd_dir / "arbiter.service").exists()


def test_cli_deploy_docker_generated_helper_install_rejects_installed_config_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "installed-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / ".env").write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=.\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=installed-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "error: install requires docker.env host paths below --to, "
        "got deployment root: .\n"
    ) in result.stderr
    assert (installed_config_dir / "installed-server.yaml").read_text(
        encoding="utf-8"
    ) == "installed config\n"
    assert (installed_config_dir / ".env").read_text(encoding="utf-8") == (
        "SECRET=installed\n"
    )
    assert not (install_dir / "backup").exists()
    assert not (systemd_dir / "arbiter.service").exists()


def test_cli_deploy_docker_generated_helper_install_protects_custom_installed_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "conf"
    installed_env_dir = install_dir / "secrets"
    installed_config_dir.mkdir(parents=True)
    installed_env_dir.mkdir()
    (installed_config_dir / "installed-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    installed_env = installed_env_dir / "arbiter.env"
    installed_env.write_text("SECRET=installed\n", encoding="utf-8")
    installed_env.chmod(0o644)
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./secrets/arbiter.env\n"
        "ARBITER_CONFIG_NAME=installed-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert installed_env.read_text(encoding="utf-8") == "SECRET=installed\n"
    _assert_posix_mode(installed_env, 0o600)
    installed_docker_env = (install_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_APP_ENV_FILE=./secrets/arbiter.env\n" in installed_docker_env


def test_cli_deploy_docker_generated_helper_install_preserves_custom_config_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "prod-conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "installed-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    installed_env = installed_config_dir / ".env"
    installed_env.write_text("SECRET=installed\n", encoding="utf-8")
    installed_env.chmod(0o644)
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./prod-conf\n"
        "ARBITER_APP_ENV_FILE=./prod-conf/.env\n"
        "ARBITER_CONFIG_NAME=installed-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (installed_config_dir / "installed-server.yaml").read_text(
        encoding="utf-8"
    ) == "installed config\n"
    assert not (installed_config_dir / "arbiter-server.yaml").exists()
    assert not (installed_config_dir / "config-dir").exists()
    assert installed_env.read_text(encoding="utf-8") == "SECRET=installed\n"
    _assert_posix_mode(installed_env, 0o600)
    config_backups = sorted((install_dir / "backup").glob("conf-*"))
    assert len(config_backups) == 1
    assert (config_backups[0] / "installed-server.yaml").read_text(
        encoding="utf-8"
    ) == "installed config\n"
    assert (config_backups[0] / ".env").read_text(encoding="utf-8") == (
        "SECRET=installed\n"
    )
    _assert_posix_mode(config_backups[0] / ".env", 0o600)
    installed_docker_env = (install_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_CONFIG_DIR=./prod-conf\n" in installed_docker_env
    assert "ARBITER_APP_ENV_FILE=./prod-conf/.env\n" in installed_docker_env


@pytest.mark.parametrize(
    ("replace_args", "expected_env"),
    [
        (("--replace-config",), "SECRET=installed\n"),
        (("--replace-config", "--replace-env"), "SECRET=staging\n"),
    ],
)
def test_cli_deploy_docker_generated_helper_install_can_replace_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    replace_args: tuple[str, ...],
    expected_env: str,
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    staging_config_dir = deploy_dir / "conf"
    (staging_config_dir / "arbiter-server.yaml").write_text(
        "staging config\n",
        encoding="utf-8",
    )
    (staging_config_dir / "arbiter-server.yaml").chmod(0o640)
    (staging_config_dir / ".env").write_text("SECRET=staging\n", encoding="utf-8")
    (staging_config_dir / ".env").chmod(0o600)

    installed_config_dir = install_dir / "conf"
    installed_config_dir.mkdir(parents=True)
    (installed_config_dir / "arbiter-server.yaml").write_text(
        "installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / "installed-only.yaml").write_text(
        "stale installed config\n",
        encoding="utf-8",
    )
    (installed_config_dir / ".env").write_text("SECRET=installed\n", encoding="utf-8")
    (install_dir / "docker.env").write_text(
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_NAME=arbiter-server\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then '
        'printf "arbiter:x:123:\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
            *replace_args,
            "--no-start",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert (installed_config_dir / "arbiter-server.yaml").read_text(
        encoding="utf-8"
    ) == "staging config\n"
    assert not (installed_config_dir / "installed-only.yaml").exists()
    assert (installed_config_dir / ".env").read_text(encoding="utf-8") == expected_env


def test_cli_deploy_docker_generated_helper_install_aborts_on_bad_wheelhouse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    install_dir = tmp_path / "opt" / "arbiter"
    systemd_dir = tmp_path / "systemd"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = -u ] && [ "$#" = 1 ]; then printf "0\\n"; exit 0; fi\n'
        'if [ "$1" = -u ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        'if [ "$1" = -g ] && [ "$2" = arbiter ]; then printf "123\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = group ] && [ "$2" = arbiter ]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_calls = tmp_path / "docker-calls"
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{docker_calls}"\n'
        'case "$*" in\n'
        '  *"--no-index --find-links /wheels"*) exit 17 ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)

    result = subprocess.run(
        [
            deploy_dir / "arbiter-docker",
            "install",
            "--to",
            str(install_dir),
            "--user",
            "arbiter",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "installing Arbiter" not in result.stdout
    assert "validating dependency wheelhouse" not in result.stdout
    assert (
        f"error: dependency wheelhouse validation failed: {install_dir / 'wheels'}\n"
        in (result.stderr)
    )
    assert "install aborted before writing/restarting the systemd service\n" in (
        result.stderr
    )
    assert (
        f"run {deploy_dir / 'arbiter-docker'} prepare, then rerun: sudo "
        f"{deploy_dir / 'arbiter-docker'} install\n"
    ) in result.stderr
    assert not (systemd_dir / "arbiter.service").exists()
    assert not systemctl_calls.exists()


def test_cli_deploy_docker_generated_helper_doctor_colors_tty_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env.pop("ARBITER_COLOR", None)

    result = _run_with_pty(
        [deploy_dir / "arbiter-docker", "doctor"], cwd=tmp_path, env=env
    )

    assert result.returncode == 0
    assert "\033[32mok\033[0m:" in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_rejects_docker_subnet_overlap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / "arbiter-server.yaml").chmod(0o640)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = info ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = network ] && [ "$2" = ls ]; then\n'
        "  if [ \"${3:-}\" = -q ]; then printf 'network-id\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = network ] && [ "$2" = inspect ]; then\n'
        "  printf 'existing-network 172.31.251.0/24 \\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "doctor"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "fail: Docker subnet 172.31.251.0/24 overlaps network "
        "existing-network (172.31.251.0/24)\n"
    ) in result.stdout


def test_cli_deploy_docker_generated_helper_preserves_requirements_after_bad_edit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    requirements_file = deploy_dir / "requirements.txt"
    original_requirements = requirements_file.read_text(encoding="utf-8")
    editor = tmp_path / "bad-editor"
    editor.write_text(
        "#!/usr/bin/env sh\n" "printf 'arbiter-suite\\n' > \"$1\"\n",
        encoding="utf-8",
    )
    editor.chmod(0o755)
    env = os.environ.copy()
    env["ARBITER_EDITOR"] = str(editor)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "edit-requirements"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert requirements_file.read_text(encoding="utf-8") == original_requirements
    assert (
        "requirement must be an exact package pin (name==version) or an "
        "absolute container path"
    ) in result.stdout
    assert f"error: requirements unchanged: {requirements_file}\n" in result.stderr


def test_cli_deploy_docker_generated_helper_sync_env_uses_env_bootstrap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "conf" / "arbiter-server.yaml").write_text(
        "arbiter: {}\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "arbiter-server-calls"
    fake_arbiter_server = fake_bin / "arbiter-server"
    fake_arbiter_server.write_text(
        "#!/usr/bin/env sh\n" 'printf \'%s\\n\' "$*" > "$ARBITER_SERVER_CALLS"\n',
        encoding="utf-8",
    )
    fake_arbiter_server.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SERVER_CALLS"] = str(calls_file)

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "sync-env"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert calls_file.read_text(encoding="utf-8") == (
        f"--config-dir {deploy_dir / 'conf'} --config-name arbiter-server "
        "env bootstrap\n"
    )


def test_cli_deploy_docker_update_preserves_local_config_and_env_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    deploy_dir.mkdir()
    config_file = deploy_dir / "conf" / "arbiter-server.yaml"
    config_file.parent.mkdir()
    config_file.write_text(
        "arbiter:\n"
        "  server:\n"
        "    host: ${oc.env:ARBITER_SERVER_HOST,127.0.0.1}\n"
        "  etc:\n"
        "    token: ${oc.env:EXISTING_TOKEN}\n"
        "    timeout: ${oc.env:NEW_TIMEOUT,30}\n",
        encoding="utf-8",
    )
    env_file = deploy_dir / "conf" / ".env"
    env_file.write_text("EXISTING_TOKEN=keep\n", encoding="utf-8")
    docker_env_file = deploy_dir / "docker.env"
    docker_env_file.write_text(
        "ARBITER_HOST_PORT=9000\n"
        "ARBITER_HOST_BIND=0.0.0.0\n"
        "ARBITER_PUBLIC_SCHEME=http\n"
        "ARBITER_DOCKER_SUBNET=172.31.251.0/24\n"
        "LOCAL_ONLY=value\n",
        encoding="utf-8",
    )
    requirements_file = deploy_dir / "requirements.txt"
    requirements_file.write_text("arbiter-suite==old\n", encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert config_file.read_text(encoding="utf-8").startswith("arbiter:\n")
    assert requirements_file.read_text(encoding="utf-8") == "arbiter-suite==old\n"
    assert env_file.read_text(encoding="utf-8") == "EXISTING_TOKEN=keep\n"
    assert docker_env_file.read_text(encoding="utf-8") == (
        "# Docker Compose settings for the Arbiter deployment.\n"
        "# These values control the container wrapper, not Arbiter runtime "
        "config.\n"
        "\n"
        "ARBITER_IMAGE=python:3.11-slim\n"
        "ARBITER_CONTAINER_NAME=arbiter-staging\n"
        f"ARBITER_CONTAINER_USER={_default_container_user()}\n"
        "ARBITER_RESTART=on-failure\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_CONFIG_NAME=arbiter-server\n"
        "ARBITER_REQUIREMENTS_FILE=./requirements.txt\n"
        "ARBITER_WHEELS_DIR=./wheels\n"
        "ARBITER_SERVER_DATA_DIR=./data/server\n"
        "ARBITER_PLUGIN_DATA_DIR=./data/plugins\n"
        "ARBITER_HOST_BIND=0.0.0.0\n"
        "ARBITER_HOST_PORT=9000\n"
        "ARBITER_CONTAINER_PORT=8075\n"
        "ARBITER_PUBLIC_SCHEME=https\n"
        "ARBITER_PUBLIC_BASE_URL=\n"
        "ARBITER_DOCKER_NETWORK_NAME=arbiter-staging\n"
        "ARBITER_DOCKER_BRIDGE_NAME=arbiter-stg0\n"
        "ARBITER_DOCKER_SUBNET=172.31.251.0/24\n"
        "\n"
        "# Extra local Compose values.\n"
        "LOCAL_ONLY=value\n"
    )
    assert (deploy_dir / "compose.yaml").exists()
    assert (deploy_dir / "arbiter-docker").exists()
    capsys.readouterr()


def test_cli_deploy_docker_update_creates_installed_default_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_installed_deploy_environment(monkeypatch)
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "arbiter-server==0.9.0.dev2\n"
        "arbiter-imap==0.9.0.dev2\n"
        "arbiter-smtp==0.9.0.dev2\n"
    )
    assert not (deploy_dir / "compose.override.yaml").exists()
    capsys.readouterr()


def test_cli_deploy_docker_update_reports_compact_noop_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert capsys.readouterr().out == f"Files already up to date: {deploy_dir}\n"


def test_cli_deploy_docker_update_repairs_stale_template_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    compose_file = deploy_dir / "compose.yaml"
    helper_file = deploy_dir / "arbiter-docker"
    compose_hash = hashlib.sha256(compose_file.read_bytes()).hexdigest()
    helper_hash = hashlib.sha256(helper_file.read_bytes()).hexdigest()
    manifest_path = deploy_dir / ".arbiter-deploy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["compose.yaml"]["sha256"] = "stale-compose"
    manifest["files"]["arbiter-docker"]["sha256"] = "stale-helper"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    output = capsys.readouterr().out
    assert f"skipped managed file with local edits: {compose_file}\n" not in output
    assert f"skipped managed file with local edits: {helper_file}\n" not in output
    assert f"template already up to date: {compose_file}\n" not in output
    assert f"template already up to date: {helper_file}\n" not in output
    assert f"wrote {manifest_path}\n" in output
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert repaired_manifest["files"]["compose.yaml"]["sha256"] == compose_hash
    assert repaired_manifest["files"]["arbiter-docker"]["sha256"] == helper_hash


def test_cli_deploy_docker_update_skips_modified_manifest_owned_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    compose_file = deploy_dir / "compose.yaml"
    compose_file.write_text("operator change\n", encoding="utf-8")
    manifest_path = deploy_dir / ".arbiter-deploy.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert compose_file.read_text(encoding="utf-8") == "operator change\n"
    assert manifest_path.read_text(encoding="utf-8") == original_manifest
    output = capsys.readouterr().out
    assert f"skipped managed file with local edits: {compose_file}\n" in output
    assert f"wrote {manifest_path}\n" not in output
    assert "Files already up to date:" not in output


def test_cli_deploy_docker_update_force_overwrites_modified_manifest_owned_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    compose_file = deploy_dir / "compose.yaml"
    helper_file = deploy_dir / "arbiter-docker"
    expected_compose = compose_file.read_text(encoding="utf-8")
    expected_helper = helper_file.read_text(encoding="utf-8")
    compose_file.write_text("operator compose change\n", encoding="utf-8")
    helper_file.write_text("operator helper change\n", encoding="utf-8")
    manifest_path = deploy_dir / ".arbiter-deploy.json"

    assert (
        main(["deploy", "docker", f"docker.dir={deploy_dir}", "update", "--force"]) == 0
    )

    assert compose_file.read_text(encoding="utf-8") == expected_compose
    assert helper_file.read_text(encoding="utf-8") == expected_helper
    _assert_posix_executable(helper_file)
    output = capsys.readouterr().out
    assert f"force updating managed file with local edits: {compose_file}\n" in output
    assert f"force updating managed file with local edits: {helper_file}\n" in output
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert (
        manifest["files"]["compose.yaml"]["sha256"]
        == hashlib.sha256(expected_compose.encode("utf-8")).hexdigest()
    )
    assert (
        manifest["files"]["arbiter-docker"]["sha256"]
        == hashlib.sha256(expected_helper.encode("utf-8")).hexdigest()
    )


def test_cli_deploy_docker_update_repairs_helper_executable_bit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        pytest.skip("POSIX executable-bit repair is not available")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    helper_file = deploy_dir / "arbiter-docker"
    helper_file.chmod(0o644)

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    _assert_posix_executable(helper_file)
    assert "Files already up to date:" not in capsys.readouterr().out


def test_cli_deploy_docker_init_rejects_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deploy_dir = tmp_path / "docker"

    assert (
        main(["deploy", "docker", f"docker.dir={deploy_dir}", "init", "--force"]) == 2
    )

    assert capsys.readouterr().err == (
        "Arbiter deploy error: --force is only supported with docker deploy update\n"
    )
    assert not deploy_dir.exists()


def test_cli_deploy_docker_helper_down_removes_orphans_only_for_managed_compose(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if os.name == "nt":
        pytest.skip("generated Docker helper is a POSIX shell script")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "conf" / "arbiter-server.yaml").write_text(
        "arbiter: {}\n",
        encoding="utf-8",
    )
    (deploy_dir / "conf" / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker-args.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n" 'printf "%s\\n" "$*" >> "$DOCKER_ARGS_LOG"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "DOCKER_ARGS_LOG": str(docker_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "down"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "down --remove-orphans" in docker_log.read_text(encoding="utf-8")

    (deploy_dir / "compose.yaml").write_text("operator change\n", encoding="utf-8")
    docker_log.write_text("", encoding="utf-8")

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "down"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert "down\n" in docker_log.read_text(encoding="utf-8")
    assert "--remove-orphans" not in docker_log.read_text(encoding="utf-8")
    assert result.stderr == (
        "not removing orphan containers automatically: compose.yaml has local edits\n"
        "pass --remove-orphans to down if you want to remove stale services\n"
    )


def test_cli_deploy_docker_helper_logs_include_timestamps(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if os.name == "nt":
        pytest.skip("generated Docker helper is a POSIX shell script")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=arbiter-suite==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (deploy_dir / "conf" / "arbiter-server.yaml").write_text(
        "arbiter: {}\n",
        encoding="utf-8",
    )
    (deploy_dir / "conf" / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker-args.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n" 'printf "%s\\n" "$*" >> "$DOCKER_ARGS_LOG"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "DOCKER_ARGS_LOG": str(docker_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "logs"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert "logs --timestamps -f\n" in docker_log.read_text(encoding="utf-8")


def test_cli_deploy_docker_reports_unknown_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["deploy", "docker", "docker.image=python", "init"]) == 2

    assert capsys.readouterr().err == (
        "Arbiter deploy error: unknown docker deploy override: docker.image\n"
    )


def test_cli_lists_plugins_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    monkeypatch.setattr(
        "arbiter_server.main.source_info",
        lambda: SimpleNamespace(commit="abc123", dirty=True),
    )

    assert main(["--config-dir", "/tmp", "plugins", "list", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == _expected_version_info(
        commit="abc123",
        dirty=True,
    )


def test_cli_version_prints_server_and_plugin_versions(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    monkeypatch.setattr(
        "arbiter_server.main.source_info",
        lambda: SimpleNamespace(commit="abc123", dirty=True),
    )

    assert main(["--config-dir", "/tmp", "version"]) == 0

    version_info = _expected_version_info(commit="abc123", dirty=True)
    server = cast(dict[str, str], version_info["server"])
    plugins = cast(list[dict[str, str]], version_info["plugins"])
    assert capsys.readouterr().out == (
        f"server {server['version']} (api {server['api_version']})\n"
        "source abc123 dirty\n"
        "plugins:\n"
        f"  {plugins[0]['name']} {plugins[0]['version']} "
        f"(server api {plugins[0]['server_api_version']})\n"
        f"  {plugins[1]['name']} {plugins[1]['version']} "
        f"(server api {plugins[1]['server_api_version']})\n"
    )


def test_cli_version_prints_known_deployment_scope(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.runtime_version_info",
        lambda service_plugins=None: {
            "server": {"version": "1.2.3", "api_version": "0.9"},
            "deployment_scope": "staged",
            "source": {"commit": None, "dirty": None, "build_time": None},
            "plugins": [],
        },
    )

    assert main(["--config-dir", "/tmp", "version"]) == 0

    assert "deployment scope staged\n" in capsys.readouterr().out


def test_cli_version_prints_installed_deployment_scope(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.runtime_version_info",
        lambda service_plugins=None: {
            "server": {"version": "1.2.3", "api_version": "0.9"},
            "deployment_scope": "installed",
            "source": {"commit": None, "dirty": None, "build_time": None},
            "plugins": [],
        },
    )

    assert main(["--config-dir", "/tmp", "version"]) == 0

    assert "deployment scope installed\n" in capsys.readouterr().out


def test_cli_version_can_print_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    monkeypatch.setattr(
        "arbiter_server.main.source_info",
        lambda: SimpleNamespace(commit="abc123", dirty=False),
    )

    assert main(["--config-dir", "/tmp", "version", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == _expected_version_info(
        commit="abc123",
        dirty=False,
    )


def test_cli_serve_subcommand_passes_config_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_calls: list[dict[str, object]] = []

    def fake_serve(**kwargs: object) -> int:
        serve_calls.append(kwargs)
        return 0

    monkeypatch.setattr("arbiter_server.main._run_serve", fake_serve)

    assert (
        main(
            [
                "--config-dir",
                "/tmp",
                "--config-name",
                "arbiter-server-local",
                "serve",
                "arbiter.server.bind.port=8075",
            ]
        )
        == 0
    )

    assert serve_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server-local",
            "overrides": ["arbiter.server.bind.port=8075"],
            "skip_runtime_permission_checks": False,
        },
    ]


def test_cli_accepts_config_args_after_subcommand(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["bootstrap", "arbiter", "--config-dir", str(tmp_path)]) == 0

    assert (tmp_path / "arbiter-server.yaml").exists()
    assert capsys.readouterr().out == (
        f"wrote {tmp_path / 'arbiter-server.yaml'}\n"
        f"wrote {tmp_path / 'arbiter' / 'server.yaml'}\n"
    )


def test_cli_config_check_subcommand_passes_config_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_calls: list[dict[str, object]] = []

    def fake_check(**kwargs: object) -> int:
        check_calls.append(kwargs)
        return 0

    monkeypatch.setattr("arbiter_server.main._run_config_check", fake_check)

    assert (
        main(
            [
                "--config-dir",
                "/tmp",
                "config",
                "check",
                "--live",
                "arbiter.server.bind.port=8075",
            ]
        )
        == 0
    )

    assert check_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server",
            "overrides": ["arbiter.server.bind.port=8075"],
            "live": True,
        },
    ]


def test_cli_config_check_reports_hydra_composition_errors_compactly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_config_dir = tmp_path / "missing"

    assert main(["--config-dir", str(missing_config_dir), "config", "check"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Arbiter config error: Primary config directory")
    assert str(missing_config_dir) in captured.err
    assert "Traceback" not in captured.err


def test_cli_config_check_prints_warnings_without_failing(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = object()
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **kwargs: cfg)
    monkeypatch.setattr(
        "arbiter_server.main.config_check_components",
        lambda cfg, **kwargs: iter(
            (
                ConfigCheckComponentReport(name="server"),
                ConfigCheckComponentReport(
                    name="imap",
                    warnings=(
                        ConfigCheckIssue(
                            message="IMAP account has no accessible configured folders",
                            account="primary",
                            policy="bot",
                        ),
                    ),
                ),
            ),
        ),
    )

    assert main(["--config-dir", "/tmp", "config", "check"]) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "server              | pass\n"
        "Plugins             | warn\n"
        "└── imap            | warn\n"
        "    └── primary/bot | warn | "
        "IMAP account has no accessible configured folders\n"
    )
    assert captured.err == ""


def test_cli_config_check_prints_aligned_report_after_components_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingStdout:
        def __init__(self) -> None:
            self.text = ""
            self.flush_count = 0

        def write(self, value: str) -> int:
            self.text += value
            return len(value)

        def flush(self) -> None:
            self.flush_count += 1

        def isatty(self) -> bool:
            return False

    stdout = RecordingStdout()
    cfg = object()
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **kwargs: cfg)
    monkeypatch.setattr(sys, "stdout", stdout)

    def fake_components(cfg: object, **kwargs: object):
        yield ConfigCheckComponentReport(name="server")
        assert stdout.text == ""
        yield ConfigCheckComponentReport(
            name="imap",
            warnings=(ConfigCheckIssue(message="still checking later component"),),
        )

    monkeypatch.setattr(
        "arbiter_server.main.config_check_components",
        fake_components,
    )

    assert (
        _run_config_check(
            config_dir="/tmp",
            config_name="arbiter-server",
            overrides=(),
        )
        == 0
    )
    assert stdout.text == (
        "server   | pass\n"
        "Plugins  | warn\n"
        "└── imap | warn\n"
        "- warn: still checking later component\n"
    )


def test_cli_config_check_animates_active_component_on_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingStdout:
        def __init__(self) -> None:
            self.text = ""

        def write(self, value: str) -> int:
            self.text += value
            return len(value)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    stdout = RecordingStdout()
    cfg = object()
    monkeypatch.setenv("ARBITER_COLOR", "never")
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **kwargs: cfg)
    monkeypatch.setattr(sys, "stdout", stdout)

    def fake_components(cfg: object, **kwargs: object):
        progress = cast(Callable[[str, str | None], None], kwargs["progress"])
        progress("smtp", "primary")
        assert "\r\033[2Ksmtp/primary: testing |" in stdout.text
        yield ConfigCheckComponentReport(name="smtp")

    monkeypatch.setattr(
        "arbiter_server.main.config_check_components",
        fake_components,
    )

    assert (
        _run_config_check(
            config_dir="/tmp",
            config_name="arbiter-server",
            overrides=(),
        )
        == 0
    )
    assert "\r\033[2Ksmtp/primary: testing |" in stdout.text
    assert "\r\033[2KPlugins  | pass\n└── smtp | pass\n" in stdout.text


def test_cli_config_check_can_color_statuses(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = object()
    monkeypatch.setenv("ARBITER_COLOR", "always")
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **kwargs: cfg)
    monkeypatch.setattr(
        "arbiter_server.main.config_check_components",
        lambda cfg, **kwargs: iter(
            (
                ConfigCheckComponentReport(name="server"),
                ConfigCheckComponentReport(
                    name="imap",
                    warnings=(
                        ConfigCheckIssue(
                            message="IMAP account has no accessible configured folders",
                            account="primary",
                            policy="bot",
                        ),
                    ),
                ),
                ConfigCheckComponentReport(
                    name="smtp",
                    errors=(
                        ConfigCheckIssue(
                            message="SMTP sent-copy destination missing",
                            account="primary",
                            policy="bot",
                        ),
                    ),
                ),
            ),
        ),
    )

    assert main(["--config-dir", "/tmp", "config", "check"]) == 1

    captured = capsys.readouterr()
    assert "\033[94mserver\033[0m" in captured.out
    assert "\033[94mimap\033[0m" in captured.out
    assert "\033[94msmtp\033[0m" in captured.out
    assert "\033[32mpass\033[0m" in captured.out
    assert "\033[33mwarn\033[0m" in captured.out
    assert "\033[31mfail\033[0m" in captured.out
    assert (
        "\033[94mserver\033[0m              | \033[32mpass\033[0m\n"
        "\033[94mPlugins\033[0m             | \033[31mfail\033[0m\n"
        "├── \033[94mimap\033[0m            | \033[33mwarn\033[0m\n"
        "│   └── primary/bot | \033[33mwarn\033[0m | "
        "\033[33mIMAP account has no accessible configured folders\033[0m\n"
    ) in captured.out
    assert (
        "└── \033[94msmtp\033[0m            | \033[31mfail\033[0m\n"
        "    └── primary/bot | \033[31mfail\033[0m | "
        "\033[31mSMTP sent-copy destination missing\033[0m\n"
    ) in captured.out
    assert captured.err == ""


def test_cli_config_show_subcommand_passes_config_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show_calls: list[dict[str, object]] = []

    def fake_show(**kwargs: object) -> int:
        show_calls.append(kwargs)
        return 0

    monkeypatch.setattr("arbiter_server.main._run_config_show", fake_show)

    assert (
        main(
            [
                "--config-dir",
                "/tmp",
                "config",
                "show",
                "--resolve",
                "arbiter.server.bind.port=8075",
            ]
        )
        == 0
    )

    assert show_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server",
            "overrides": ["arbiter.server.bind.port=8075"],
            "resolve": True,
        },
    ]


def test_cli_bootstrap_arbiter_uses_default_config_dir(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert main(["bootstrap", "arbiter"]) == 0

    config_dir = tmp_path / ".arbiter"
    assert (config_dir / "arbiter-server.yaml").exists()
    assert capsys.readouterr().out == (
        f"wrote {config_dir / 'arbiter-server.yaml'}\n"
        f"wrote {config_dir / 'arbiter' / 'server.yaml'}\n"
    )


def test_cli_bootstrap_arbiter_writes_main_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0

    config_file = config_dir / "arbiter-server.yaml"
    assert config_file.read_text(encoding="utf-8") == (
        "defaults:\n"
        "# Arbiter composes this config at startup from the defaults "
        "below.\n"
        "# Inspect the composed config with:\n"
        "#   arbiter-server --config-dir <dir> --config-name arbiter-server config show\n"
        "# Override composed values with Hydra overrides, for example:\n"
        "#   arbiter-server --config-dir <dir> serve arbiter.server.bind.port=8075\n"
        "# Optionally load a config-dir-relative dotenv file before composition:\n"
        "#   arbiter:\n"
        "#     env_file: local.env\n"
        "  - arbiter_app_config_schema\n"
        "  - arbiter: server\n"
        "  - _self_\n"
    )
    server_file = config_dir / "arbiter" / "server.yaml"
    assert server_file.read_text(encoding="utf-8") == (
        "# @package arbiter\n"
        "server:\n"
        "  name: arbiter\n"
        "  transport: https\n"
        "  bind:\n"
        "    scheme: https\n"
        "    host: 127.0.0.1\n"
        "    port: 8075\n"
        '    path: ""\n'
        "  public:\n"
        "    scheme: https\n"
        "  tls:\n"
        "    source: SELF_SIGNED\n"
        "deployment_scope: unknown\n"
        "discovery:\n"
        "  max_account_preview_limit: 25\n"
        "  max_operation_preview_limit: 25\n"
    )
    _assert_posix_mode(config_file, 0o640)
    _assert_posix_mode(server_file, 0o640)
    assert capsys.readouterr().out == (
        f"wrote {config_file}\n" f"wrote {server_file}\n"
    )

    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", str(config_dir), "config", "check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "server | fail\n"
        "- fail: config must define at least one service account before Arbiter can run\n"
        "  currently installed arbiter plugins: imap, smtp\n"
        "  use `arbiter-server --config-dir DIR bootstrap plugin PLUGIN account "
        "NAME` to create an account config\n"
    )
    assert captured.err == ""

    served: dict[str, object] = {}

    def fake_run_server(server: object, transport: object) -> None:
        served["server"] = server
        served["transport"] = transport

    monkeypatch.setattr("arbiter_server.main._run_server", fake_run_server)
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "--unsafe-skip-runtime-permission-checks",
                "serve",
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == (
        "Arbiter config error: config must define at least one service "
        "account before Arbiter can run\n"
        "  currently installed arbiter plugins: imap, smtp\n"
        "  use `arbiter-server --config-dir DIR bootstrap plugin PLUGIN account "
        "NAME` to create an account config\n"
    )
    assert served == {}


def test_cli_bootstrap_plugin_account_writes_service_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "smtp",
                "account",
                "personal_account",
            ]
        )
        == 0
    )

    account_file = (
        config_dir / "arbiter" / "account" / "smtp" / ("personal_account.yaml")
    )
    account_yaml = account_file.read_text(encoding="utf-8")
    assert "# @package arbiter.account.smtp.personal_account\n" in account_yaml
    assert "defaults:\n" in account_yaml
    assert "  - schema@_here_\n" in account_yaml
    assert "  - _self_\n" in account_yaml
    assert "# Human-facing summary shown by account listing tools.\n" in account_yaml
    assert "description: SMTP account for (${.from_email})\n" in account_yaml
    assert "# Operator guidance shown to agents during discovery.\n" in account_yaml
    assert 'guidance: ""\n' in account_yaml
    assert "# Matching policy generated alongside this account.\n" in account_yaml
    assert "policy: personal_account_policy\n" in account_yaml
    assert "host: smtp.example.com\n" in account_yaml
    assert "port: 587\n" in account_yaml
    assert "# Credentials are read from the Arbiter process environment.\n" in (
        account_yaml
    )
    assert "username: ${oc.env:SMTP_PERSONAL_ACCOUNT_USERNAME}\n" in account_yaml
    assert "password: ${oc.env:SMTP_PERSONAL_ACCOUNT_PASSWORD}\n" in account_yaml
    policy_file = (
        config_dir / "arbiter" / "policy" / "smtp" / "personal_account_policy.yaml"
    )
    policy_yaml = policy_file.read_text(encoding="utf-8")
    assert "# @package arbiter.policy.smtp.personal_account_policy\n" in policy_yaml
    assert "defaults:\n" in policy_yaml
    assert "  - schema@_here_\n" in policy_yaml
    assert "  - _self_\n" in policy_yaml
    assert "max_messages_per_minute: 30\n" in policy_yaml
    assert "allowed_domain_patterns: []\n" in policy_yaml
    assert "example.com" not in policy_yaml
    main_config = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert (
        "/arbiter/account/smtp@arbiter.account.smtp.personal_account" not in main_config
    )
    assert "/arbiter/policy/smtp@arbiter.policy.smtp.personal_account_policy" not in (
        main_config
    )
    assert capsys.readouterr().out == (
        f"wrote {account_file}\n"
        f"wrote {policy_file}\n"
        "\n"
        "Edit the generated account and policy files, then activate the account:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate account smtp personal_account\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )


def test_cli_bootstrap_plugin_account_refuses_existing_policy_without_partial_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()
    policy_file = config_dir / "arbiter" / "policy" / "smtp" / "primary_policy.yaml"
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text("existing: true\n", encoding="utf-8")

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "smtp",
                "account",
                "primary",
            ]
        )
        == 1
    )

    account_file = config_dir / "arbiter" / "account" / "smtp" / "primary.yaml"
    assert not account_file.exists()
    assert policy_file.read_text(encoding="utf-8") == "existing: true\n"
    assert capsys.readouterr().err == (
        "Arbiter bootstrap error: refusing to overwrite existing file: "
        f"{policy_file}\n"
    )


def test_cli_bootstrap_plugin_policy_writes_service_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "smtp",
                "policy",
                "readonly",
            ]
        )
        == 0
    )

    policy_file = config_dir / "arbiter" / "policy" / "smtp" / "readonly.yaml"
    policy_yaml = policy_file.read_text(encoding="utf-8")
    assert "# @package arbiter.policy.smtp.readonly\n" in policy_yaml
    assert "defaults:\n" in policy_yaml
    assert "  - schema@_here_\n" in policy_yaml
    assert "  - _self_\n" in policy_yaml
    assert "max_messages_per_minute: 30\n" in policy_yaml
    assert "allowed_domain_patterns: []\n" in policy_yaml
    assert "example.com" not in policy_yaml
    main_config = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "/arbiter/policy/smtp@arbiter.policy.smtp.readonly" not in main_config
    assert capsys.readouterr().out == (
        f"wrote {policy_file}\n"
        "\n"
        f"To activate the generated policy, add this to {config_dir / 'arbiter-server.yaml'}:\n"
        "defaults:\n"
        "  - arbiter/policy:\n"
        "    - smtp/readonly\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )


def test_cli_config_activate_account_activates_matching_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "smtp",
                "account",
                "personal_account",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "account",
                "smtp",
                "personal_account",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "  - arbiter/account:\n" in config_yaml
    assert "    - smtp/personal_account\n" in config_yaml
    assert "  - arbiter/policy:\n" in config_yaml
    assert "    - smtp/personal_account_policy\n" in config_yaml
    cfg = compose_config(config_dir=config_dir, config_name="arbiter-server")
    assert cfg.arbiter.account.smtp.personal_account.policy == "personal_account_policy"
    assert (
        cfg.arbiter.policy.smtp.personal_account_policy.limits.max_recipients_per_message
        == 10
    )
    assert capsys.readouterr().out == f"updated {config_dir / 'arbiter-server.yaml'}\n"


def test_cli_config_activate_account_can_alias_policy_file_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    account_dir = config_dir / "arbiter" / "account" / "smtp"
    policy_dir = config_dir / "arbiter" / "policy" / "smtp"
    account_dir.mkdir(parents=True)
    policy_dir.mkdir(parents=True)
    (account_dir / "bot.yaml").write_text(
        "# @package arbiter.account.smtp.bot\n"
        "defaults:\n"
        "  - schema@_here_\n"
        "  - _self_\n"
        "policy: bot_policy\n"
        "host: smtp.example.com\n"
        "authenticate: false\n",
        encoding="utf-8",
    )
    (policy_dir / "bot.yaml").write_text(
        "# @package arbiter.policy.smtp.bot_policy\n"
        "defaults:\n"
        "  - schema@_here_\n"
        "  - _self_\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "account",
                "smtp",
                "bot",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "  - arbiter/account:\n" in config_yaml
    assert "    - smtp/bot\n" in config_yaml
    assert "  - arbiter/policy:\n" in config_yaml
    assert "    - smtp/bot\n" in config_yaml
    cfg = compose_config(config_dir=config_dir, config_name="arbiter-server")
    assert cfg.arbiter.account.smtp.bot.policy == "bot_policy"
    assert cfg.arbiter.policy.smtp.bot_policy.limits.max_messages_per_minute is None


def test_cli_config_deactivate_account_deactivates_unused_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "smtp",
                "account",
                "primary",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "account",
                "smtp",
                "primary",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "deactivate",
                "account",
                "smtp",
                "primary",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "  - arbiter/account:\n" not in config_yaml
    assert "  - arbiter/policy:\n" not in config_yaml
    assert capsys.readouterr().out == f"updated {config_dir / 'arbiter-server.yaml'}\n"


def test_cli_config_deactivate_account_keeps_shared_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    account_dir = config_dir / "arbiter" / "account" / "smtp"
    policy_dir = config_dir / "arbiter" / "policy" / "smtp"
    account_dir.mkdir(parents=True)
    policy_dir.mkdir(parents=True)
    for account_name in ("primary", "secondary"):
        (account_dir / f"{account_name}.yaml").write_text(
            f"# @package arbiter.account.smtp.{account_name}\n"
            "defaults:\n"
            "  - schema@_here_\n"
            "  - _self_\n"
            "policy: shared\n"
            "host: smtp.example.com\n"
            "authenticate: false\n",
            encoding="utf-8",
        )
    (policy_dir / "shared.yaml").write_text(
        "# @package arbiter.policy.smtp.shared\n"
        "defaults:\n"
        "  - schema@_here_\n"
        "  - _self_\n",
        encoding="utf-8",
    )
    for account_name in ("primary", "secondary"):
        assert (
            main(
                [
                    "--config-dir",
                    str(config_dir),
                    "config",
                    "activate",
                    "account",
                    "smtp",
                    account_name,
                ]
            )
            == 0
        )
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "deactivate",
                "account",
                "smtp",
                "primary",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "    - smtp/primary\n" not in config_yaml
    assert "    - smtp/secondary\n" in config_yaml
    assert "    - smtp/shared\n" in config_yaml
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "deactivate",
                "account",
                "smtp",
                "secondary",
            ]
        )
        == 0
    )
    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "    - smtp/secondary\n" not in config_yaml
    assert "    - smtp/shared\n" not in config_yaml


def test_cli_bootstrap_plugin_imap_account_writes_service_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "imap",
                "account",
                "bot",
            ]
        )
        == 0
    )

    account_file = config_dir / "arbiter" / "account" / "imap" / "bot.yaml"
    account_yaml = account_file.read_text(encoding="utf-8")
    assert "# @package arbiter.account.imap.bot\n" in account_yaml
    assert "defaults:\n" in account_yaml
    assert "  - schema@_here_\n" in account_yaml
    assert "  - _self_\n" in account_yaml
    assert "# Human-facing summary shown by account listing tools.\n" in account_yaml
    assert "description: IMAP account for (${.username})\n" in account_yaml
    assert "# Operator guidance shown to agents during discovery.\n" in account_yaml
    assert 'guidance: ""\n' in account_yaml
    assert "# Matching policy generated alongside this account.\n" in account_yaml
    assert "policy: bot_policy\n" in account_yaml
    assert "host: imap.example.com\n" in account_yaml
    assert "port: 993\n" in account_yaml
    assert "# Credentials are read from the Arbiter process environment.\n" in (
        account_yaml
    )
    assert "username: ${oc.env:IMAP_BOT_ACCOUNT_USERNAME}\n" in account_yaml
    assert "password: ${oc.env:IMAP_BOT_ACCOUNT_PASSWORD}\n" in account_yaml
    assert "default_folder: INBOX\n" in account_yaml
    assert "folders:\n" in account_yaml
    assert "  INBOX:\n" in account_yaml
    assert "  Drafts:\n" in account_yaml
    assert "    kind: DRAFTS\n" in account_yaml
    policy_file = config_dir / "arbiter" / "policy" / "imap" / "bot_policy.yaml"
    policy_yaml = policy_file.read_text(encoding="utf-8")
    assert "# @package arbiter.policy.imap.bot_policy\n" in policy_yaml
    assert "defaults:\n" in policy_yaml
    assert "  - schema@_here_\n" in policy_yaml
    assert "  - _self_\n" in policy_yaml
    assert (
        "# Explicit folder access baseline. This default-open variant exposes all server folders first, then lets you add deny rules below.\n"
        in (policy_yaml)
    )
    assert "folder_access:\n" in policy_yaml
    assert '    - allow_glob: "*"\n' in policy_yaml
    assert "operation_defaults:\n" in policy_yaml
    assert "  read: allow\n" in policy_yaml
    assert "  search: allow\n" in policy_yaml
    assert "  move: false\n" in policy_yaml
    assert "  delete: deny\n" in policy_yaml
    assert "SEEN: read_only\n" in policy_yaml
    assert "user_flags: {}\n" in policy_yaml
    assert (
        "folders:\n"
        "  Sent:\n"
        "    folder_append: allow\n"
        "    system_flags:\n"
        "      SEEN: read_write\n"
        "  Drafts:\n"
        "    folder_append: allow\n"
        "    system_flags:\n"
        "      SEEN: read_write\n"
        "      DRAFT: read_write\n" in policy_yaml
    )
    main_config = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "/arbiter/account/imap@arbiter.account.imap.bot" not in main_config
    assert "/arbiter/policy/imap@arbiter.policy.imap.bot_policy" not in main_config
    assert capsys.readouterr().out == (
        f"wrote {account_file}\n"
        f"wrote {policy_file}\n"
        "\n"
        "Edit the generated account and policy files, then activate the account:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate account imap bot\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )


def test_cli_bootstrap_plugin_imap_policy_lists_variants(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--config-dir",
                str(tmp_path),
                "bootstrap",
                "plugin",
                "imap",
                "policy",
                "--list-variants",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == (
        "default-closed\tdeny all folders first, then add allow rules\n"
        "default-open\tallow all folders first, then add deny rules\n"
    )


def test_cli_bootstrap_plugin_imap_policy_writes_selected_variant(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "arbiter"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "plugin",
                "imap",
                "policy",
                "locked_down",
                "--variant",
                "default-closed",
            ]
        )
        == 0
    )

    policy_file = config_dir / "arbiter" / "policy" / "imap" / "locked_down.yaml"
    policy_yaml = policy_file.read_text(encoding="utf-8")
    assert '    - deny_glob: "*"\n' in policy_yaml
    assert "operation_defaults:\n" in policy_yaml
    assert "folder_append: deny\n" in policy_yaml
    assert "      SEEN: read_write\n" in policy_yaml
    assert "      DRAFT: read_write\n" in policy_yaml


def _test_service_plugins() -> list[ServicePlugin]:
    return [
        SMTPServicePlugin(),
        IMAPServicePlugin(),
    ]


def _app_config_with_smtp() -> AppConfig:
    return AppConfig(
        arbiter=ArbiterConfig(
            account={
                "smtp": {
                    "primary": SMTPConfig(
                        description="Bot-owned account for automated email tasks.",
                        policy="bot",
                    )
                },
                "imap": {},
            },
            policy={
                "smtp": {"bot": SMTPServicePolicyConfig()},
                "imap": {},
            },
        )
    )


def _app_config_with_smtp_imap() -> AppConfig:
    return AppConfig(
        arbiter=ArbiterConfig(
            account={
                "smtp": {
                    "primary": SMTPConfig(
                        description="Bot-owned account for automated email tasks.",
                        policy="bot",
                    )
                },
                "imap": {
                    "primary": IMAPConfig(
                        default_folder="INBOX",
                        folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                    )
                },
            },
            policy={
                "smtp": {"bot": SMTPServicePolicyConfig()},
                "imap": {
                    "bot": IMAPAccessPolicyConfig(
                        folder_access=IMAPFolderAccessConfig(
                            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
                        )
                    )
                },
            },
        )
    )


def test_log_startup_summary_includes_safe_runtime_context(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _app_config_with_smtp()
    cast(SMTPConfig, cfg.arbiter.account["smtp"]["primary"]).password = "super-secret"

    monkeypatch.setattr("arbiter_server.main.arbiter_server_version", lambda: "1.2.3")
    caplog.set_level(logging.INFO, logger="arbiter_server.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "Arbiter starting version=1.2.3" in message
    assert "deployment_scope=unknown" in message
    assert "transport=https" in message
    assert "bind=127.0.0.1:8075" in message
    assert "url=https://127.0.0.1:8075" in message
    assert "services=smtp" in message
    assert "service_accounts=smtp:primary" in message
    assert "super-secret" not in message
    assert "agent@example.com" not in message


def test_log_startup_summary_uses_public_base_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _app_config_with_smtp()
    cfg.arbiter.server.bind.host = "0.0.0.0"
    cfg.arbiter.server.bind.port = 8075
    cfg.arbiter.server.public.base_url = "https://arbiter.example.test"
    caplog.set_level(logging.INFO, logger="arbiter_server.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "bind=0.0.0.0:8075" in message
    assert "url=https://arbiter.example.test" in message


def test_server_urls_default_to_loopback_base_url() -> None:
    cfg = _app_config_with_smtp()
    cfg.arbiter.server.bind.host = "0.0.0.0"
    cfg.arbiter.server.bind.port = 8075

    assert _artifact_base_url(cfg) == "https://127.0.0.1:8075/api/v1/artifacts"


def test_empty_public_base_url_override_is_invalid() -> None:
    cfg = _app_config_with_smtp()
    cfg.arbiter.server.bind.host = "0.0.0.0"
    cfg.arbiter.server.bind.port = 8075
    cfg.arbiter.server.public.base_url = " "

    with pytest.raises(ValueError, match="public.base_url must be non-empty"):
        _artifact_base_url(cfg)


def test_cleartext_public_base_url_override_is_invalid() -> None:
    cfg = _app_config_with_smtp()
    cfg.arbiter.server.public.base_url = "http://127.0.0.1:8075"

    with pytest.raises(ValueError, match="public.base_url must use https"):
        _artifact_base_url(cfg)


def test_artifact_base_url_uses_public_base_url() -> None:
    cfg = _app_config_with_smtp()
    cfg.arbiter.server.bind.host = "0.0.0.0"
    cfg.arbiter.server.bind.port = 8075
    cfg.arbiter.server.public.base_url = "https://arbiter.example.test/root/"

    assert (
        _artifact_base_url(cfg) == "https://arbiter.example.test/root/api/v1/artifacts"
    )


def test_self_signed_tls_files_are_generated_under_storage_root(
    tmp_path: Path,
) -> None:
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            storage=StorageConfig(server_data_dir=str(tmp_path / "server")),
        )
    )

    cert_file, key_file = _server_tls_files(cfg, generate_self_signed=True)

    assert cert_file == tmp_path / "server" / "tls" / "arbiter-self-signed.crt"
    assert key_file == tmp_path / "server" / "tls" / "arbiter-self-signed.key"
    assert "BEGIN CERTIFICATE" in cert_file.read_text(encoding="utf-8")
    assert "BEGIN PRIVATE KEY" in key_file.read_text(encoding="utf-8")
    if os.name != "nt":
        assert key_file.stat().st_mode & 0o077 == 0


def test_self_signed_tls_source_accepts_enum_value_strings(tmp_path: Path) -> None:
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            storage=StorageConfig(server_data_dir=str(tmp_path / "server")),
        )
    )
    cfg.arbiter.server.tls.source = "self-signed"  # type: ignore[assignment]

    cert_file, key_file = _server_tls_files(cfg, generate_self_signed=False)

    assert cert_file == (tmp_path / "server" / "tls" / "arbiter-self-signed.crt")
    assert key_file == (tmp_path / "server" / "tls" / "arbiter-self-signed.key")


def test_cert_files_tls_source_requires_existing_files(tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg.arbiter.server.tls.source = ServerTlsSource.CERT_FILES
    cfg.arbiter.server.tls.cert_file = str(tmp_path / "cert.pem")
    cfg.arbiter.server.tls.key_file = str(tmp_path / "key.pem")

    with pytest.raises(ValueError, match="TLS certificate file not found"):
        _server_tls_files(cfg, generate_self_signed=False)


def test_build_app_wires_smtp_sent_copy_to_matching_imap_account() -> None:
    smtp_sends: list[dict[str, object]] = []
    imap_appends: list[dict[str, object]] = []
    cfg = _app_config_with_smtp_imap()
    cast(IMAPConfig, cfg.arbiter.account["imap"]["primary"]).folders["Sent"] = (
        IMAPFolderConfig(description="Sent mail", kind=IMAPFolderKind.SENT)
    )
    cast(IMAPAccessPolicyConfig, cfg.arbiter.policy["imap"]["bot"]).folders["Sent"] = (
        IMAPFolderOperationPolicyConfig(
            folder_append=IMAPOperationDecision.allow,
            system_flags=IMAPSystemFlagsPolicyConfig(SEEN=IMAPFlagMode.read_write),
        )
    )

    class FakeSMTPClient:
        def send(
            self,
            message_bytes: bytes,
            sender: str,
            recipients: list[str],
        ) -> None:
            smtp_sends.append(
                {
                    "message_bytes": message_bytes,
                    "sender": sender,
                    "recipients": recipients,
                }
            )

        def test_connection(self) -> None:
            return None

    class FakeIMAPClient:
        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (r"\Seen",),
        ) -> None:
            imap_appends.append(
                {
                    "folder": folder,
                    "message_bytes": message_bytes,
                    "flags": tuple(flags),
                }
            )

    app = build_app(
        cfg,
        service_plugins=_test_service_plugins(),
        runtime_dependencies={
            "smtp_client_factory": lambda config: FakeSMTPClient(),
            "imap_client_factory": lambda config: FakeIMAPClient(),
        },
    )

    result = app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert len(smtp_sends) == 1
    assert result.sent_copy == {
        "status": "saved",
        "account": "primary",
        "folder": "Sent",
    }
    assert len(imap_appends) == 1
    assert imap_appends[0]["folder"] == "Sent"
    assert imap_appends[0]["flags"] == (r"\Seen",)
    assert b"Subject: Hello" in cast(bytes, imap_appends[0]["message_bytes"])


def test_build_app_sent_copy_does_not_infer_folder_from_different_account() -> None:
    smtp_sends: list[dict[str, object]] = []
    imap_appends: list[dict[str, object]] = []
    cfg = _app_config_with_smtp_imap()
    cfg.arbiter.account["imap"]["other"] = IMAPConfig(
        default_folder="INBOX",
        folders={
            "Sent": IMAPFolderConfig(description="Sent mail", kind=IMAPFolderKind.SENT)
        },
    )

    class FakeSMTPClient:
        def send(
            self,
            message_bytes: bytes,
            sender: str,
            recipients: list[str],
        ) -> None:
            smtp_sends.append(
                {
                    "message_bytes": message_bytes,
                    "sender": sender,
                    "recipients": recipients,
                }
            )

        def test_connection(self) -> None:
            return None

    class FakeIMAPClient:
        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (r"\Seen",),
        ) -> None:
            imap_appends.append(
                {
                    "folder": folder,
                    "message_bytes": message_bytes,
                    "flags": tuple(flags),
                }
            )

    app = build_app(
        cfg,
        service_plugins=_test_service_plugins(),
        runtime_dependencies={
            "smtp_client_factory": lambda config: FakeSMTPClient(),
            "imap_client_factory": lambda config: FakeIMAPClient(),
        },
    )

    result = app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert len(smtp_sends) == 1
    assert imap_appends == []
    assert result.sent_copy == {
        "status": "skipped",
        "account": "primary",
        "reason": "IMAP account has no folder configured with kind=SENT: primary",
        "error_type": "ValueError",
    }


def test_run_server_preserves_hydra_logging_for_http_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured["run_kwargs"] = kwargs

    fake_uvicorn = ModuleType("uvicorn")
    setattr(fake_uvicorn, "run", fake_run)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    fake_server = SimpleNamespace(
        app="native-http-app",
        host="127.0.0.1",
        port=8075,
        ssl_certfile="/tls/cert.pem",
        ssl_keyfile="/tls/key.pem",
    )

    _run_server(cast(Any, fake_server), cast(Any, "https"))

    assert captured["app"] == "native-http-app"
    assert captured["run_kwargs"] == {
        "host": "127.0.0.1",
        "port": 8075,
        "ssl_certfile": "/tls/cert.pem",
        "ssl_keyfile": "/tls/key.pem",
        "log_config": None,
    }


def test_run_server_rejects_non_https_transport() -> None:
    with pytest.raises(ValueError, match="unsupported Arbiter HTTPS transport"):
        _run_server(cast(Any, SimpleNamespace()), cast(Any, "websocket"))
