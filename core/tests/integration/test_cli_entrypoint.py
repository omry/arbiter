from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Mapping

import pytest


def _agent_arbiter_command() -> Path:
    command = Path(sys.executable).with_name("arbiter-server")
    if not command.exists():
        raise AssertionError(f"arbiter-server console script not found: {command}")
    return command


def _arbiter_command() -> Path:
    command = Path(sys.executable).with_name("arbiter")
    if not command.exists():
        raise AssertionError(f"arbiter console script not found: {command}")
    return command


def _run_agent_arbiter(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_agent_arbiter_command()), *args],
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
            ("--config-dir", ".", "plugins", "--help"),
            "usage: arbiter-server plugins ",
        ),
        (
            ("--config-dir", ".", "plugins", "list", "--help"),
            "usage: arbiter-server plugins list ",
        ),
    ],
)
def test_agent_arbiter_console_script_help(
    args: tuple[str, ...],
    expected: str,
) -> None:
    result = _run_agent_arbiter(*args)

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


def test_agent_arbiter_console_script_env_bootstrap_and_check(
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

    bootstrap = _run_agent_arbiter(
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
        "# agent-arbiter-smtp\n"
        "SMTP_PRIMARY_ACCOUNT_USERNAME=\n"
        "SMTP_PRIMARY_ACCOUNT_PASSWORD=\n"
    )

    check = _run_agent_arbiter(
        "--config-dir",
        str(tmp_path),
        "env",
        "check",
    )

    assert check.returncode == 0
    assert check.stdout == "env ok: 2 variables satisfied\n"
    assert check.stderr == ""


def test_agent_arbiter_console_script_bootstrap_arbiter(
    tmp_path: Path,
) -> None:
    result = _run_agent_arbiter("bootstrap", "arbiter", "--config-dir", str(tmp_path))

    assert result.returncode == 0
    assert result.stdout == (
        f"wrote {tmp_path / 'arbiter-server.yaml'}\n"
        f"wrote {tmp_path / 'arbiter' / 'server.yaml'}\n"
    )
    assert result.stderr == ""
    assert (tmp_path / "arbiter-server.yaml").exists()


def test_agent_arbiter_console_script_bootstrap_plugin_account(
    tmp_path: Path,
) -> None:
    result = _run_agent_arbiter(
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


def test_agent_arbiter_console_script_bootstrap_plugin_policy(
    tmp_path: Path,
) -> None:
    result = _run_agent_arbiter(
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


def test_agent_arbiter_console_script_config_show_and_check(
    tmp_path: Path,
) -> None:
    bootstrap = _run_agent_arbiter(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "arbiter",
    )
    assert bootstrap.returncode == 0
    account = _run_agent_arbiter(
        "--config-dir",
        str(tmp_path),
        "bootstrap",
        "plugin",
        "smtp",
        "account",
        "primary",
    )
    assert account.returncode == 0
    activate = _run_agent_arbiter(
        "--config-dir",
        str(tmp_path),
        "config",
        "activate",
        "account",
        "smtp",
        "primary",
    )
    assert activate.returncode == 0
    env_bootstrap = _run_agent_arbiter(
        "--config-dir", str(tmp_path), "env", "bootstrap"
    )
    assert env_bootstrap.returncode == 0

    show = _run_agent_arbiter("--config-dir", str(tmp_path), "config", "show")
    check = _run_agent_arbiter("--config-dir", str(tmp_path), "config", "check")

    assert show.returncode == 0
    assert "arbiter:" in show.stdout
    assert "primary:" in show.stdout
    assert show.stderr == ""
    assert check.returncode == 0
    assert check.stdout == "config ok: services=smtp service_accounts=smtp:primary\n"
    assert check.stderr == ""


def test_agent_arbiter_console_script_config_deactivate(
    tmp_path: Path,
) -> None:
    assert (
        _run_agent_arbiter(
            "--config-dir", str(tmp_path), "bootstrap", "arbiter"
        ).returncode
        == 0
    )
    assert (
        _run_agent_arbiter(
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
        _run_agent_arbiter(
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

    result = _run_agent_arbiter(
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


def test_agent_arbiter_console_script_plugins_list() -> None:
    result = _run_agent_arbiter("--config-dir", ".", "plugins", "list")

    assert result.returncode == 0
    assert result.stdout == "imap\nsmtp\n"
    assert result.stderr == ""


def test_agent_arbiter_console_script_serve_reports_unrunnable_config(
    tmp_path: Path,
) -> None:
    result = _run_agent_arbiter("--config-dir", str(tmp_path), "bootstrap", "arbiter")
    assert result.returncode == 0

    serve = _run_agent_arbiter("--config-dir", str(tmp_path), "serve")

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
        "mcp_url=http://127.0.0.1:9/mcp",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Could not connect to Agent Arbiter at http://127.0.0.1:9/mcp. "
        "Is arbiter-server serve running?\n"
    )


def test_arbiter_client_console_script_reads_client_config(
    tmp_path: Path,
) -> None:
    client_config = tmp_path / "arbiter-client.yaml"
    client_config.write_text("mcp_url: http://127.0.0.1:9/mcp\n", encoding="utf-8")

    result = _run_arbiter(
        "--config-dir",
        str(tmp_path),
        "accounts",
        "list",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Could not connect to Agent Arbiter at http://127.0.0.1:9/mcp. "
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
        "mcp_url=http://127.0.0.1:8025/mcp",
    )

    assert result.returncode == 0
    assert result.stdout == f"wrote {tmp_path / 'arbiter-client.yaml'}\n"
    assert result.stderr == ""
    assert (tmp_path / "arbiter-client.yaml").read_text(encoding="utf-8") == (
        "mcp_url: http://127.0.0.1:8025/mcp\n"
    )
