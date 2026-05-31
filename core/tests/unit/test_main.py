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

from agent_arbiter.config import AppConfig, ArbiterConfig, DiscoveryConfig
from agent_arbiter.app import CORE_TOOL_NAMES
from agent_arbiter.main import (
    _run_server,
    build_app,
    build_server,
    compose_config,
    config_check_summary,
    ensure_runnable_config,
    load_env_file,
    log_startup_summary,
    main,
    service_plugin_names,
)
from agent_arbiter.plugins import discover_service_plugins
from agent_arbiter_imap import IMAPRuntime, IMAPServicePlugin
from agent_arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFolderConfig,
)
from agent_arbiter_smtp import SendEmailResult, SMTPRuntime, SMTPServicePlugin
from agent_arbiter_smtp.config import SMTPConfig, SMTPServicePolicyConfig
from agent_arbiter.services import (
    CORE_API_VERSION,
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    CapabilityDescriptor,
    OperationDescriptor,
    RuntimeRegistry,
    ServicePlugin,
    ServicePluginContext,
)


def test_build_app_accepts_hydra_config() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp_imap())

    app = build_app(cfg, service_plugins=_test_service_plugins())

    assert app.tool_names() == list(CORE_TOOL_NAMES)


def test_build_app_list_accounts_uses_real_config_shape() -> None:
    cfg = OmegaConf.structured(_app_config_with_smtp())

    app = build_app(cfg, service_plugins=_test_service_plugins())

    assert app.list_accounts() == {
        "smtp": {
            "primary": {
                "description": "Bot-owned account for automated email tasks.",
                "policy": "bot",
                "enabled": True,
                "send": "allowed",
                "require_confirmation": False,
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
        version = "0.8.0"
        core_api_version = CORE_API_VERSION

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
    assert app.tool_names() == list(CORE_TOOL_NAMES)
    assert app.list_accounts() == {"whatsapp": {"bot": {"enabled": True}}}


def test_discover_service_plugins_loads_entry_point_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        def __init__(self, name: str) -> None:
            self.name = name
            self.version = "0.8.0"
            self.core_api_version = CORE_API_VERSION

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
        "agent_arbiter.plugins.entry_points",
        lambda: FakeEntryPoints(
            [
                FakeEntryPoint(smtp_plugin),
                FakeEntryPoint(imap_plugin),
            ]
        ),
    )

    assert [plugin.name for plugin in discover_service_plugins()] == ["imap", "smtp"]


def test_discover_service_plugins_rejects_wrong_core_api_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlugin:
        name = "stale"
        version = "0.7.9"
        core_api_version = "0.7"

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
        "agent_arbiter.plugins.entry_points",
        lambda: FakeEntryPoints([FakeEntryPoint()]),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "service plugin stale targets Agent Arbiter core API 0.7, "
            "but loaded core API is 0.8"
        ),
    ):
        discover_service_plugins()


def test_build_app_rejects_plugin_version_outside_core_line() -> None:
    class FakeExternalPlugin:
        name = "whatsapp"
        version = "0.7.9"
        core_api_version = CORE_API_VERSION

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
            "core API line 0.8"
        ),
    ):
        build_app(cfg, service_plugins=[FakeExternalPlugin()])


def test_config_check_summary_validates_runtime_construction() -> None:
    assert (
        config_check_summary(
            _app_config_with_smtp_imap(),
            service_plugins=_test_service_plugins(),
        )
        == "config ok: services=smtp,imap service_accounts=smtp:primary;imap:primary"
    )


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
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", "/tmp", "plugins", "list"]) == 0

    assert capsys.readouterr().out == "imap\nsmtp\n"


def test_server_cli_help_uses_agent_arbiter_program_name(
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
        "agent_arbiter.main.compose_config",
        lambda **_kwargs: OmegaConf.structured(_app_config_with_smtp()),
    )
    monkeypatch.setattr(
        "agent_arbiter.main.build_server",
        lambda _cfg: object(),
    )
    monkeypatch.setattr("agent_arbiter.main._run_server", raise_keyboard_interrupt)

    assert main(["--config-dir", "/tmp", "serve"]) == 130

    assert capsys.readouterr().err == "Agent Arbiter server stopped.\n"


def test_compose_config_registers_configs_before_composing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    (tmp_path / "config.yaml").write_text(
        "arbiter:\n  server:\n    name: agent-arbiter\n",
        encoding="utf-8",
    )

    def fake_register_configs() -> None:
        calls.append("register_configs")

    monkeypatch.setattr("agent_arbiter.main.register_configs", fake_register_configs)

    cfg = compose_config(config_dir=tmp_path, config_name="config")

    assert cfg.arbiter.server.name == "agent-arbiter"
    assert calls == ["register_configs"]


def test_compose_config_loads_env_file_before_composing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_ARBITER_TEST_SERVER_NAME", raising=False)
    (tmp_path / "config.yaml").write_text(
        "arbiter:\n"
        "  env_file: local.env\n"
        "  server:\n"
        "    name: ${oc.env:AGENT_ARBITER_TEST_SERVER_NAME}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "\n"
        "# Local operator-owned environment.\n"
        'export AGENT_ARBITER_TEST_SERVER_NAME="from-env-file" # comment\n',
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
    monkeypatch.setenv("AGENT_ARBITER_TEST_ENV_FILE_PRECEDENCE", "from-process")
    env_file = tmp_path / "local.env"
    env_file.write_text(
        'AGENT_ARBITER_TEST_ENV_FILE_PRECEDENCE="from file"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["AGENT_ARBITER_TEST_ENV_FILE_PRECEDENCE"] == "from-process"


def test_load_env_file_reports_invalid_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "local.env"
    env_file.write_text("not an assignment\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid env file line 1"):
        load_env_file(env_file)


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
        "Agent Arbiter env error: missing required environment variables:\n"
        "  SMTP_PRIMARY_ACCOUNT_PASSWORD (agent-arbiter-smtp)\n"
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
        "# agent-arbiter-imap\n"
        "IMAP_PRIMARY_ACCOUNT_USERNAME=imap-user\n"
        "IMAP_PRIMARY_ACCOUNT_PASSWORD=\n"
        "\n"
        "# agent-arbiter-smtp\n"
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
        "# agent-arbiter-smtp\n" "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n",
        encoding="utf-8",
    )

    assert main(["--config-dir", str(tmp_path), "env", "bootstrap"]) == 0

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
        "# agent-arbiter-smtp\n"
        "SMTP_PRIMARY_ACCOUNT_USERNAME=\n"
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n"
    )
    assert capsys.readouterr().out == f"wrote {tmp_path / '.env'}\n"


def test_cli_deploy_docker_init_writes_local_deploy_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "1.2.3")
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
    assert "python -m pip wheel --no-cache-dir --no-deps --wheel-dir" in compose_text
    assert (
        "python -m pip install --no-cache-dir -r /tmp/requirements.pinned "
        "/tmp/agent-arbiter-wheels/*.whl"
    ) in compose_text
    assert "AGENT_ARBITER_SERVER_HOST: 0.0.0.0" in compose_text
    assert (
        '"arbiter.server.host=$AGENT_ARBITER_SERVER_HOST" '
        '"arbiter.server.port=$AGENT_ARBITER_CONTAINER_PORT"'
    ) in compose_text
    assert not (deploy_dir / "config.yaml").exists()
    assert (deploy_dir / "conf").is_dir()
    assert not (deploy_dir / "conf" / ".env").exists()
    docker_env = (deploy_dir / "docker.env").read_text(encoding="utf-8")
    assert "AGENT_ARBITER_HOST_BIND=127.0.0.1\n" in docker_env
    assert "AGENT_ARBITER_HOST_PORT=8025\n" in docker_env
    assert "AGENT_ARBITER_DOCKER_NETWORK_NAME=agent-arbiter\n" in docker_env
    assert "AGENT_ARBITER_DOCKER_SUBNET=172.31.250.0/24\n" in docker_env
    assert "AGENT_ARBITER_LOCAL_SOURCE_DIR" not in docker_env
    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "agent-arbiter==1.2.3\n"
    )
    assert not (deploy_dir / "compose.override.yaml").exists()
    helper = deploy_dir / "arbiter-docker"
    assert helper.exists()
    assert helper.stat().st_mode & 0o111
    manifest = json.loads(
        (deploy_dir / ".agent-arbiter-deploy.json").read_text(encoding="utf-8")
    )
    assert manifest["generator"] == "arbiter-server deploy docker"
    assert sorted(manifest["files"]) == [
        "arbiter-docker",
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
                "docker.requirement=agent-arbiter==1.2.3",
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
                "docker.requirement=agent-arbiter==1.2.3",
            ]
        )
        == 1
    )

    assert compose_file.read_text(encoding="utf-8") == "existing\n"
    assert capsys.readouterr().err == (
        "Agent Arbiter deploy error: refusing to overwrite existing deployment "
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
                "docker.requirement=agent-arbiter-core==1.2.3",
                "docker.requirement=agent-arbiter-smtp==1.2.3",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "agent-arbiter-core==1.2.3\n" "agent-arbiter-smtp==1.2.3\n"
    )
    capsys.readouterr()


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
                "docker.requirement=agent-arbiter",
                "init",
            ]
        )
        == 2
    )

    assert capsys.readouterr().err == (
        "Agent Arbiter deploy error: docker.requirement must be an exact "
        "package pin (name==version) or an absolute container path\n"
        "  value: agent-arbiter\n"
    )
    assert not deploy_dir.exists()


def test_cli_deploy_docker_init_uses_local_checkout_default_requirements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "0.8.0.dev1")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    for package_dir in ("core", "smtp", "imap"):
        package_path = checkout / package_dir
        package_path.mkdir()
        (package_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.chdir(checkout)
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 0

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/agent-arbiter/core\n"
        "/source/agent-arbiter/smtp\n"
        "/source/agent-arbiter/imap\n"
    )
    assert (deploy_dir / "compose.override.yaml").read_text(encoding="utf-8") == (
        "services:\n"
        "  agent-arbiter:\n"
        "    volumes:\n"
        f"      - {checkout.resolve()}:/source/agent-arbiter:ro\n"
    )
    capsys.readouterr()


def test_cli_deploy_docker_init_rejects_unlocated_local_dev_default_requirement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "0.8.0.dev1")
    outside_checkout = tmp_path / "outside"
    outside_checkout.mkdir()
    monkeypatch.chdir(outside_checkout)
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "init"]) == 2

    assert capsys.readouterr().err == (
        "Agent Arbiter deploy error: cannot infer default docker requirements\n"
        "  pass docker.requirement=agent-arbiter==VERSION for a published "
        "package\n"
        "  for a local checkout, run this command from the checkout or pass "
        "absolute container source paths\n"
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
                "docker.requirement=/source/agent-arbiter/core",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/agent-arbiter/core\n"
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
                "docker.requirement=/wheels/agent_arbiter-1.2.3-py3-none-any.whl",
                "init",
            ]
        )
        == 0
    )

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/wheels/agent_arbiter-1.2.3-py3-none-any.whl\n"
    )
    compose_text = (deploy_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "--no-index --find-links /wheels -r /requirements.txt" in compose_text
    assert not (deploy_dir / "wheels").exists()
    capsys.readouterr()


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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
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
        "ok: requirements file uses exact pins or absolute container paths"
        in valid_result.stdout
    )

    (deploy_dir / "requirements.txt").write_text("agent-arbiter\n", encoding="utf-8")
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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    (deploy_dir / "requirements.txt").write_text("agent-arbiter\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
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
    env["AGENT_ARBITER_COLOR"] = "always"
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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
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
    env["AGENT_ARBITER_COLOR"] = "never"

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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
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
    assert "ok: preinstall checks passed\n" in result.stdout


def test_cli_deploy_docker_generated_helper_preinstall_rejects_source_override(
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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  agent-arbiter:\n"
        "    volumes:\n"
        "      - /home/example/agent-arbiter:/source/agent-arbiter:ro\n",
        encoding="utf-8",
    )
    (deploy_dir / "requirements.txt").write_text(
        "/source/agent-arbiter/core\n",
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
        "fail: preinstall cannot promote local source requirements: "
        f"{deploy_dir / 'requirements.txt'}\n"
    ) in result.stdout
    assert (
        "      use pinned packages (name==version) or /wheels/*.whl entries "
        "before install\n"
    ) in result.stdout
    assert (
        "fail: preinstall cannot promote a local source mount: "
        f"{deploy_dir / 'compose.override.yaml'}\n"
    ) in result.stdout
    assert (
        "      remove the /source/agent-arbiter mount after switching " "requirements\n"
    ) in result.stdout


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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")

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
    assert "ok: preinstall checks passed\n" in result.stdout
    assert f"would copy deployment: {deploy_dir} -> /opt/arbiter\n" in result.stdout
    assert "would create system group if missing: arbiter\n" in result.stdout
    assert "would create system user if missing: arbiter\n" in result.stdout
    assert "would write systemd unit: /etc/systemd/system/arbiter.service\n" in (
        result.stdout
    )
    assert "would run: systemctl restart arbiter.service\n" in result.stdout


def test_cli_deploy_docker_generated_helper_doctor_colors_tty_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not shutil.which("script"):
        pytest.skip("script command is not available")
    deploy_dir = tmp_path / "docker"
    assert (
        main(
            [
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
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
    env.pop("AGENT_ARBITER_COLOR", None)

    result = subprocess.run(
        ["script", "-q", "-c", f"{deploy_dir / 'arbiter-docker'} doctor", "/dev/null"],
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config_dir = deploy_dir / "conf"
    (config_dir / "arbiter-server.yaml").write_text("arbiter: {}\n", encoding="utf-8")
    (config_dir / ".env").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        "  printf 'Docker Compose version v2.fake\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = network ] && [ "$2" = ls ]; then\n'
        "  if [ \"${3:-}\" = -q ]; then printf 'network-id\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = network ] && [ "$2" = inspect ]; then\n'
        "  printf 'mail-sentry 172.31.250.0/24 \\n'\n"
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
        "fail: Docker subnet 172.31.250.0/24 already belongs to network "
        "mail-sentry\n"
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
                "docker.requirement=agent-arbiter==1.2.3",
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
        "#!/usr/bin/env sh\n" "printf 'agent-arbiter\\n' > \"$1\"\n",
        encoding="utf-8",
    )
    editor.chmod(0o755)
    env = os.environ.copy()
    env["AGENT_ARBITER_EDITOR"] = str(editor)

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
                "docker.requirement=agent-arbiter==1.2.3",
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
        "    host: ${oc.env:AGENT_ARBITER_SERVER_HOST,127.0.0.1}\n"
        "  etc:\n"
        "    token: ${oc.env:EXISTING_TOKEN}\n"
        "    timeout: ${oc.env:NEW_TIMEOUT,30}\n",
        encoding="utf-8",
    )
    env_file = deploy_dir / "conf" / ".env"
    env_file.write_text("EXISTING_TOKEN=keep\n", encoding="utf-8")
    docker_env_file = deploy_dir / "docker.env"
    docker_env_file.write_text(
        "AGENT_ARBITER_HOST_PORT=9000\n" "LOCAL_ONLY=value\n",
        encoding="utf-8",
    )
    requirements_file = deploy_dir / "requirements.txt"
    requirements_file.write_text("agent-arbiter==old\n", encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert config_file.read_text(encoding="utf-8").startswith("arbiter:\n")
    assert requirements_file.read_text(encoding="utf-8") == "agent-arbiter==old\n"
    assert env_file.read_text(encoding="utf-8") == "EXISTING_TOKEN=keep\n"
    assert docker_env_file.read_text(encoding="utf-8") == (
        "# Docker Compose settings for the Agent Arbiter deployment.\n"
        "# These values control the container wrapper, not Agent Arbiter runtime "
        "config.\n"
        "\n"
        "AGENT_ARBITER_IMAGE=python:3.11-slim\n"
        "AGENT_ARBITER_CONTAINER_NAME=agent-arbiter\n"
        "AGENT_ARBITER_RESTART=unless-stopped\n"
        "AGENT_ARBITER_APP_ENV_FILE=./conf/.env\n"
        "AGENT_ARBITER_CONFIG_DIR=./conf\n"
        "AGENT_ARBITER_CONFIG_NAME=arbiter-server\n"
        "AGENT_ARBITER_REQUIREMENTS_FILE=./requirements.txt\n"
        "AGENT_ARBITER_HOST_BIND=127.0.0.1\n"
        "AGENT_ARBITER_HOST_PORT=9000\n"
        "AGENT_ARBITER_CONTAINER_PORT=8025\n"
        "AGENT_ARBITER_DOCKER_NETWORK_NAME=agent-arbiter\n"
        "AGENT_ARBITER_DOCKER_BRIDGE_NAME=agent-arbiter0\n"
        "AGENT_ARBITER_DOCKER_SUBNET=172.31.250.0/24\n"
        "\n"
        "# Extra local Compose values.\n"
        "LOCAL_ONLY=value\n"
    )
    assert (deploy_dir / "compose.yaml").exists()
    assert (deploy_dir / "arbiter-docker").exists()
    capsys.readouterr()


def test_cli_deploy_docker_update_creates_local_checkout_source_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "0.8.0.dev1")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    for package_dir in ("core", "smtp", "imap"):
        package_path = checkout / package_dir
        package_path.mkdir()
        (package_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.chdir(checkout)
    deploy_dir = tmp_path / "docker"

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert (deploy_dir / "requirements.txt").read_text(encoding="utf-8") == (
        "/source/agent-arbiter/core\n"
        "/source/agent-arbiter/smtp\n"
        "/source/agent-arbiter/imap\n"
    )
    assert (deploy_dir / "compose.override.yaml").read_text(encoding="utf-8") == (
        "services:\n"
        "  agent-arbiter:\n"
        "    volumes:\n"
        f"      - {checkout.resolve()}:/source/agent-arbiter:ro\n"
    )
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
                "docker.requirement=agent-arbiter==1.2.3",
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
                "docker.requirement=agent-arbiter==1.2.3",
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
    manifest_path = deploy_dir / ".agent-arbiter-deploy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["compose.yaml"]["sha256"] = "stale-compose"
    manifest["files"]["arbiter-docker"]["sha256"] = "stale-helper"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    output = capsys.readouterr().out
    assert f"skipped modified file: {compose_file}\n" not in output
    assert f"skipped modified file: {helper_file}\n" not in output
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
                "docker.requirement=agent-arbiter==1.2.3",
                "init",
            ]
        )
        == 0
    )
    capsys.readouterr()
    compose_file = deploy_dir / "compose.yaml"
    compose_file.write_text("operator change\n", encoding="utf-8")
    manifest_path = deploy_dir / ".agent-arbiter-deploy.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")

    assert main(["deploy", "docker", f"docker.dir={deploy_dir}", "update"]) == 0

    assert compose_file.read_text(encoding="utf-8") == "operator change\n"
    assert manifest_path.read_text(encoding="utf-8") == original_manifest
    output = capsys.readouterr().out
    assert f"skipped modified file: {compose_file}\n" in output
    assert f"wrote {manifest_path}\n" not in output
    assert "Files already up to date:" not in output


def test_cli_deploy_docker_reports_unknown_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["deploy", "docker", "docker.image=python", "init"]) == 2

    assert capsys.readouterr().err == (
        "Agent Arbiter deploy error: unknown docker deploy override: docker.image\n"
    )


def test_cli_lists_plugins_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", "/tmp", "plugins", "list", "--json"]) == 0

    assert capsys.readouterr().out == (
        '{"core": {"version": "0.8.0", "api_version": "0.8"}, '
        '"plugins": [{"name": "imap", "version": "0.8.0", '
        '"core_api_version": "0.8"}, {"name": "smtp", "version": "0.8.0", '
        '"core_api_version": "0.8"}]}\n'
    )


def test_cli_version_prints_core_and_plugin_versions(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", "/tmp", "version"]) == 0

    assert capsys.readouterr().out == (
        "core 0.8.0 (api 0.8)\n"
        "plugins:\n"
        "  imap 0.8.0 (core api 0.8)\n"
        "  smtp 0.8.0 (core api 0.8)\n"
    )


def test_cli_version_can_print_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_arbiter.main.discover_service_plugins",
        lambda: _test_service_plugins(),
    )

    assert main(["--config-dir", "/tmp", "version", "--json"]) == 0

    assert capsys.readouterr().out == (
        '{"core": {"version": "0.8.0", "api_version": "0.8"}, '
        '"plugins": [{"name": "imap", "version": "0.8.0", '
        '"core_api_version": "0.8"}, {"name": "smtp", "version": "0.8.0", '
        '"core_api_version": "0.8"}]}\n'
    )


def test_cli_serve_subcommand_passes_config_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_calls: list[dict[str, object]] = []

    def fake_serve(**kwargs: object) -> int:
        serve_calls.append(kwargs)
        return 0

    monkeypatch.setattr("agent_arbiter.main._run_serve", fake_serve)

    assert (
        main(
            [
                "--config-dir",
                "/tmp",
                "--config-name",
                "arbiter-server-local",
                "serve",
                "arbiter.server.port=8025",
            ]
        )
        == 0
    )

    assert serve_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server-local",
            "overrides": ["arbiter.server.port=8025"],
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

    monkeypatch.setattr("agent_arbiter.main._run_config_check", fake_check)

    assert (
        main(["--config-dir", "/tmp", "config", "check", "arbiter.server.port=8025"])
        == 0
    )

    assert check_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server",
            "overrides": ["arbiter.server.port=8025"],
        },
    ]


def test_cli_config_show_subcommand_passes_config_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show_calls: list[dict[str, object]] = []

    def fake_show(**kwargs: object) -> int:
        show_calls.append(kwargs)
        return 0

    monkeypatch.setattr("agent_arbiter.main._run_config_show", fake_show)

    assert (
        main(
            [
                "--config-dir",
                "/tmp",
                "config",
                "show",
                "--resolve",
                "arbiter.server.port=8025",
            ]
        )
        == 0
    )

    assert show_calls == [
        {
            "config_dir": "/tmp",
            "config_name": "arbiter-server",
            "overrides": ["arbiter.server.port=8025"],
            "resolve": True,
        },
    ]


def test_cli_bootstrap_arbiter_uses_default_config_dir(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

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
        "# Agent Arbiter composes this config at startup from the defaults "
        "below.\n"
        "# Inspect the composed config with:\n"
        "#   arbiter-server --config-dir <dir> --config-name arbiter-server config show\n"
        "# Override composed values with Hydra overrides, for example:\n"
        "#   arbiter-server --config-dir <dir> serve arbiter.server.port=8025\n"
        "# Optionally load a config-dir-relative dotenv file before composition:\n"
        "#   arbiter:\n"
        "#     env_file: local.env\n"
        "  - arbiter: server\n"
        "  - _self_\n"
    )
    server_file = config_dir / "arbiter" / "server.yaml"
    assert server_file.read_text(encoding="utf-8") == (
        "# @package arbiter\n"
        "server:\n"
        "  name: agent-arbiter\n"
        "  transport: streamable-http\n"
        "  host: 127.0.0.1\n"
        "  port: 8000\n"
        "  path: /mcp\n"
        "  stateless_http: true\n"
        "  json_response: true\n"
        "discovery:\n"
        "  max_account_preview_limit: 25\n"
        "  max_operation_preview_limit: 25\n"
    )
    assert capsys.readouterr().out == (
        f"wrote {config_file}\n" f"wrote {server_file}\n"
    )

    assert main(["--config-dir", str(config_dir), "config", "check"]) == 1
    assert capsys.readouterr().err == (
        "Agent Arbiter config error: config must define at least one service "
        "account before Agent Arbiter can run\n"
        "  currently installed arbiter plugins: imap, smtp\n"
        "  use `arbiter-server --config-dir DIR bootstrap plugin PLUGIN account "
        "NAME` to create an account config\n"
    )

    served: dict[str, object] = {}

    def fake_run_server(server: object, transport: object) -> None:
        served["server"] = server
        served["transport"] = transport

    monkeypatch.setattr("agent_arbiter.main._run_server", fake_run_server)
    assert main(["--config-dir", str(config_dir), "serve"]) == 1
    assert capsys.readouterr().err == (
        "Agent Arbiter config error: config must define at least one service "
        "account before Agent Arbiter can run\n"
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
    assert "# Require confirmation before sending through this policy.\n" in policy_yaml
    assert "require_confirmation: true\n" in policy_yaml
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
        "Agent Arbiter bootstrap error: refusing to overwrite existing file: "
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
    assert "# Require confirmation before sending through this policy.\n" in policy_yaml
    assert "require_confirmation: true\n" in policy_yaml
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
    assert cfg.arbiter.policy.smtp.personal_account_policy.require_confirmation is True
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
        "  - _self_\n"
        "require_confirmation: true\n",
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
    assert cfg.arbiter.policy.smtp.bot_policy.require_confirmation is True


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
        "  - _self_\n"
        "require_confirmation: true\n",
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


def test_cli_bootstrap_plugin_refuses_missing_example(
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
                "account",
                "primary",
            ]
        )
        == 1
    )

    assert capsys.readouterr().err == (
        "Agent Arbiter bootstrap error: service plugin does not provide an "
        "account bootstrap example: imap\n"
    )


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
                "smtp": {"bot": SMTPServicePolicyConfig(require_confirmation=False)},
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
                "smtp": {"bot": SMTPServicePolicyConfig(require_confirmation=False)},
                "imap": {"bot": IMAPAccessPolicyConfig()},
            },
        )
    )


def test_log_startup_summary_includes_safe_runtime_context(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _app_config_with_smtp()
    cast(SMTPConfig, cfg.arbiter.account["smtp"]["primary"]).password = "super-secret"

    monkeypatch.setattr("agent_arbiter.main.package_version", lambda: "1.2.3")
    caplog.set_level(logging.INFO, logger="agent_arbiter.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "Agent Arbiter starting version=1.2.3" in message
    assert "transport=streamable-http" in message
    assert "bind=127.0.0.1:8000/mcp" in message
    assert "services=smtp" in message
    assert "service_accounts=smtp:primary" in message
    assert "super-secret" not in message
    assert "agent@example.com" not in message


def test_build_server_registers_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    tools: dict[str, Callable[..., object]] = {}
    list_accounts_calls = 0
    send_email_calls: list[dict[str, object]] = []
    list_messages_calls: list[dict[str, object]] = []
    get_message_calls: list[dict[str, object]] = []
    search_messages_calls: list[dict[str, object]] = []
    move_message_calls: list[dict[str, object]] = []
    mark_message_read_calls: list[dict[str, object]] = []
    delete_message_calls: list[dict[str, object]] = []
    fake_cfg = _app_config_with_smtp_imap()
    smtp_accounts = fake_cfg.arbiter.account["smtp"]
    smtp_policies = fake_cfg.arbiter.policy["smtp"]
    imap_accounts = fake_cfg.arbiter.account["imap"]
    imap_policies = fake_cfg.arbiter.policy["imap"]

    class FakeSMTPRuntime(SMTPRuntime):
        def send_email(
            self,
            account: str,
            to: list[str],
            subject: str,
            text_body: str | None = None,
            html_body: str | None = None,
            cc: list[str] | None = None,
            bcc: list[str] | None = None,
        ) -> SendEmailResult:
            send_email_calls.append(
                {
                    "account": account,
                    "to": to,
                    "subject": subject,
                    "text_body": text_body,
                    "html_body": html_body,
                    "cc": cc,
                    "bcc": bcc,
                }
            )
            return SendEmailResult(
                tool="send_email",
                message_id="<message-id@example.com>",
                recipient_count=len(to) + len(cc or []) + len(bcc or []),
            )

    class FakeIMAPRuntime(IMAPRuntime):
        def list_messages(
            self,
            account: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            list_messages_calls.append(
                {"account": account, "folder": folder, "limit": limit}
            )
            return {"account": account, "folder": folder or "INBOX", "messages": []}

        def get_message(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            get_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"account": account, "folder": folder or "INBOX", "message": {}}

        def search_messages(
            self,
            account: str,
            query: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            search_messages_calls.append(
                {
                    "account": account,
                    "query": query,
                    "folder": folder,
                    "limit": limit,
                }
            )
            return {
                "account": account,
                "folder": folder or "INBOX",
                "query": query,
                "messages": [],
            }

        def move_message(
            self,
            account: str,
            message_id: str,
            destination_folder: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            move_message_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "destination_folder": destination_folder,
                    "folder": folder,
                }
            )
            return {"ok": True}

        def mark_message_read(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
            read: bool = True,
        ) -> dict[str, object]:
            mark_message_read_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "folder": folder,
                    "read": read,
                }
            )
            return {"ok": True}

        def delete_message(
            self,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            delete_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"ok": True}

    class FakeApp:
        runtime_registry = RuntimeRegistry(
            {
                "smtp": FakeSMTPRuntime(
                    accounts=smtp_accounts,
                    policies=smtp_policies,
                    smtp_client_factory=lambda config: cast(Any, object()),
                ),
                "imap": FakeIMAPRuntime(
                    accounts=imap_accounts,
                    policies=imap_policies,
                ),
            }
        )

        def list_accounts(self) -> dict[str, object]:
            nonlocal list_accounts_calls
            list_accounts_calls += 1
            return {
                "imap": {
                    "primary": {
                        "description": "Primary account",
                        "policy": "bot",
                        "enabled": True,
                    },
                },
                "smtp": {
                    "primary": {
                        "description": "Primary account",
                        "policy": "bot",
                        "enabled": True,
                        "send": "allowed",
                        "require_confirmation": False,
                    },
                },
            }

        def send_email(
            self,
            *,
            account: str,
            to: list[str],
            subject: str,
            text_body: str | None = None,
            html_body: str | None = None,
            cc: list[str] | None = None,
            bcc: list[str] | None = None,
        ) -> SimpleNamespace:
            send_email_calls.append(
                {
                    "account": account,
                    "to": to,
                    "subject": subject,
                    "text_body": text_body,
                    "html_body": html_body,
                    "cc": cc,
                    "bcc": bcc,
                }
            )
            return SimpleNamespace(
                message_id="<message-id@example.com>",
                recipient_count=len(to) + len(cc or []) + len(bcc or []),
            )

        def list_messages(
            self,
            *,
            account: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            list_messages_calls.append(
                {"account": account, "folder": folder, "limit": limit}
            )
            return {"account": account, "folder": folder or "INBOX", "messages": []}

        def get_message(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            get_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"account": account, "folder": folder or "INBOX", "message": {}}

        def search_messages(
            self,
            *,
            account: str,
            query: str,
            folder: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            search_messages_calls.append(
                {
                    "account": account,
                    "query": query,
                    "folder": folder,
                    "limit": limit,
                }
            )
            return {
                "account": account,
                "folder": folder or "INBOX",
                "query": query,
                "messages": [],
            }

        def move_message(
            self,
            *,
            account: str,
            message_id: str,
            destination_folder: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            move_message_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "destination_folder": destination_folder,
                    "folder": folder,
                }
            )
            return {"ok": True}

        def mark_message_read(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
            read: bool = True,
        ) -> dict[str, object]:
            mark_message_read_calls.append(
                {
                    "account": account,
                    "message_id": message_id,
                    "folder": folder,
                    "read": read,
                }
            )
            return {"ok": True}

        def delete_message(
            self,
            *,
            account: str,
            message_id: str,
            folder: str | None = None,
        ) -> dict[str, object]:
            delete_message_calls.append(
                {"account": account, "message_id": message_id, "folder": folder}
            )
            return {"ok": True}

    class FakeFastMCP:
        def __init__(
            self,
            name: str,
            *,
            stateless_http: bool,
            json_response: bool,
        ) -> None:
            self.name = name
            self.stateless_http = stateless_http
            self.json_response = json_response
            self.settings = SimpleNamespace(
                host="",
                port=0,
                streamable_http_path="",
            )
            self._mcp_server = SimpleNamespace(version="")
            self.run_transport = ""

        def tool(
            self, **kwargs: object
        ) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(func: Callable[..., object]) -> Callable[..., object]:
                tools[func.__name__] = func
                return func

            return decorator

        def run(self, *, transport: str) -> None:
            self.run_transport = transport

    fastmcp_module = ModuleType("mcp.server.fastmcp")
    setattr(fastmcp_module, "FastMCP", FakeFastMCP)
    server_module = ModuleType("mcp.server")
    mcp_module = ModuleType("mcp")

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    monkeypatch.setattr(
        "agent_arbiter.main.build_app",
        lambda cfg, service_plugins=None, runtime_dependencies=None: FakeApp(),
    )

    cfg = OmegaConf.structured(fake_cfg)

    server = cast(Any, build_server(cfg, service_plugins=_test_service_plugins()))

    assert server.name == "agent-arbiter"
    assert server.stateless_http is True
    assert server.json_response is True
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.streamable_http_path == "/mcp"
    assert server._mcp_server.version != ""
    assert sorted(tools) == sorted(CORE_TOOL_NAMES)

    assert tools["version_info"]() == {
        "core": {"version": "0.8.0", "api_version": "0.8"},
        "plugins": [
            {"name": "imap", "version": "0.8.0", "core_api_version": "0.8"},
            {"name": "smtp", "version": "0.8.0", "core_api_version": "0.8"},
        ],
    }
    assert tools["list_caps"]() == {"capabilities": ["imap", "smtp"]}

    capabilities = cast(dict[str, Any], tools["describe_caps"]())
    assert capabilities["capabilities"] == [
        {
            "id": "imap",
            "description": "Read and manage mail through configured IMAP accounts.",
            "account_count": 1,
            "accounts": ["primary"],
            "accounts_truncated": False,
            "operation_count": 6,
            "operations": [
                "delete_message",
                "get_message",
                "list_messages",
                "mark_message_read",
                "move_message",
                "search_messages",
            ],
            "operations_truncated": False,
        },
        {
            "id": "smtp",
            "description": "Send email through configured SMTP accounts.",
            "account_count": 1,
            "accounts": ["primary"],
            "accounts_truncated": False,
            "operation_count": 1,
            "operations": ["send_email"],
            "operations_truncated": False,
        },
    ]

    limited_capabilities = cast(
        dict[str, Any],
        tools["describe_caps"](operation_preview_limit=3, account_preview_limit=1),
    )
    imap_limited = limited_capabilities["capabilities"][0]
    assert imap_limited["id"] == "imap"
    assert imap_limited["operations"] == [
        "delete_message",
        "get_message",
        "list_messages",
    ]
    assert imap_limited["operations_truncated"] is True

    with pytest.raises(ValueError, match="operation_preview_limit must be >= 0"):
        tools["describe_caps"](operation_preview_limit=-1)

    smtp_capability = tools["describe_cap"](capability="smtp")
    assert smtp_capability == {
        "id": "smtp",
        "description": "Send email through configured SMTP accounts.",
        "accounts": {
            "primary": {
                "description": "Bot-owned account for automated email tasks.",
                "policy": "bot",
                "enabled": True,
                "send": "allowed",
                "require_confirmation": False,
            },
        },
        "operations": [
            {
                "id": "smtp:send_email",
                "name": "send_email",
                "description": (
                    "Send a single email message through the configured SMTP "
                    "submission server for the selected account. Use at least one "
                    "recipient in to and at least one of text_body or html_body."
                ),
            },
        ],
    }

    imap_capability = cast(
        dict[str, Any],
        tools["describe_cap"](capability="imap"),
    )
    assert imap_capability["accounts"] == {
        "primary": {
            "description": "",
            "policy": "bot",
            "enabled": True,
            "confirmation_required": [],
            "message": {
                "read_allowed": True,
                "move_allowed": True,
                "delete_allowed": True,
                "flags": {
                    "seen": "read_only",
                    "flagged": "read_only",
                    "answered": "read_only",
                    "deleted": "read_only",
                    "draft": "read_only",
                },
            },
        }
    }
    assert [operation["id"] for operation in imap_capability["operations"]] == [
        "imap:delete_message",
        "imap:get_message",
        "imap:list_messages",
        "imap:mark_message_read",
        "imap:move_message",
        "imap:search_messages",
    ]

    smtp_operation = cast(
        dict[str, Any],
        tools["describe_op"](id="smtp:send_email"),
    )
    assert smtp_operation["input_schema"]["required"] == ["account", "to", "subject"]

    send_result = tools["run_op"](
        id="smtp:send_email",
        arguments={
            "account": "primary",
            "to": ["to@example.com"],
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
            "subject": "Hello",
            "text_body": "Plain body",
        },
    )

    assert send_result == {
        "ok": True,
        "message_id": "<message-id@example.com>",
        "recipient_count": 3,
    }
    assert send_email_calls == [
        {
            "account": "primary",
            "to": ["to@example.com"],
            "subject": "Hello",
            "text_body": "Plain body",
            "html_body": None,
            "cc": ["cc@example.com"],
            "bcc": ["bcc@example.com"],
        }
    ]

    assert tools["run_op"](
        id="imap:list_messages",
        arguments={"account": "primary", "folder": "INBOX", "limit": 5},
    ) == {
        "account": "primary",
        "folder": "INBOX",
        "messages": [],
    }
    assert list_messages_calls == [
        {"account": "primary", "folder": "INBOX", "limit": 5}
    ]

    assert tools["run_op"](
        id="imap:get_message",
        arguments={"account": "primary", "folder": "INBOX", "message_id": "42"},
    ) == {
        "account": "primary",
        "folder": "INBOX",
        "message": {},
    }

    capped_tools: dict[str, Callable[..., object]] = {}

    class CappedFakeFastMCP(FakeFastMCP):
        def tool(
            self, **kwargs: object
        ) -> Callable[[Callable[..., object]], Callable[..., object]]:
            def decorator(func: Callable[..., object]) -> Callable[..., object]:
                capped_tools[func.__name__] = func
                return func

            return decorator

    setattr(fastmcp_module, "FastMCP", CappedFakeFastMCP)
    capped_cfg = _app_config_with_smtp_imap()
    capped_cfg.arbiter.discovery = DiscoveryConfig(
        max_account_preview_limit=25,
        max_operation_preview_limit=2,
    )
    build_server(
        OmegaConf.structured(capped_cfg), service_plugins=_test_service_plugins()
    )
    capped_capabilities = cast(
        dict[str, Any],
        capped_tools["describe_caps"](operation_preview_limit=99),
    )
    assert capped_capabilities["capabilities"][0]["operations"] == [
        "delete_message",
        "get_message",
    ]
    assert capped_capabilities["capabilities"][0]["operations_truncated"] is True
    assert get_message_calls == [
        {"account": "primary", "message_id": "42", "folder": "INBOX"}
    ]

    assert tools["run_op"](
        id="imap:search_messages",
        arguments={
            "account": "primary",
            "query": "invoice",
            "folder": "INBOX",
            "limit": 10,
        },
    ) == {
        "account": "primary",
        "folder": "INBOX",
        "query": "invoice",
        "messages": [],
    }
    assert search_messages_calls == [
        {
            "account": "primary",
            "query": "invoice",
            "folder": "INBOX",
            "limit": 10,
        }
    ]

    assert tools["run_op"](
        id="imap:move_message",
        arguments={
            "account": "primary",
            "message_id": "42",
            "destination_folder": "Archive",
            "folder": "INBOX",
        },
    ) == {"ok": True}
    assert move_message_calls == [
        {
            "account": "primary",
            "message_id": "42",
            "destination_folder": "Archive",
            "folder": "INBOX",
        }
    ]

    assert tools["run_op"](
        id="imap:mark_message_read",
        arguments={
            "account": "primary",
            "message_id": "42",
            "folder": "INBOX",
            "read": False,
        },
    ) == {"ok": True}
    assert mark_message_read_calls == [
        {
            "account": "primary",
            "message_id": "42",
            "folder": "INBOX",
            "read": False,
        }
    ]

    assert tools["run_op"](
        id="imap:delete_message",
        arguments={"account": "primary", "message_id": "42", "folder": "INBOX"},
    ) == {"ok": True}
    assert delete_message_calls == [
        {"account": "primary", "message_id": "42", "folder": "INBOX"}
    ]

    with pytest.raises(
        ValueError,
        match="smtp:send_email missing required argument\\(s\\): subject",
    ):
        tools["run_op"](
            id="smtp:send_email",
            arguments={"account": "primary", "to": ["to@example.com"]},
        )

    with pytest.raises(
        ValueError,
        match="smtp:send_email received unknown argument\\(s\\): reply_to",
    ):
        tools["run_op"](
            id="smtp:send_email",
            arguments={
                "account": "primary",
                "to": ["to@example.com"],
                "subject": "Hello",
                "reply_to": "reply@example.com",
            },
        )

    with pytest.raises(
        ValueError,
        match="imap:list_messages argument limit must be integer",
    ):
        tools["run_op"](
            id="imap:list_messages",
            arguments={"account": "primary", "limit": "5"},
        )


@pytest.mark.parametrize(
    ("transport", "expected_app"),
    [
        ("streamable-http", "streamable-http-app"),
        ("sse", "sse-app"),
    ],
)
def test_run_server_preserves_hydra_logging_for_uvicorn_transports(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    expected_app: str,
) -> None:
    captured: dict[str, object] = {}

    class FakeUvicornConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            captured["app"] = app
            captured["config_kwargs"] = kwargs

    class FakeUvicornServer:
        def __init__(self, config: FakeUvicornConfig) -> None:
            captured["config"] = config

        async def serve(self) -> None:
            captured["served"] = True

    fake_uvicorn = ModuleType("uvicorn")
    setattr(fake_uvicorn, "Config", FakeUvicornConfig)
    setattr(fake_uvicorn, "Server", FakeUvicornServer)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    def streamable_http_app() -> str:
        return "streamable-http-app"

    def sse_app(mount_path: str | None) -> str:
        captured["sse_mount_path"] = mount_path
        return "sse-app"

    fake_server = SimpleNamespace(
        settings=SimpleNamespace(host="127.0.0.1", port=8025, log_level="INFO"),
        streamable_http_app=streamable_http_app,
        sse_app=sse_app,
    )

    _run_server(cast(Any, fake_server), cast(Any, transport))

    assert captured["app"] == expected_app
    assert captured["served"] is True
    assert captured["config_kwargs"] == {
        "host": "127.0.0.1",
        "port": 8025,
        "log_level": "info",
        "log_config": None,
    }
    if transport == "sse":
        assert captured["sse_mount_path"] is None


def test_run_server_keeps_stdio_on_fastmcp_runner() -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.transport = ""

        def run(self, *, transport: str) -> None:
            self.transport = transport

    fake_server = FakeServer()

    _run_server(cast(Any, fake_server), "stdio")

    assert fake_server.transport == "stdio"


def test_build_server_describes_send_email_tool_schema() -> None:
    server = cast(
        Any,
        build_server(
            OmegaConf.structured(_app_config_with_smtp_imap()),
            service_plugins=_test_service_plugins(),
        ),
    )

    assert sorted(server._tool_manager._tools) == sorted(CORE_TOOL_NAMES)

    describe_op_tool = server._tool_manager._tools["describe_op"]
    assert "Operation ids use CAPABILITY:OPERATION syntax" in (
        describe_op_tool.description
    )
    assert describe_op_tool.parameters["properties"]["id"]["type"] == "string"

    run_op_tool = server._tool_manager._tools["run_op"]
    assert "Run one Agent Arbiter operation by id" in run_op_tool.description
    run_parameters = run_op_tool.parameters["properties"]
    assert run_parameters["id"]["type"] == "string"
    assert run_parameters["arguments"]["anyOf"] == [
        {
            "additionalProperties": True,
            "type": "object",
        },
        {"type": "null"},
    ]
