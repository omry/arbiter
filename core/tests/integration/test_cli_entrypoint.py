from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import pytest

if TYPE_CHECKING:
    from conftest import LocalArbiterServerFactory

_GO_CLIENT_SMOKE_OUTDIR_ENV = "ARBITER_GO_CLIENT_SMOKE_OUTDIR"
_GO_CLIENT_SMOKE_REUSE_ENV = "ARBITER_GO_CLIENT_SMOKE_REUSE"


def _arbiter_server_command() -> Path:
    command = Path(sys.executable).with_name("arbiter-server")
    if not command.exists():
        raise AssertionError(f"arbiter-server console script not found: {command}")
    return command


def _arbiter_command() -> Path:
    command = Path(sys.executable).with_name("arbiter")
    if not command.exists():
        raise AssertionError(f"arbiter console script not found: {command}")
    return command


def _run_arbiter_server(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_arbiter_server_command()), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run_arbiter(
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_arbiter_command()), *args],
        check=False,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _current_go_target() -> tuple[str, str, str]:
    goos_by_platform = {
        "darwin": "darwin",
        "linux": "linux",
        "win32": "windows",
    }
    goarch_by_machine = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        goos = goos_by_platform[sys.platform]
        goarch = goarch_by_machine[platform.machine().lower()]
    except KeyError:
        pytest.skip(
            f"unsupported Go client smoke platform: "
            f"{sys.platform}/{platform.machine()}"
        )
    binary_name = "arbiter.exe" if goos == "windows" else "arbiter"
    return goos, goarch, binary_name


def _build_current_go_client(tmp_path: Path) -> Path:
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    goos, goarch, binary_name = _current_go_target()
    configured_outdir = os.environ.get(_GO_CLIENT_SMOKE_OUTDIR_ENV)
    if os.environ.get("CI") == "true" and not configured_outdir:
        pytest.skip(f"{_GO_CLIENT_SMOKE_OUTDIR_ENV} is required in CI")
    outdir = Path(configured_outdir) if configured_outdir else tmp_path / "go-client"
    if not outdir.is_absolute():
        outdir = _repo_root() / outdir
    binary = outdir / f"{goos}-{goarch}" / binary_name
    if os.environ.get(_GO_CLIENT_SMOKE_REUSE_ENV) == "1" and binary.exists():
        return binary

    env = os.environ.copy()
    result = subprocess.run(
        [
            str(_repo_root() / "tools" / "build_go_client"),
            "--root",
            str(_repo_root()),
            "--outdir",
            str(outdir),
            "--target",
            f"{goos}-{goarch}",
            "--skip-generate",
        ],
        check=False,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, (
        f"go client build failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert binary.exists()
    return binary


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--help",), "usage: arbiter-server "),
        (("--config-dir", ".", "serve", "--help"), "usage: arbiter-server serve "),
        (("--config-dir", ".", "config", "--help"), "usage: arbiter-server config "),
        (
            ("--config-dir", ".", "config", "check", "--help"),
            "usage: arbiter-server config check ",
        ),
        (
            ("--config-dir", ".", "config", "show", "--help"),
            "usage: arbiter-server config show ",
        ),
        (
            ("--config-dir", ".", "config", "activate", "--help"),
            "usage: arbiter-server config activate ",
        ),
        (
            ("--config-dir", ".", "config", "deactivate", "--help"),
            "usage: arbiter-server config deactivate ",
        ),
        (
            ("--config-dir", ".", "bootstrap", "--help"),
            "usage: arbiter-server bootstrap ",
        ),
        (
            ("--config-dir", ".", "bootstrap", "arbiter", "--help"),
            "usage: arbiter-server bootstrap arbiter ",
        ),
        (
            ("--config-dir", ".", "bootstrap", "plugin", "--help"),
            "usage: arbiter-server bootstrap plugin ",
        ),
        (("--config-dir", ".", "env", "--help"), "usage: arbiter-server env "),
        (
            ("--config-dir", ".", "env", "check", "--help"),
            "usage: arbiter-server env check ",
        ),
        (
            ("--config-dir", ".", "env", "bootstrap", "--help"),
            "usage: arbiter-server env bootstrap ",
        ),
        (
            ("--config-dir", ".", "deploy", "--help"),
            "usage: arbiter-server deploy ",
        ),
        (
            ("--config-dir", ".", "deploy", "docker", "--help"),
            "usage: arbiter-server deploy docker ",
        ),
        (
            ("--config-dir", ".", "plugins", "--help"),
            "usage: arbiter-server plugins ",
        ),
        (
            ("--config-dir", ".", "plugins", "list", "--help"),
            "usage: arbiter-server plugins list ",
        ),
    ],
)
def test_arbiter_console_script_help(
    args: tuple[str, ...],
    expected: str,
) -> None:
    result = _run_arbiter_server(*args)

    assert result.returncode == 0
    assert expected in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--help",), "usage: arbiter "),
        (("mcp", "--help"), "usage: arbiter mcp "),
        (("mcp", "tools", "--help"), "usage: arbiter mcp tools "),
        (("mcp", "call", "--help"), "usage: arbiter mcp call "),
        (("cap", "--help"), "usage: arbiter cap "),
        (("capabilities", "--help"), "usage: arbiter cap "),
        (("cap", "desc", "--help"), "usage: arbiter cap desc "),
        (("cap", "describe", "--help"), "usage: arbiter cap desc "),
        (("op", "--help"), "usage: arbiter op "),
        (("operation", "--help"), "usage: arbiter op "),
        (("op", "desc", "--help"), "usage: arbiter op desc "),
        (("op", "describe", "--help"), "usage: arbiter op desc "),
        (("op", "run", "--help"), "usage: arbiter op run "),
        (("accounts", "--help"), "usage: arbiter accounts "),
        (("accounts", "list", "--help"), "usage: arbiter accounts list "),
        (("accounts", "desc", "--help"), "usage: arbiter accounts desc "),
        (("accounts", "describe", "--help"), "usage: arbiter accounts desc "),
        (("bootstrap", "--help"), "usage: arbiter bootstrap "),
        (("bootstrap", "client", "--help"), "usage: arbiter bootstrap client "),
        (("--version",), "arbiter "),
    ],
)
def test_arbiter_client_console_script_help(
    args: tuple[str, ...],
    expected: str,
) -> None:
    result = _run_arbiter(*args)

    assert result.returncode == 0
    assert expected in result.stdout
    assert result.stderr == ""


def test_arbiter_console_script_env_bootstrap_and_check(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "arbiter-server.yaml"
    config_file.write_text(
        "arbiter:\n"
        "  account:\n"
        "    smtp:\n"
        "      primary:\n"
        "        username: ${oc.env:SMTP_PRIMARY_ACCOUNT_USERNAME}\n"
        "        password: ${oc.env:SMTP_PRIMARY_ACCOUNT_PASSWORD}\n",
        encoding="utf-8",
    )

    bootstrap = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "env",
        "bootstrap",
    )

    assert bootstrap.returncode == 0
    assert bootstrap.stdout == f"wrote {tmp_path / '.env'}\n"
    assert bootstrap.stderr == ""
    assert config_file.read_text(encoding="utf-8") == (
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

    check = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "env",
        "check",
    )

    assert check.returncode == 0
    assert check.stdout == "env ok: 2 variables satisfied\n"
    assert check.stderr == ""


def test_arbiter_console_script_bootstrap_arbiter(
    tmp_path: Path,
) -> None:
    result = _run_arbiter_server("bootstrap", "arbiter", "--config-dir", str(tmp_path))

    assert result.returncode == 0
    assert result.stdout == (
        f"wrote {tmp_path / 'arbiter-server.yaml'}\n"
        f"wrote {tmp_path / 'arbiter' / 'server.yaml'}\n"
    )
    assert result.stderr == ""
    assert (tmp_path / "arbiter-server.yaml").exists()


def test_arbiter_console_script_bootstrap_plugin_account(
    tmp_path: Path,
) -> None:
    result = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "plugin",
        "smtp",
        "account",
        "primary",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert (tmp_path / "arbiter" / "account" / "smtp" / "primary.yaml").exists()
    assert (tmp_path / "arbiter" / "policy" / "smtp" / "primary_policy.yaml").exists()


def test_arbiter_console_script_bootstrap_plugin_policy(
    tmp_path: Path,
) -> None:
    result = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "plugin",
        "smtp",
        "policy",
        "readonly",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert (tmp_path / "arbiter" / "policy" / "smtp" / "readonly.yaml").exists()


def test_arbiter_console_script_config_show_and_check(
    tmp_path: Path,
) -> None:
    bootstrap = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "arbiter",
    )
    assert bootstrap.returncode == 0
    account = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "plugin",
        "smtp",
        "account",
        "primary",
    )
    assert account.returncode == 0
    activate = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "config",
        "activate",
        "account",
        "smtp",
        "primary",
    )
    assert activate.returncode == 0
    env_bootstrap = _run_arbiter_server(
        "--config-dir", str(tmp_path), "env", "bootstrap"
    )
    assert env_bootstrap.returncode == 0

    show = _run_arbiter_server("--config-dir", str(tmp_path), "config", "show")
    check = _run_arbiter_server("--config-dir", str(tmp_path), "config", "check")

    assert show.returncode == 0
    assert "arbiter:" in show.stdout
    assert "primary:" in show.stdout
    assert show.stderr == ""
    assert check.returncode == 0
    assert check.stdout == "config ok: services=smtp service_accounts=smtp:primary\n"
    assert check.stderr == ""


def test_arbiter_console_script_config_deactivate(
    tmp_path: Path,
) -> None:
    assert (
        _run_arbiter_server(
            "--config-dir", str(tmp_path), "bootstrap", "arbiter"
        ).returncode
        == 0
    )
    assert (
        _run_arbiter_server(
            "--config-dir",
            str(tmp_path),
            "bootstrap",
            "plugin",
            "smtp",
            "account",
            "primary",
        ).returncode
        == 0
    )
    assert (
        _run_arbiter_server(
            "--config-dir",
            str(tmp_path),
            "config",
            "activate",
            "account",
            "smtp",
            "primary",
        ).returncode
        == 0
    )

    result = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "config",
        "deactivate",
        "account",
        "smtp",
        "primary",
    )

    assert result.returncode == 0
    assert result.stdout == f"updated {tmp_path / 'arbiter-server.yaml'}\n"
    assert result.stderr == ""


def test_arbiter_console_script_plugins_list() -> None:
    result = _run_arbiter_server("--config-dir", ".", "plugins", "list")

    assert result.returncode == 0
    assert result.stdout == "imap\nsmtp\n"
    assert result.stderr == ""


def test_arbiter_console_script_serve_reports_unrunnable_config(
    tmp_path: Path,
) -> None:
    result = _run_arbiter_server("--config-dir", str(tmp_path), "bootstrap", "arbiter")
    assert result.returncode == 0

    serve = _run_arbiter_server("--config-dir", str(tmp_path), "serve")

    assert serve.returncode == 1
    assert "config must define at least one service account" in serve.stderr
    assert serve.stdout == ""


@pytest.mark.parametrize(
    "args",
    [
        ("mcp", "tools"),
        ("mcp",),
        ("mcp", "tools", "--json"),
        ("mcp", "call", "list_caps"),
        ("cap",),
        ("accounts", "list"),
        ("accounts", "list", "--json"),
        ("accounts", "desc", "smtp"),
        ("accounts",),
    ],
)
def test_arbiter_client_console_script_reports_clean_connection_failure(
    args: tuple[str, ...],
) -> None:
    result = _run_arbiter(
        *args,
        "arbiter.mcp_url=http://127.0.0.1:9/mcp",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Arbiter connection error: could not connect to Arbiter at "
        "http://127.0.0.1:9/mcp (client override arbiter.mcp_url). "
        "Is arbiter-server serve running?\n"
    )


def test_arbiter_client_console_script_reads_client_config(
    tmp_path: Path,
) -> None:
    client_config = tmp_path / "arbiter-client.yaml"
    client_config.write_text(
        "arbiter:\n  mcp_url: http://127.0.0.1:9/mcp\n",
        encoding="utf-8",
    )

    result = _run_arbiter(
        "--config-dir",
        str(tmp_path),
        "accounts",
        "list",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Arbiter connection error: could not connect to Arbiter at "
        f"http://127.0.0.1:9/mcp (client config {client_config}). "
        "Is arbiter-server serve running?\n"
    )


def test_arbiter_client_console_script_bootstrap_client(
    tmp_path: Path,
) -> None:
    result = _run_arbiter(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "client",
        "arbiter.mcp_url=http://127.0.0.1:8025/mcp",
    )

    assert result.returncode == 0
    assert result.stdout == f"wrote {tmp_path / 'arbiter-client.yaml'}\n"
    assert result.stderr == ""
    assert (tmp_path / "arbiter-client.yaml").read_text(encoding="utf-8") == (
        "arbiter:\n  mcp_url: http://127.0.0.1:8025/mcp\n"
    )


def test_local_arbiter_server_fixture_serves_version_info(
    local_arbiter_server_factory: LocalArbiterServerFactory,
) -> None:
    server = local_arbiter_server_factory.start()

    result = server.run_client(
        "mcp",
        "call",
        "version_info",
        command=_arbiter_command(),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert "structuredContent" in payload


def test_current_platform_go_client_calls_local_arbiter_server(
    tmp_path: Path,
    local_arbiter_server_factory: LocalArbiterServerFactory,
) -> None:
    binary = _build_current_go_client(tmp_path)
    server = local_arbiter_server_factory.start()

    result = server.run_client(
        "mcp",
        "call",
        "version_info",
        command=binary,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert "structuredContent" in payload
