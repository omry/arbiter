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

from arbiter_server.config import AppConfig, ArbiterConfig, DiscoveryConfig
from arbiter_server.app import SERVER_TOOL_NAMES
from arbiter_server.file_protection.windows import (
    _WindowsAccessAce,
    _windows_icacls_remediation,
    _windows_unallowed_access_reason,
    _windows_unallowed_permission_reason,
    ensure_runtime_config_permissions as ensure_windows_runtime_config_permissions,
)
from arbiter_server.main import (
    ENV_FILE_MODE,
    _build_local_source_wheel,
    _default_container_user,
    _run_server,
    _write_text_with_mode,
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
from arbiter_server.plugins import discover_service_plugins
from arbiter_imap import IMAPRuntime, IMAPServicePlugin
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFolderConfig,
)
from arbiter_smtp import SendEmailResult, SMTPRuntime, SMTPServicePlugin
from arbiter_smtp.config import SMTPConfig, SMTPServicePolicyConfig
from arbiter_server.services import (
    SERVER_API_VERSION,
    SERVER_VERSION,
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    CapabilityDescriptor,
    OperationDescriptor,
    RuntimeRegistry,
    ServicePlugin,
    ServicePluginContext,
)


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
        "source": {"commit": commit, "dirty": dirty},
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

    assert app.tool_names() == list(SERVER_TOOL_NAMES)


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
    assert app.tool_names() == list(SERVER_TOOL_NAMES)
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
        "arbiter:\n" "  env_file: local.env\n" "  server:\n" "    transport: stdio\n",
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
        "arbiter:\n" "  env_file: local.env\n" "  server:\n" "    transport: stdio\n",
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
        "arbiter:\n  server:\n    transport: stdio\n", encoding="utf-8"
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
    assert "unsafe config file permissions" in stderr
    assert "Builtin Users" in stderr


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
    assert (
        '"$$venv_python" -m pip --disable-pip-version-check wheel --no-cache-dir '
        "--no-deps --wheel-dir"
    ) in compose_text
    assert (
        '"$$venv_python" -m pip --disable-pip-version-check install --no-cache-dir '
        "-r /tmp/requirements.pinned /tmp/arbiter-wheels/*.whl"
    ) in compose_text
    assert "${ARBITER_WHEELS_DIR:-./wheels}:/wheels:ro" in compose_text
    assert "container_name: ${ARBITER_CONTAINER_NAME:-arbiter-staging}" in compose_text
    assert "user: ${ARBITER_CONTAINER_USER:-10001:10001}" in compose_text
    assert "ARBITER_SERVER_HOST: 0.0.0.0" in compose_text
    assert "ARBITER_RUNTIME_VENV: ${ARBITER_RUNTIME_VENV:-/tmp/arbiter-venv}" in (
        compose_text
    )
    assert 'case "$$runtime_venv" in /tmp/arbiter-*)' in compose_text
    assert 'case "$$runtime_venv" in *..*)' in compose_text
    assert 'case "$$HOME" in /tmp/arbiter-*)' in compose_text
    assert 'case "$$HOME" in *..*)' in compose_text
    assert 'python -m venv "$$runtime_venv"' in compose_text
    assert 'exec "$$runtime_venv/bin/arbiter-server"' in compose_text
    assert (
        '"${ARBITER_HOST_BIND:-127.0.0.1}:'
        '${ARBITER_HOST_PORT:-18025}:${ARBITER_CONTAINER_PORT:-8025}"'
    ) in compose_text
    assert "name: ${ARBITER_DOCKER_NETWORK_NAME:-arbiter-staging}" in compose_text
    assert (
        'com.docker.network.bridge.name: "${ARBITER_DOCKER_BRIDGE_NAME:-arbiter-stg0}"'
        in compose_text
    )
    assert 'subnet: "${ARBITER_DOCKER_SUBNET:-172.31.251.0/24}"' in compose_text
    assert "ARBITER_DEPLOYMENT_SCOPE" not in compose_text
    assert (
        '"arbiter.server.host=$$ARBITER_SERVER_HOST" '
        '"arbiter.server.port=$$ARBITER_CONTAINER_PORT" '
        '"arbiter.deployment_scope=staged"'
    ) in compose_text
    assert not (deploy_dir / "config.yaml").exists()
    assert (deploy_dir / "conf").is_dir()
    assert not (deploy_dir / "conf" / ".env").exists()
    docker_env = (deploy_dir / "docker.env").read_text(encoding="utf-8")
    assert "ARBITER_DEPLOYMENT_SCOPE" not in docker_env
    assert "ARBITER_CONTAINER_NAME=arbiter-staging\n" in docker_env
    assert f"ARBITER_CONTAINER_USER={_default_container_user()}\n" in docker_env
    assert "ARBITER_HOST_BIND=127.0.0.1\n" in docker_env
    assert "ARBITER_HOST_PORT=18025\n" in docker_env
    assert "ARBITER_WHEELS_DIR=./wheels\n" in docker_env
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
        "arbiter-server==0.9.0\n" "arbiter-smtp==0.9.1\n" "arbiter-imap==0.9.0\n"
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
    assert result.stdout == "imap\nsmtp\n"


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


def test_cli_deploy_docker_generated_helper_up_prints_mcp_url(
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
    assert result.stdout == (
        " ✔ Staging MCP port: 8025 -> 18025 to prevent collision\n"
        " ✔ MCP URL: http://127.0.0.1:18025/mcp\n"
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
        " \033[32m✔\033[0m Staging MCP port: 8025 -> 18025 to prevent collision\n"
        " \033[32m✔\033[0m MCP URL: "
        "\033[94mhttp://127.0.0.1:18025/mcp\033[0m\n"
    )
    assert result.stderr == ""

    docker_env.write_text(
        docker_env.read_text(encoding="utf-8").replace(
            "ARBITER_HOST_PORT=18025\n",
            "ARBITER_HOST_PORT=8025\n",
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
    assert result.stdout == " ✔ MCP URL: http://127.0.0.1:8025/mcp\n"
    assert result.stderr == ""

    docker_call_text = docker_calls.read_text(encoding="utf-8")
    assert "info\n" in docker_call_text
    assert "inspect arbiter-staging --format" in docker_call_text
    assert "compose --env-file" in docker_call_text
    assert "up -d\n" in docker_call_text


def test_cli_deploy_docker_generated_helper_test_calls_version_info(
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
    arbiter_calls = tmp_path / "arbiter-calls"
    fake_arbiter = fake_bin / "arbiter"
    fake_arbiter_count = tmp_path / "arbiter-count"
    fake_arbiter.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{arbiter_calls}"\n'
        "count=0\n"
        f'if [ -f "{fake_arbiter_count}" ]; then count="$(cat "{fake_arbiter_count}")"; fi\n'
        f'printf "%s\\n" "$((count + 1))" > "{fake_arbiter_count}"\n'
        'if [ "$count" -lt "${ARBITER_TEST_CONNECT_FAILURES:-0}" ]; then\n'
        "  printf 'Arbiter connection error: could not connect\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        'exit "${ARBITER_TEST_STATUS:-0}"\n',
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
    assert result.stdout == " ✔ MCP test: http://127.0.0.1:18025/mcp\n"
    assert result.stderr == ""
    assert arbiter_calls.read_text(encoding="utf-8") == (
        "mcp call version_info arbiter.mcp_url=http://127.0.0.1:18025/mcp\n"
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
    assert result.stdout == " ✔ MCP test: http://127.0.0.1:18025/mcp\n"
    assert result.stderr == ""
    assert fake_arbiter_count.read_text(encoding="utf-8") == "3\n"

    result = subprocess.run(
        [deploy_dir / "arbiter-docker", "test"],
        check=False,
        cwd=tmp_path,
        env={**env, "ARBITER_COLOR": "always", "ARBITER_TEST_STATUS": "7"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout == (
        " \033[31m✘\033[0m MCP test: " "\033[94mhttp://127.0.0.1:18025/mcp\033[0m\n"
    )
    assert result.stderr == ""


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
    assert " ✔ Staging MCP port: 8025 -> 18025 to prevent collision\n" in result.stdout
    assert " ✔ MCP URL: http://127.0.0.1:18025/mcp\n" in result.stdout
    assert result.stderr == ""
    assert "ARBITER_DOCKER_SUBNET=10.213.200.0/24\n" in (
        deploy_dir / "docker.env"
    ).read_text(encoding="utf-8")
    assert "compose --env-file" in docker_calls.read_text(encoding="utf-8")


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
        "restart=unless-stopped\n"
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
        "restart=unless-stopped\n"
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
        "ok: requirements file uses exact pins or absolute container paths"
        in valid_result.stdout
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
        "arbiter-server, arbiter-smtp, or arbiter-imap pins"
    ) in result.stdout
    assert (
        "fail: requirements file contains unpinned package requirements"
        in result.stdout
    )


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
    (deploy_dir / "requirements.txt").write_text(
        "/source/arbiter/server\n",
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
        "fail: install requires package or wheel requirements, but this file "
        "uses the local checkout: "
        f"{deploy_dir / 'requirements.txt'}\n"
    ) in result.stdout
    assert (
        "      edit requirements.txt to use exact package pins such as "
        "arbiter-server==VERSION, arbiter-smtp==VERSION, and "
        "arbiter-imap==VERSION\n"
    ) in result.stdout
    assert (
        "      alternatively, use absolute container wheel paths such as "
        "/wheels/arbiter_server-VERSION-py3-none-any.whl\n"
    ) in result.stdout
    assert (
        "fail: install cannot keep the local checkout mounted into the container: "
        f"{deploy_dir / 'compose.override.yaml'}\n"
    ) in result.stdout
    assert (
        "      after switching requirements away from /source/arbiter, remove "
        "compose.override.yaml or delete the /source/arbiter volume from it\n"
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
    assert result.stdout.startswith(
        "installing Arbiter to /opt/arbiter as arbiter:arbiter "
        "(service: arbiter.service)\n"
    )
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
    assert "would run: systemctl restart arbiter.service\n" in result.stdout


def test_cli_deploy_docker_generated_helper_install_omits_missing_docker_unit(
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
        "#!/usr/bin/env sh\n" f'printf "%s\\n" "$*" >> "{docker_calls}"\n' "exit 0\n",
        encoding="utf-8",
    )
    systemctl_calls = tmp_path / "systemctl-calls"
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = cat ] && [ "$2" = docker.service ]; then exit 1; fi\n'
        f'printf "%s\\n" "$*" >> "{systemctl_calls}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "arbiter").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    for fake_command in ("id", "getent", "docker", "systemctl", "arbiter", "chown"):
        (fake_bin / fake_command).chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARBITER_SYSTEMD_DIR"] = str(systemd_dir)
    env["ARBITER_COLOR"] = "always"

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
        "\033[33mwarn\033[0m: docker.service not found; generated unit will "
        "rely on Docker socket availability\n"
    ) in result.stdout
    assert "success: installed Arbiter Docker deployment\n" in result.stdout
    assert f"installed to: {install_dir}\n" in result.stdout
    assert f"systemd unit: {systemd_dir / 'arbiter.service'}\n" in result.stdout
    assert "service: arbiter.service enabled and restarted\n" in result.stdout
    assert result.stderr == ""
    installed_compose = (install_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "arbiter.deployment_scope=installed" in installed_compose
    assert "ARBITER_CONTAINER_NAME:-arbiter-staging" not in installed_compose
    assert "ARBITER_HOST_PORT:-18025" not in installed_compose
    assert "ARBITER_DOCKER_NETWORK_NAME:-arbiter-staging" not in installed_compose
    assert "ARBITER_DOCKER_BRIDGE_NAME:-arbiter-stg0" not in installed_compose
    assert "ARBITER_DOCKER_SUBNET:-172.31.251.0/24" not in installed_compose
    assert "ARBITER_CONTAINER_NAME:-arbiter" in installed_compose
    assert "ARBITER_HOST_BIND:-127.0.0.1" in installed_compose
    assert "ARBITER_HOST_PORT:-8025" in installed_compose
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
    assert "ARBITER_HOST_PORT=8025\n" in installed_docker_env
    assert "ARBITER_DOCKER_NETWORK_NAME=arbiter\n" in installed_docker_env
    assert "ARBITER_DOCKER_BRIDGE_NAME=arbiter0\n" in installed_docker_env
    assert "ARBITER_DOCKER_SUBNET=172.31.250.0/24\n" in installed_docker_env
    unit_text = (systemd_dir / "arbiter.service").read_text(encoding="utf-8")
    assert "Requires=docker.service\n" not in unit_text
    assert "After=docker.service\n" not in unit_text
    assert f"WorkingDirectory={install_dir}\n" in unit_text
    assert docker_calls.read_text(encoding="utf-8") == (
        "info\n"
        "run --rm --user 123:123 "
        f"-v {install_dir / 'requirements.txt'}:/requirements.txt:ro "
        f"-v {install_dir / 'wheels'}:/wheels:ro "
        "python:3.11-slim python -m pip --disable-pip-version-check "
        "install --no-cache-dir "
        "--target /tmp/arbiter-wheelhouse-check --no-index --find-links /wheels "
        "-r /requirements.txt\n"
    )
    assert systemctl_calls.read_text(encoding="utf-8") == (
        "daemon-reload\n" "enable arbiter.service\n" "restart arbiter.service\n"
    )


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


def test_cli_deploy_docker_generated_helper_install_can_replace_existing_config(
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
            "--replace-config",
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
    assert (installed_config_dir / ".env").read_text(encoding="utf-8") == (
        "SECRET=staging\n"
    )


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
    assert result.stdout.startswith(
        f"installing Arbiter to {install_dir} as arbiter:arbiter "
        "(service: arbiter.service)\n"
    )
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
        "ARBITER_RESTART=unless-stopped\n"
        "ARBITER_APP_ENV_FILE=./conf/.env\n"
        "ARBITER_CONFIG_DIR=./conf\n"
        "ARBITER_CONFIG_NAME=arbiter-server\n"
        "ARBITER_REQUIREMENTS_FILE=./requirements.txt\n"
        "ARBITER_WHEELS_DIR=./wheels\n"
        "ARBITER_HOST_BIND=0.0.0.0\n"
        "ARBITER_HOST_PORT=9000\n"
        "ARBITER_CONTAINER_PORT=8025\n"
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
        "deployment scope unknown\n"
        "source abc123 dirty\n"
        "plugins:\n"
        f"  {plugins[0]['name']} {plugins[0]['version']} "
        f"(server api {plugins[0]['server_api_version']})\n"
        f"  {plugins[1]['name']} {plugins[1]['version']} "
        f"(server api {plugins[1]['server_api_version']})\n"
    )


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

    monkeypatch.setattr("arbiter_server.main._run_config_show", fake_show)

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
        "#   arbiter-server --config-dir <dir> serve arbiter.server.port=8025\n"
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
        "  transport: streamable-http\n"
        "  host: 127.0.0.1\n"
        "  port: 8000\n"
        "  path: /mcp\n"
        "  stateless_http: true\n"
        "  json_response: true\n"
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
    assert capsys.readouterr().err == (
        "Arbiter config error: config must define at least one service "
        "account before Arbiter can run\n"
        "  currently installed arbiter plugins: imap, smtp\n"
        "  use `arbiter-server --config-dir DIR bootstrap plugin PLUGIN account "
        "NAME` to create an account config\n"
    )

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
    policy_file = config_dir / "arbiter" / "policy" / "imap" / "bot_policy.yaml"
    policy_yaml = policy_file.read_text(encoding="utf-8")
    assert "# @package arbiter.policy.imap.bot_policy\n" in policy_yaml
    assert "defaults:\n" in policy_yaml
    assert "  - schema@_here_\n" in policy_yaml
    assert "  - _self_\n" in policy_yaml
    assert (
        "# Read/search are enabled by default; mutating mailbox actions are disabled.\n"
        in (policy_yaml)
    )
    assert "allow_read: true\n" in policy_yaml
    assert "allow_search: true\n" in policy_yaml
    assert "allow_move: false\n" in policy_yaml
    assert "allow_delete: false\n" in policy_yaml
    assert "seen: read_only\n" in policy_yaml
    assert "user_flags: {}\n" in policy_yaml
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

    monkeypatch.setattr("arbiter_server.main.arbiter_server_version", lambda: "1.2.3")
    caplog.set_level(logging.INFO, logger="arbiter_server.main")

    log_startup_summary(cfg)

    message = caplog.messages[0]
    assert "Arbiter starting version=1.2.3" in message
    assert "deployment_scope=unknown" in message
    assert "transport=streamable-http" in message
    assert "bind=127.0.0.1:8000/mcp" in message
    assert "mcp_url=http://127.0.0.1:8000/mcp" in message
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
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "ok",
                    "stage": "connect_auth_noop",
                    "delivery": "skipped",
                }
            }

        def send_email(
            self,
            account: str,
            to: list[str],
            subject: str,
            text_body: str | None = None,
            html_body: str | None = None,
            cc: list[str] | None = None,
            bcc: list[str] | None = None,
            idempotency_key: str | None = None,
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
                    "idempotency_key": idempotency_key,
                }
            )
            return SendEmailResult(
                tool="send_email",
                message_id="<message-id@example.com>",
                recipient_count=len(to) + len(cc or []) + len(bcc or []),
            )

    class FakeIMAPRuntime(IMAPRuntime):
        def test_accounts(self) -> dict[str, object]:
            return {
                "primary": {
                    "status": "skipped",
                    "stage": "connect_auth_noop",
                    "reason": "test disabled in fixture",
                }
            }

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
            idempotency_key: str | None = None,
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
                    "idempotency_key": idempotency_key,
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
        "arbiter_server.main.build_app",
        lambda cfg, service_plugins=None, runtime_dependencies=None: FakeApp(),
    )
    monkeypatch.setattr(
        "arbiter_server.main.source_info",
        lambda: SimpleNamespace(commit=None, dirty=None),
    )

    cfg = OmegaConf.structured(fake_cfg)

    server = cast(Any, build_server(cfg, service_plugins=_test_service_plugins()))

    assert server.name == "arbiter"
    assert server.stateless_http is True
    assert server.json_response is True
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.streamable_http_path == "/mcp"
    assert server._mcp_server.version != ""
    assert sorted(tools) == sorted(SERVER_TOOL_NAMES)

    assert tools["version_info"]() == _expected_version_info(
        commit=None,
        dirty=None,
    )
    overview = cast(dict[str, Any], tools["info"]())
    assert overview["kind"] == "overview"
    assert overview["deployment_scope"] == "unknown"
    assert overview["plugins"][0] == {
        "id": "imap",
        "description": "Read and manage mail through configured IMAP accounts.",
        "version": IMAPServicePlugin.version,
        "account_count": 1,
        "operation_count": 6,
        "accounts": [
            {
                "plugin": "imap",
                "name": "primary",
                "description": "",
                "guidance": "",
            }
        ],
    }
    plugins_info = cast(dict[str, Any], tools["info"](kind="plugins"))
    plugins = cast(list[dict[str, Any]], plugins_info["plugins"])
    assert plugins[1] == {
        "id": "smtp",
        "description": "Send email through configured SMTP accounts.",
        "version": SMTPServicePlugin.version,
        "account_count": 1,
        "operation_count": 1,
    }
    smtp_account_info = cast(
        dict[str, Any],
        tools["info"](kind="account", plugin="smtp", account="primary"),
    )
    assert smtp_account_info["kind"] == "account"
    assert smtp_account_info["description"] == (
        "Bot-owned account for automated email tasks."
    )
    assert smtp_account_info["policy"] == "bot"
    assert smtp_account_info["guidance"] == ""
    tests_info = cast(dict[str, Any], tools["info"](kind="tests"))
    assert tests_info == {
        "kind": "tests",
        "plugins": [
            {
                "plugin": "imap",
                "accounts": [
                    {
                        "plugin": "imap",
                        "account": "primary",
                        "status": "skipped",
                        "stage": "connect_auth_noop",
                        "reason": "test disabled in fixture",
                    }
                ],
            },
            {
                "plugin": "smtp",
                "accounts": [
                    {
                        "plugin": "smtp",
                        "account": "primary",
                        "status": "ok",
                        "stage": "connect_auth_noop",
                        "delivery": "skipped",
                    }
                ],
            },
        ],
    }
    smtp_account_test = cast(
        dict[str, Any],
        tools["info"](kind="test", plugin="smtp", account="primary"),
    )
    assert smtp_account_test == {
        "kind": "test",
        "plugin": "smtp",
        "account": "primary",
        "status": "ok",
        "stage": "connect_auth_noop",
        "delivery": "skipped",
    }
    smtp_operation_info = cast(
        dict[str, Any],
        tools["info"](kind="op", plugin="smtp", operation="send_email"),
    )
    assert smtp_operation_info["id"] == "smtp:send_email"
    assert smtp_operation_info["input_schema"]["required"] == [
        "account",
        "to",
        "subject",
    ]
    assert tools["list_caps"]() == {"capabilities": ["imap", "smtp"]}

    capabilities = cast(dict[str, Any], tools["describe_caps"]())
    assert capabilities["capabilities"] == [
        {
            "id": "imap",
            "description": "Read and manage mail through configured IMAP accounts.",
            "version": IMAPServicePlugin.version,
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
            "version": SMTPServicePlugin.version,
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
                "guidance": "",
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
            "guidance": "",
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
    assert "idempotency_key" in smtp_operation["input_schema"]["properties"]

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
        "idempotency_replayed": False,
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
            "idempotency_key": None,
        }
    ]

    send_idempotent_result = tools["run_op"](
        id="smtp:send_email",
        arguments={
            "account": "primary",
            "to": ["to@example.com"],
            "subject": "Hello",
            "text_body": "Plain body",
            "idempotency_key": "send-1",
        },
    )

    assert send_idempotent_result == {
        "ok": True,
        "message_id": "<message-id@example.com>",
        "recipient_count": 1,
        "idempotency_replayed": False,
    }
    assert send_email_calls[-1] == {
        "account": "primary",
        "to": ["to@example.com"],
        "subject": "Hello",
        "text_body": "Plain body",
        "html_body": None,
        "cc": None,
        "bcc": None,
        "idempotency_key": "send-1",
    }

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

    assert sorted(server._tool_manager._tools) == sorted(SERVER_TOOL_NAMES)

    describe_op_tool = server._tool_manager._tools["describe_op"]
    assert "Operation ids use CAPABILITY:OPERATION syntax" in (
        describe_op_tool.description
    )
    assert describe_op_tool.parameters["properties"]["id"]["type"] == "string"

    run_op_tool = server._tool_manager._tools["run_op"]
    assert "Run one Arbiter operation by id" in run_op_tool.description
    run_parameters = run_op_tool.parameters["properties"]
    assert run_parameters["id"]["type"] == "string"
    assert run_parameters["arguments"]["anyOf"] == [
        {
            "additionalProperties": True,
            "type": "object",
        },
        {"type": "null"},
    ]
