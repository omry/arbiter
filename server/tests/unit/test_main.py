import json
import logging
import os
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
    _config_check_output_width,
    _display_config_path,
    _config_check_tree_lines,
    _run_config_check,
    _run_config_show,
    _run_server,
    _live_account_test_result,
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

_SUPPORTS_POSIX_FILE_MODES = os.name == "posix"


def _assert_posix_mode(path: Path, expected: int) -> None:
    if not _SUPPORTS_POSIX_FILE_MODES:
        return
    assert path.stat().st_mode & 0o777 == expected


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
        "server              │ pass\n"
        "Plugins             │ pass\n"
        "├── smtp            │ pass\n"
        "│   └── primary/bot │ pass │ account/policy pair valid\n"
        "└── imap            │ pass\n"
        "    └── primary/bot │ pass │ account/policy pair valid"
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
        "server              │ pass\n"
        "Plugins             │ pass\n"
        "└── fake            │ pass\n"
        "    └── primary/bot │ pass │ account/policy pair valid"
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
        "server         │ pass\n"
        "Plugins        │ warn\n"
        "└── smtp       │ warn\n"
        "    ├── a/p    │ pass │ short name\n"
        "    └── long/p │ warn │ long name"
    )


def test_config_check_tree_lines_wrap_messages_under_message_column() -> None:
    lines = _config_check_tree_lines(
        (
            ConfigCheckComponentReport(
                name="imap",
                errors=(
                    ConfigCheckIssue(
                        message=(
                            "KeyError raised while resolving interpolation: "
                            "\"Environment variable 'IMAP_PERSONAL_ACCOUNT_HOST' "
                            'not found"\n'
                            "full_key: arbiter.account.imap.personal.host\n"
                            "object_type=IMAPConfig"
                        ),
                        account="personal",
                        policy="personal_policy",
                    ),
                ),
            ),
        ),
        width=72,
    )

    assert all(len(line) <= 72 for line in lines)
    assert lines == (
        "Plugins                          │ fail",
        "└── imap                         │ fail",
        "    └── personal/personal_policy │ fail │ KeyError raised while",
        "                                          resolving interpolation:",
        '                                          "Environment variable',
        "                                          'IMAP_PERSONAL_ACCOUNT_HOST'",
        '                                          not found"',
        "                                          full_key: arbiter.account.imap",
        "                                          .personal.host",
        "                                          object_type=IMAPConfig",
    )


def test_config_check_output_width_uses_columns_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NonTTY:
        def isatty(self) -> bool:
            return False

    monkeypatch.setenv("COLUMNS", "72")

    assert _config_check_output_width(NonTTY()) == 72


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
        "server              │ pass\n"
        "Plugins             │ fail\n"
        "└── fake            │ fail\n"
        "    ├── primary/bot │ pass │ account/policy pair valid\n"
        "    └── primary/bot │ fail │ bad fake policy"
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
        "server                  │ pass\n"
        "Plugins                 │ fail\n"
        "└── fake                │ fail\n"
        "    └── primary/missing │ fail │ account references an unknown policy"
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
        "server              │ pass\n"
        "Plugins             │ fail\n"
        "└── fake            │ fail\n"
        "    └── primary/bot │ fail │ authentication failed"
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
        "server              │ pass\n"
        "Plugins             │ fail\n"
        "└── fake            │ fail\n"
        "    └── primary/bot │ fail │ "
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
        "    └── primary/bot │ fail │ "
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
        "    └── primary/bot │ fail │ "
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
        "Plugins             │ pass",
        "└── fake            │ pass",
        "    └── primary/bot │ pass │ live account check passed",
    )
    assert progress_calls == [("server", None), ("fake", None), ("fake", "primary")]


def test_live_account_warning_status_renders_as_warn() -> None:
    result = _live_account_test_result(
        account_name="primary",
        account_config=SimpleNamespace(policy="bot"),
        result={
            "status": "warning",
            "message": "optional capability unavailable",
        },
    )

    assert result == ConfigCheckAccountResult(
        account="primary",
        policy="bot",
        status="warn",
        message="optional capability unavailable",
    )


def test_runnable_config_requires_at_least_one_service_account() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "config must define at least one service account[\\s\\S]*"
            "currently installed arbiter plugins: imap, smtp[\\s\\S]*"
            "bootstrap --plugin PLUGIN --account NAME"
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
    assert main(["--config-dir", str(tmp_path), "bootstrap", "--server"]) == 0
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


def test_cli_env_check_treats_env_references_with_defaults_as_optional(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IMAP_PRIMARY_ACCOUNT_PORT", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    imap:\n"
        "      primary:\n"
        "        port: ${oc.env:IMAP_PRIMARY_ACCOUNT_PORT,993}\n",
        encoding="utf-8",
    )
    (tmp_path / "local.env").write_text("", encoding="utf-8")

    assert main(["--config-dir", str(tmp_path), "env", "check"]) == 0

    assert capsys.readouterr().out == "env ok: 0 variables satisfied\n"


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
        "        port: ${oc.env:SMTP_PRIMARY_ACCOUNT_PORT,587}\n"
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
        "SMTP_PRIMARY_ACCOUNT_PORT=2525\n"
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
        "SMTP_PRIMARY_ACCOUNT_PORT=2525\n"
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


def test_cli_env_bootstrap_comments_env_references_with_defaults(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IMAP_PRIMARY_ACCOUNT_HOST", raising=False)
    monkeypatch.delenv("IMAP_PRIMARY_ACCOUNT_PORT", raising=False)
    monkeypatch.delenv("IMAP_PRIMARY_ACCOUNT_LABEL", raising=False)
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  account:\n"
        "    imap:\n"
        "      primary:\n"
        "        host: ${oc.env:IMAP_PRIMARY_ACCOUNT_HOST}\n"
        "        port: ${oc.env:IMAP_PRIMARY_ACCOUNT_PORT,993}\n"
        '        label: ${oc.env:IMAP_PRIMARY_ACCOUNT_LABEL,"mail, primary"}\n',
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text("", encoding="utf-8")

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    assert env_file.read_text(encoding="utf-8") == (
        "# arbiter-imap\n"
        "IMAP_PRIMARY_ACCOUNT_HOST=\n"
        "# IMAP_PRIMARY_ACCOUNT_PORT=993\n"
        "# IMAP_PRIMARY_ACCOUNT_LABEL=mail, primary\n"
    )
    assert capsys.readouterr().out == f"wrote {env_file}\n"


def test_cli_env_bootstrap_reports_reploy_display_path_for_current_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(tmp_path))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "conf")
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  env_file: .env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# arbiter-smtp\n" "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    assert capsys.readouterr().out == "env file already up to date: conf/.env\n"


def test_cli_config_show_reports_reploy_display_path_for_unreadable_main_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path.resolve() / "arbiter-server.yaml"
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "conf")
    real_exists = Path.exists

    def exists(path: Path) -> bool:
        if path == config_file:
            raise PermissionError("permission denied")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    assert main(["--config-dir", str(tmp_path), "config", "show"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter config error: cannot read config file: "
        "conf/arbiter-server.yaml: permission denied\n"
    )


def test_cli_config_activate_reports_reploy_display_path_for_unreadable_main_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path.resolve()
    config_file = config_dir / "arbiter-server.yaml"
    account_file = config_dir / "arbiter" / "account" / "smtp" / "bot.yaml"
    policy_file = config_dir / "arbiter" / "policy" / "smtp" / "bot_policy.yaml"
    account_file.parent.mkdir(parents=True)
    policy_file.parent.mkdir(parents=True)
    account_file.write_text("policy: bot_policy\n", encoding="utf-8")
    policy_file.write_text("allow: true\n", encoding="utf-8")
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(config_dir))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "conf")
    real_exists = Path.exists

    def exists(path: Path) -> bool:
        if path == config_file:
            raise PermissionError("permission denied")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "--plugin",
                "smtp",
                "--account",
                "bot",
            ]
        )
        == 1
    )

    assert capsys.readouterr().err == (
        "Arbiter config error: cannot read config file: "
        "conf/arbiter-server.yaml: permission denied\n"
    )


def test_cli_env_bootstrap_reports_reploy_display_path_for_unreadable_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "arbiter-server.yaml"
    env_file = tmp_path / ".env"
    config_file.write_text(
        "arbiter:\n"
        "  env_file: .env\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "conf")
    real_exists = Path.exists

    def exists(path: Path) -> bool:
        if path == env_file:
            raise PermissionError("permission denied")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter env error: cannot read env file: conf/.env: permission denied\n"
    )


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


def test_cli_env_bootstrap_reports_reploy_display_path_for_written_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_PRIMARY_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(tmp_path))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "conf")
    (tmp_path / "arbiter-server.yaml").write_text(
        "arbiter:\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

    assert capsys.readouterr().out == "wrote conf/.env\n"


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
    assert main(["bootstrap", "--server", "--config-dir", str(tmp_path)]) == 0

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
        "server              │ pass\n"
        "Plugins             │ warn\n"
        "└── imap            │ warn\n"
        "    └── primary/bot │ warn │ "
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
        "server   │ pass\n"
        "Plugins  │ warn\n"
        "└── imap │ warn\n"
        "- warn: still checking later component\n"
    )


def test_cli_config_check_uses_ascii_tree_for_limited_stream_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LimitedEncodingStdout:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.text = ""

        def write(self, value: str) -> int:
            value.encode(self.encoding)
            self.text += value
            return len(value)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

    stdout = LimitedEncodingStdout()
    cfg = object()
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **kwargs: cfg)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(
        "arbiter_server.main.config_check_components",
        lambda cfg, **kwargs: iter(
            (
                ConfigCheckComponentReport(name="server"),
                ConfigCheckComponentReport(
                    name="smtp",
                    account_results=(
                        ConfigCheckAccountResult(
                            account="primary",
                            policy="primary_policy",
                            status="pass",
                            message="account/policy pair valid",
                        ),
                    ),
                ),
            ),
        ),
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
        "server                         | pass\n"
        "Plugins                        | pass\n"
        "`-- smtp                       | pass\n"
        "    `-- primary/primary_policy | pass | account/policy pair valid\n"
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
    assert "\r\033[2KPlugins  │ pass\n└── smtp │ pass\n" in stdout.text


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
    assert "\033[32mserver\033[0m" in captured.out
    assert "\033[33mimap\033[0m" in captured.out
    assert "\033[31msmtp\033[0m" in captured.out
    assert "\033[32mpass\033[0m" in captured.out
    assert "\033[33mwarn\033[0m" in captured.out
    assert "\033[31mfail\033[0m" in captured.out
    assert (
        "\033[32mserver\033[0m             \033[90m │ \033[0m"
        "\033[32mpass\033[0m\n"
        "\033[31mPlugins\033[0m            \033[90m │ \033[0m"
        "\033[31mfail\033[0m\n"
        "\033[90m├── \033[0m\033[33mimap\033[0m           "
        "\033[90m │ \033[0m\033[33mwarn\033[0m\n"
        "\033[90m│   └── \033[0m\033[33mprimary/bot\033[0m"
        "\033[90m │ \033[0m\033[33mwarn\033[0m\033[90m │ \033[0m"
        "\033[33mIMAP account has no accessible configured folders\033[0m\n"
    ) in captured.out
    assert (
        "\033[90m└── \033[0m\033[31msmtp\033[0m           "
        "\033[90m │ \033[0m\033[31mfail\033[0m\n"
        "\033[90m    └── \033[0m\033[31mprimary/bot\033[0m"
        "\033[90m │ \033[0m\033[31mfail\033[0m\033[90m │ \033[0m"
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
                "--package",
                "arbiter.server.public.base_url",
                "--value",
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
            "package": "arbiter.server.public.base_url",
            "value": True,
        },
    ]


def test_run_config_show_prints_package_value(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_compose_config(**_kwargs: object) -> object:
        return OmegaConf.create(
            {
                "arbiter": {
                    "server": {
                        "public": {
                            "scheme": "https",
                            "host": "arbiter.example.test",
                            "port": "443",
                            "base_url": "${.scheme}://${.host}:${.port}",
                        }
                    }
                }
            }
        )

    monkeypatch.setattr("arbiter_server.main.compose_config", fake_compose_config)

    assert (
        _run_config_show(
            config_dir="/tmp",
            config_name="arbiter-server",
            overrides=[],
            resolve=True,
            package="arbiter.server.public.base_url",
            value=True,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == "https://arbiter.example.test:443\n"
    assert captured.err == ""


def test_run_config_show_prints_scalar_package(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.compose_config",
        lambda **_kwargs: OmegaConf.create(
            {"arbiter": {"server": {"public": {"host": "127.0.0.1"}}}}
        ),
    )

    assert (
        _run_config_show(
            config_dir="/tmp",
            config_name="arbiter-server",
            overrides=[],
            resolve=True,
            package="arbiter.server.public.host",
            value=False,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == "127.0.0.1\n"
    assert captured.err == ""


def test_run_config_show_rejects_value_without_scalar_package(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "arbiter_server.main.compose_config",
        lambda **_kwargs: OmegaConf.create({"arbiter": {"server": {"public": {}}}}),
    )

    assert (
        _run_config_show(
            config_dir="/tmp",
            config_name="arbiter-server",
            overrides=[],
            resolve=True,
            package="arbiter.server.public",
            value=True,
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "config show --value requires a scalar package" in captured.err


def test_cli_bootstrap_server_uses_default_config_dir(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert main(["bootstrap", "--server"]) == 0

    config_dir = tmp_path / ".arbiter"
    assert (config_dir / "arbiter-server.yaml").exists()
    assert capsys.readouterr().out == (
        f"wrote {config_dir / 'arbiter-server.yaml'}\n"
        f"wrote {config_dir / 'arbiter' / 'server.yaml'}\n"
    )


def test_cli_bootstrap_server_writes_main_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0

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
        "server │ fail\n"
        "- fail: config must define at least one service account before Arbiter can run\n"
        "  currently installed arbiter plugins: imap, smtp\n"
        "  use `arbiter-server --config-dir DIR bootstrap --plugin PLUGIN "
        "--account NAME` to create an account config\n"
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
        "  use `arbiter-server --config-dir DIR bootstrap --plugin PLUGIN "
        "--account NAME` to create an account config\n"
    )
    assert served == {}


def test_cli_bootstrap_server_accepts_matching_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0

    config_file = config_dir / "arbiter-server.yaml"
    server_file = config_dir / "arbiter" / "server.yaml"
    assert capsys.readouterr().out == (
        f"unchanged {config_file}\n" f"unchanged {server_file}\n"
    )


def test_cli_bootstrap_server_refuses_different_existing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    config_file = config_dir / "arbiter-server.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("local: true\n", encoding="utf-8")

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 1

    assert config_file.read_text(encoding="utf-8") == "local: true\n"
    assert capsys.readouterr().err == (
        "Arbiter bootstrap error: refusing to overwrite changed bootstrap "
        f"file: {config_file}\n"
        "  file differs from the generated bootstrap template\n"
        "  rerun with --force to overwrite it\n"
    )


def test_cli_bootstrap_server_refusal_uses_reploy_config_display_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "container-config"
    config_file = config_dir / "arbiter-server.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("local: true\n", encoding="utf-8")
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(config_dir))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "reploy-staging/conf")

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter bootstrap error: refusing to overwrite changed bootstrap "
        "file: reploy-staging/conf/arbiter-server.yaml\n"
        "  file differs from the generated bootstrap template\n"
        "  rerun with --force to overwrite it\n"
    )


def test_cli_bootstrap_server_reports_reploy_display_path_for_unreadable_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path.resolve() / "container-config"
    config_file = config_dir / "arbiter-server.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("local: true\n", encoding="utf-8")
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", str(config_dir))
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "reploy-staging/conf")
    real_exists = Path.exists

    def exists(path: Path) -> bool:
        if path == config_file:
            raise PermissionError("permission denied")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 1

    assert capsys.readouterr().err == (
        "Arbiter bootstrap error: cannot read bootstrap file: "
        "reploy-staging/conf/arbiter-server.yaml: permission denied\n"
    )


def test_reploy_config_display_dir_handles_windows_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPLOY_CONFIG_CONTAINER_DIR", r"C:\reploy\conf")
    monkeypatch.setenv("REPLOY_CONFIG_DISPLAY_DIR", "reploy-staging/conf")

    assert (
        _display_config_path(Path(r"C:\reploy\conf\arbiter-server.yaml"))
        == "reploy-staging/conf/arbiter-server.yaml"
    )


def test_cli_bootstrap_plugin_account_writes_service_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "smtp",
                "--account",
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
    assert "host: ${oc.env:SMTP_PERSONAL_ACCOUNT_HOST}\n" in account_yaml
    assert "port: ${oc.env:SMTP_PERSONAL_ACCOUNT_PORT,587}\n" in account_yaml
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
        "Edit the generated account and policy files.\n"
        "\n"
        "Rebuild the env file after edits:\n"
        f"  arbiter-server --config-dir {config_dir} env bootstrap\n"
        "\n"
        "Review activation status:\n"
        f"  arbiter-server --config-dir {config_dir} config activate\n"
        "\n"
        "Activate the account when ready:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate --plugin smtp --account personal_account\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )


def test_cli_bootstrap_plugin_defaults_to_default_account(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert main(["--config-dir", str(config_dir), "bootstrap", "--plugin", "smtp"]) == 0

    account_file = config_dir / "arbiter" / "account" / "smtp" / "default.yaml"
    policy_file = config_dir / "arbiter" / "policy" / "smtp" / "default_policy.yaml"
    assert account_file.exists()
    assert policy_file.exists()
    assert "policy: default_policy\n" in account_file.read_text(encoding="utf-8")
    assert capsys.readouterr().out == (
        f"wrote {account_file}\n"
        f"wrote {policy_file}\n"
        "\n"
        "Edit the generated account and policy files.\n"
        "\n"
        "Rebuild the env file after edits:\n"
        f"  arbiter-server --config-dir {config_dir} env bootstrap\n"
        "\n"
        "Review activation status:\n"
        f"  arbiter-server --config-dir {config_dir} config activate\n"
        "\n"
        "Activate the account when ready:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate --plugin smtp --account default\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )


def test_cli_bootstrap_plugin_accepts_comma_separated_plugins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugins",
                "imap,smtp",
            ]
        )
        == 0
    )

    for plugin in ("imap", "smtp"):
        account_file = config_dir / "arbiter" / "account" / plugin / "default.yaml"
        policy_file = config_dir / "arbiter" / "policy" / plugin / "default_policy.yaml"
        assert account_file.exists()
        assert policy_file.exists()
        assert "policy: default_policy\n" in account_file.read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    assert stdout == (
        f"wrote {config_dir / 'arbiter' / 'account' / 'imap' / 'default.yaml'}\n"
        f"wrote {config_dir / 'arbiter' / 'policy' / 'imap' / 'default_policy.yaml'}\n"
        f"wrote {config_dir / 'arbiter' / 'account' / 'smtp' / 'default.yaml'}\n"
        f"wrote {config_dir / 'arbiter' / 'policy' / 'smtp' / 'default_policy.yaml'}\n"
        "\n"
        "Edit the generated account and policy files.\n"
        "\n"
        "Rebuild the env file after edits:\n"
        f"  arbiter-server --config-dir {config_dir} env bootstrap\n"
        "\n"
        "Review activation status:\n"
        f"  arbiter-server --config-dir {config_dir} config activate\n"
        "\n"
        "Activate the accounts when ready:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate --plugins imap,smtp --account default\n"
        "\n"
        "Then inspect the composed config with:\n"
        f"  arbiter-server --config-dir {config_dir} config show\n"
    )
    assert "config activate --plugins imap,smtp --account default\n" in stdout
    assert stdout.count("Then inspect the composed config with:") == 1


def test_cli_bootstrap_plugin_accepts_comma_separated_plugins_with_shared_account_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugins",
                "imap,smtp",
                "--account",
                "bot",
            ]
        )
        == 0
    )

    for plugin in ("imap", "smtp"):
        account_file = config_dir / "arbiter" / "account" / plugin / "bot.yaml"
        policy_file = config_dir / "arbiter" / "policy" / plugin / "bot_policy.yaml"
        assert account_file.exists()
        assert policy_file.exists()
        assert "policy: bot_policy\n" in account_file.read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    assert "config activate --plugins imap,smtp --account bot\n" in stdout
    assert stdout.count("Then inspect the composed config with:") == 1


def test_cli_env_bootstrap_includes_bootstrapped_account_hosts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugins",
                "imap,smtp",
                "--account",
                "bot",
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
                "--plugins",
                "imap,smtp",
                "--account",
                "bot",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "env", "bootstrap"]) == 0

    env_text = (config_dir / ".env").read_text(encoding="utf-8")
    assert "IMAP_BOT_ACCOUNT_HOST=\n" in env_text
    assert "IMAP_BOT_ACCOUNT_USERNAME=\n" in env_text
    assert "IMAP_BOT_ACCOUNT_PASSWORD=\n" in env_text
    assert "SMTP_BOT_ACCOUNT_HOST=\n" in env_text
    assert "SMTP_BOT_ACCOUNT_USERNAME=\n" in env_text
    assert "SMTP_BOT_ACCOUNT_PASSWORD=\n" in env_text


def test_cli_bootstrap_accepts_plugin_and_account_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugins",
                "imap,smtp",
                "--account",
                "bot",
            ]
        )
        == 0
    )

    for plugin in ("imap", "smtp"):
        assert (config_dir / "arbiter" / "account" / plugin / "bot.yaml").exists()
        assert (config_dir / "arbiter" / "policy" / plugin / "bot_policy.yaml").exists()
    assert "config activate --plugins imap,smtp --account bot\n" in (
        capsys.readouterr().out
    )


def test_cli_bootstrap_option_form_defaults_account_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "imap",
            ]
        )
        == 0
    )

    assert (config_dir / "arbiter" / "account" / "imap" / "default.yaml").exists()
    assert (config_dir / "arbiter" / "policy" / "imap" / "default_policy.yaml").exists()


def test_cli_bootstrap_option_form_dry_mode_plans_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugins",
                "imap,smtp",
                "--account",
                "bot",
                "--dry-mode",
            ]
        )
        == 0
    )

    assert not (config_dir / "arbiter" / "account" / "imap" / "bot.yaml").exists()
    assert not (config_dir / "arbiter" / "policy" / "smtp" / "bot_policy.yaml").exists()
    assert capsys.readouterr().out == (
        "dry mode; no files changed\n"
        f"would create {config_dir / 'arbiter' / 'account' / 'imap' / 'bot.yaml'}\n"
        f"would create {config_dir / 'arbiter' / 'policy' / 'imap' / 'bot_policy.yaml'}\n"
        f"would create {config_dir / 'arbiter' / 'account' / 'smtp' / 'bot.yaml'}\n"
        f"would create {config_dir / 'arbiter' / 'policy' / 'smtp' / 'bot_policy.yaml'}\n"
    )


def test_cli_bootstrap_account_option_requires_plugin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["bootstrap", "--account", "bot"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "Arbiter bootstrap error: bootstrap options require --plugin/--plugins\n"
    )


def test_cli_bootstrap_help_shows_option_form_and_dry_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["bootstrap", "--help"])

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    assert "arbiter-server bootstrap --server\n" in stdout
    assert "arbiter-server bootstrap --plugin imap --account my_account\n" in stdout
    assert (
        "arbiter-server bootstrap --plugins imap,smtp --account my_account\n" in stdout
    )
    assert (
        "arbiter-server bootstrap --plugins imap,smtp --account my_account --dry-mode\n"
        in stdout
    )
    assert (
        "If --account/--accounts is omitted, the account name defaults to default."
        in stdout
    )


def test_cli_bootstrap_rejects_positional_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for target in ("arbiter", "plugin"):
        with pytest.raises(SystemExit) as exc_info:
            main(["bootstrap", target])

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"unrecognized arguments: {target}" in captured.err


def test_cli_bootstrap_plugin_uses_reploy_app_command_prefix_in_hints(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"
    monkeypatch.setenv("REPLOY_APP_COMMAND_PREFIX", "reploy app")

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(["--config-dir", str(config_dir), "bootstrap", "--plugins", "imap,smtp"])
        == 0
    )

    stdout = capsys.readouterr().out
    assert "  reploy app env bootstrap\n" in stdout
    assert "  reploy app activate\n" in stdout
    assert "  reploy app activate --plugins imap,smtp --account default\n" in stdout
    assert "  reploy app config show\n" in stdout
    assert "arbiter-server --config-dir" not in stdout


def test_cli_bootstrap_plugin_account_refuses_existing_policy_without_partial_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
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
                "--plugin",
                "smtp",
                "--account",
                "primary",
            ]
        )
        == 1
    )

    account_file = config_dir / "arbiter" / "account" / "smtp" / "primary.yaml"
    assert not account_file.exists()
    assert policy_file.read_text(encoding="utf-8") == "existing: true\n"
    assert capsys.readouterr().err == (
        "Arbiter bootstrap error: refusing to overwrite changed bootstrap "
        f"file: {policy_file}\n"
        "  file differs from the generated bootstrap template\n"
        "  rerun with --force to overwrite it\n"
    )


def test_cli_bootstrap_plugin_policy_writes_service_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "smtp",
                "--policy",
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
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "smtp",
                "--account",
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
                "--plugin",
                "smtp",
                "--account",
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


def test_cli_config_activate_account_accepts_comma_separated_plugins(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(["--config-dir", str(config_dir), "bootstrap", "--plugins", "imap,smtp"])
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
                "--plugins",
                "imap,smtp",
                "--account",
                "default",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "    - imap/default\n" in config_yaml
    assert "    - smtp/default\n" in config_yaml
    assert "    - imap/default_policy\n" in config_yaml
    assert "    - smtp/default_policy\n" in config_yaml
    assert capsys.readouterr().out == f"updated {config_dir / 'arbiter-server.yaml'}\n"

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "deactivate",
                "--plugins",
                "imap,smtp",
                "--account",
                "default",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "    - imap/default\n" not in config_yaml
    assert "    - smtp/default\n" not in config_yaml
    assert "    - imap/default_policy\n" not in config_yaml
    assert "    - smtp/default_policy\n" not in config_yaml
    assert capsys.readouterr().out == f"updated {config_dir / 'arbiter-server.yaml'}\n"


def test_cli_config_activate_accepts_plugin_and_account_options(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(["--config-dir", str(config_dir), "bootstrap", "--plugins", "imap,smtp"])
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
                "--plugins",
                "imap,smtp",
                "--account",
                "default",
            ]
        )
        == 0
    )

    config_yaml = (config_dir / "arbiter-server.yaml").read_text(encoding="utf-8")
    assert "    - imap/default\n" in config_yaml
    assert "    - smtp/default\n" in config_yaml
    assert "    - imap/default_policy\n" in config_yaml
    assert "    - smtp/default_policy\n" in config_yaml
    assert capsys.readouterr().out == f"updated {config_dir / 'arbiter-server.yaml'}\n"


def test_cli_config_activate_lists_installed_plugin_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"
    monkeypatch.setenv("REPLOY_APP_COMMAND_PREFIX", "./reploy app")
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(["--config-dir", str(config_dir), "bootstrap", "--plugins", "imap,smtp"])
        == 0
    )
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "--plugin",
                "imap",
                "--account",
                "default",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "config", "activate"]) == 0

    assert capsys.readouterr().out == (
        "installed plugins:\n"
        "  imap\n"
        "  smtp\n"
        "accounts:\n"
        "  default: ✓ imap  ✗ smtp\n"
        "    activate:\n"
        "      ./reploy app config activate --plugin smtp --account default\n"
    )

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "--plugin",
                "smtp",
                "--account",
                "default",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "config", "activate"]) == 0

    assert capsys.readouterr().out == (
        "installed plugins:\n"
        "  imap\n"
        "  smtp\n"
        "accounts:\n"
        "  default: ✓ imap  ✓ smtp\n"
    )


def test_cli_config_activate_lists_empty_account_status_consistently(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"
    monkeypatch.setenv("REPLOY_APP_COMMAND_PREFIX", "reploy app")
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "config", "activate"]) == 0

    assert capsys.readouterr().out == (
        "installed plugins:\n"
        "  imap\n"
        "  smtp\n"
        "create account configs:\n"
        "  single plugin: reploy app bootstrap --plugin imap --account my_account\n"
        "  batch: reploy app bootstrap --plugins imap,smtp --account my_account\n"
        "  preview: reploy app bootstrap --plugins imap,smtp --account my_account --dry-mode\n"
        "  account name is optional; default is used when omitted\n"
    )


def test_cli_config_activate_status_colors_plugin_icons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "conf"
    monkeypatch.setenv("ARBITER_COLOR", "always")
    monkeypatch.setattr(
        "arbiter_server.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(["--config-dir", str(config_dir), "bootstrap", "--plugins", "imap,smtp"])
        == 0
    )
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "config",
                "activate",
                "--plugin",
                "imap",
                "--account",
                "default",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--config-dir", str(config_dir), "config", "activate"]) == 0

    assert (
        "  default: \033[32m✓\033[0m imap  \033[31m✗\033[0m smtp\n"
        in capsys.readouterr().out
    )


def test_cli_config_activate_help_shows_account_target_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["config", "activate", "--help"])

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    assert (
        "usage: arbiter-server config activate " "[--plugin PLUGIN --account NAME]\n"
    ) in stdout
    assert "Show account activation status when no target is provided." in stdout
    assert "arbiter-server config activate\n" in stdout
    assert (
        "arbiter-server config activate --plugin imap --account my_account\n" in stdout
    )
    assert (
        "arbiter-server config activate --plugins imap,smtp --account my_account\n"
        in stdout
    )


def test_cli_config_activate_rejects_positional_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["config", "activate", "account"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: account" in captured.err


def test_cli_config_activate_option_form_requires_plugin_and_account(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["config", "activate", "--plugin", "imap"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Arbiter config error: config activate requires --plugin/--plugins "
        "and --account/--accounts\n"
    )


def test_cli_config_activate_account_can_alias_policy_file_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "conf"
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
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
                "--plugin",
                "smtp",
                "--account",
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
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "smtp",
                "--account",
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
                "--plugin",
                "smtp",
                "--account",
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
                "--plugin",
                "smtp",
                "--account",
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
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
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
                    "--plugin",
                    "smtp",
                    "--account",
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
                "--plugin",
                "smtp",
                "--account",
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
                "--plugin",
                "smtp",
                "--account",
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

    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "imap",
                "--account",
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
    assert "host: ${oc.env:IMAP_BOT_ACCOUNT_HOST}\n" in account_yaml
    assert "port: ${oc.env:IMAP_BOT_ACCOUNT_PORT,993}\n" in account_yaml
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
        "Edit the generated account and policy files.\n"
        "\n"
        "Rebuild the env file after edits:\n"
        f"  arbiter-server --config-dir {config_dir} env bootstrap\n"
        "\n"
        "Review activation status:\n"
        f"  arbiter-server --config-dir {config_dir} config activate\n"
        "\n"
        "Activate the account when ready:\n"
        f"  arbiter-server --config-dir {config_dir} "
        "config activate --plugin imap --account bot\n"
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
                "--plugin",
                "imap",
                "--policy",
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
    assert main(["--config-dir", str(config_dir), "bootstrap", "--server"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "bootstrap",
                "--plugin",
                "imap",
                "--policy",
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
