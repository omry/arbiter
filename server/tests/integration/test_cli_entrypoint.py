from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol

import pytest
from omegaconf import OmegaConf

_GO_CLIENT_SMOKE_OUTDIR_ENV = "ARBITER_GO_CLIENT_SMOKE_OUTDIR"
_GO_CLIENT_SMOKE_REUSE_ENV = "ARBITER_GO_CLIENT_SMOKE_REUSE"


@dataclass(frozen=True)
class ClientCommand:
    name: str
    command: Path


@dataclass
class ArtifactHTTPState:
    head_content_type: str = "text/plain; charset=utf-8"
    head_content_length: int = 12
    get_content_type: str = "text/plain; charset=utf-8"
    get_content_disposition: str | None = None
    body: bytes = b"hello world\n"
    head_calls: int = 0
    get_calls: int = 0


class RunningArbiterServer(Protocol):
    mcp_url: str

    def run_client(
        self,
        *args: str,
        command: Path,
        env: Mapping[str, str] | None = None,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]: ...


class LocalArbiterServerFactory(Protocol):
    def start(self) -> RunningArbiterServer: ...


def _arbiter_server_command() -> Path:
    command = Path(sys.executable).with_name("arbiter-server")
    if os.name == "nt" and not command.exists():
        command = command.with_suffix(".exe")
    if not command.exists():
        raise AssertionError(f"arbiter-server console script not found: {command}")
    return command


def _arbiter_command() -> Path:
    command = Path(sys.executable).with_name("arbiter-py")
    if os.name == "nt" and not command.exists():
        command = command.with_suffix(".exe")
    if not command.exists():
        raise AssertionError(f"arbiter-py console script not found: {command}")
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


def _run_client_command(
    command: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(command), *args],
        check=False,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _yaml_payload(text: str) -> object:
    return OmegaConf.to_container(OmegaConf.create(text), resolve=True)


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
    go_cache = outdir / ".gocache"
    go_cache.mkdir(parents=True, exist_ok=True)
    env.setdefault("GOCACHE", str(go_cache))
    result = subprocess.run(
        [
            sys.executable,
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


@pytest.fixture(scope="module")
def current_go_client_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_current_go_client(tmp_path_factory.mktemp("go-client"))


@pytest.fixture(params=["python", "go"])
def arbiter_client_command(
    request: pytest.FixtureRequest,
) -> ClientCommand:
    if request.param == "python":
        return ClientCommand("python", _arbiter_command())
    if request.param == "go":
        return ClientCommand(
            "go",
            request.getfixturevalue("current_go_client_binary"),
        )
    raise AssertionError(f"unknown client parameter: {request.param}")


class _ArtifactRequestHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        state = _artifact_state(self.server)
        state.head_calls += 1
        self.send_response(200)
        self.send_header("Content-Type", state.head_content_type)
        self.send_header("Content-Length", str(state.head_content_length))
        self.end_headers()

    def do_GET(self) -> None:
        state = _artifact_state(self.server)
        state.get_calls += 1
        self.send_response(200)
        self.send_header("Content-Type", state.get_content_type)
        if state.get_content_disposition is not None:
            self.send_header("Content-Disposition", state.get_content_disposition)
        self.send_header("Content-Length", str(len(state.body)))
        self.end_headers()
        self.wfile.write(state.body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _ArtifactHTTPServer(ThreadingHTTPServer):
    artifact_state: ArtifactHTTPState


def _artifact_state(server: object) -> ArtifactHTTPState:
    state = getattr(server, "artifact_state", None)
    assert isinstance(state, ArtifactHTTPState)
    return state


@dataclass(frozen=True)
class RunningArtifactHTTPServer:
    url: str
    state: ArtifactHTTPState
    server: _ArtifactHTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _start_artifact_http_server(state: ArtifactHTTPState) -> RunningArtifactHTTPServer:
    server = _ArtifactHTTPServer(("127.0.0.1", 0), _ArtifactRequestHandler)
    server.artifact_state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return RunningArtifactHTTPServer(
        url=f"http://{host}:{port}/artifact",
        state=state,
        server=server,
        thread=thread,
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
        (("--help",), "usage: arbiter-py "),
        (("mcp", "--help"), "usage: arbiter-py mcp "),
        (("mcp", "tools", "--help"), "usage: arbiter-py mcp tools "),
        (("mcp", "call", "--help"), "usage: arbiter-py mcp call "),
        (("cap", "--help"), "usage: arbiter-py cap "),
        (("capabilities", "--help"), "usage: arbiter-py cap "),
        (("cap", "desc", "--help"), "usage: arbiter-py cap desc "),
        (("cap", "describe", "--help"), "usage: arbiter-py cap desc "),
        (("op", "--help"), "usage: arbiter-py op "),
        (("operation", "--help"), "usage: arbiter-py op "),
        (("op", "list", "--help"), "usage: arbiter-py op list "),
        (("op", "desc", "--help"), "usage: arbiter-py op desc "),
        (("op", "describe", "--help"), "usage: arbiter-py op desc "),
        (("op", "run", "--help"), "usage: arbiter-py op run "),
        (("artifact", "--help"), "usage: arbiter-py artifact "),
        (("artifact", "get", "--help"), "usage: arbiter-py artifact get "),
        (("artifact", "save", "--help"), "usage: arbiter-py artifact save "),
        (("accounts", "--help"), "usage: arbiter-py accounts "),
        (("accounts", "list", "--help"), "usage: arbiter-py accounts list "),
        (("accounts", "desc", "--help"), "usage: arbiter-py accounts desc "),
        (("accounts", "describe", "--help"), "usage: arbiter-py accounts desc "),
        (("bootstrap", "--help"), "usage: arbiter-py bootstrap "),
        (("bootstrap", "client", "--help"), "usage: arbiter-py bootstrap client "),
        (("--version",), "arbiter-py "),
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


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--help",), ("usage:", "info", "op", "artifact")),
        (("bootstrap", "--help"), ("usage:", "bootstrap", "client")),
        (("bootstrap", "client", "--help"), ("usage:", "bootstrap client")),
        (("info", "--help"), ("usage:", "info", "--yaml")),
        (("op", "--help"), ("usage:", "op", "list", "desc", "run")),
        (("op", "list", "--help"), ("usage:", "op list", "[plugin]")),
        (("op", "desc", "--help"), ("usage:", "op desc")),
        (("op", "run", "--help"), ("usage:", "op run", "--args")),
        (("mcp", "--help"), ("usage:", "mcp", "tools", "call")),
        (("mcp", "tools", "--help"), ("usage:", "mcp tools")),
        (("mcp", "call", "--help"), ("usage:", "mcp call", "--args")),
    ],
)
def test_arbiter_clients_shared_help(
    arbiter_client_command: ClientCommand,
    args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    result = _run_client_command(arbiter_client_command.command, *args)

    assert result.returncode == 0
    for fragment in expected:
        assert fragment in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ("artifact", "--help"),
            ("usage:", "artifact", "get", "save", "with-temp", "with-stdin"),
        ),
        (
            ("artifact", "get", "--help"),
            ("usage:", "artifact get", "--stdout", "--max-bytes"),
        ),
        (
            ("artifact", "save", "--help"),
            ("usage:", "artifact save", "explicit", "stdout"),
        ),
        (
            ("artifact", "with-temp", "--help"),
            ("usage:", "artifact with-temp", "--max-child-stdout-bytes"),
        ),
        (
            ("artifact", "with-stdin", "--help"),
            ("usage:", "artifact with-stdin", "--max-child-stdout-bytes"),
        ),
    ],
)
def test_arbiter_clients_artifact_help(
    arbiter_client_command: ClientCommand,
    args: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    result = _run_client_command(arbiter_client_command.command, *args)

    assert result.returncode == 0
    for fragment in expected:
        assert fragment in result.stdout
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
    assert check.stdout == (
        "server: pass\n"
        "smtp: pass\n"
        "result | plugin | account | policy         | message\n"
        "-------+--------+---------+----------------+--------------------------\n"
        "pass   | smtp   | primary | primary_policy | account/policy pair valid\n"
    )
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

    serve = _run_arbiter_server(
        "--config-dir",
        str(tmp_path),
        "--unsafe-skip-runtime-permission-checks",
        "serve",
    )

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
    arbiter_client_command: ClientCommand,
    local_arbiter_server_factory: LocalArbiterServerFactory,
) -> None:
    server = local_arbiter_server_factory.start()

    result = server.run_client(
        "mcp",
        "call",
        "version_info",
        command=arbiter_client_command.command,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert "structuredContent" in payload


def test_arbiter_clients_info_short(
    arbiter_client_command: ClientCommand,
    local_arbiter_server_factory: LocalArbiterServerFactory,
) -> None:
    server = local_arbiter_server_factory.start()

    json_result = server.run_client(
        "info",
        "--short",
        command=arbiter_client_command.command,
    )

    assert json_result.returncode == 0
    assert json_result.stderr == ""
    payload = json.loads(json_result.stdout)
    assert payload["kind"] == "overview_short"
    assert payload["server_url"] == server.mcp_url
    assert "plugins" not in payload
    assert "operations" not in payload
    accounts = payload["accounts"]
    assert isinstance(accounts, list)
    primary = next(account for account in accounts if account["id"] == "smtp:primary")
    assert isinstance(primary.get("description"), str)

    yaml_result = server.run_client(
        "info",
        "--short",
        "--yaml",
        command=arbiter_client_command.command,
    )

    assert yaml_result.returncode == 0
    assert yaml_result.stderr == ""
    assert "kind: overview_short\n" in yaml_result.stdout
    assert f"server_url: {server.mcp_url}\n" in yaml_result.stdout
    assert "id: smtp:primary\n" in yaml_result.stdout


def test_arbiter_clients_op_list(
    arbiter_client_command: ClientCommand,
    local_arbiter_server_factory: LocalArbiterServerFactory,
) -> None:
    server = local_arbiter_server_factory.start()

    all_result = server.run_client(
        "op",
        "list",
        command=arbiter_client_command.command,
    )

    assert all_result.returncode == 0
    assert all_result.stderr == ""
    assert json.loads(all_result.stdout) == {"plugins": ["smtp"]}

    all_plain_result = server.run_client(
        "op",
        "list",
        "--plain",
        command=arbiter_client_command.command,
    )

    assert all_plain_result.returncode == 0
    assert all_plain_result.stderr == ""
    assert all_plain_result.stdout.splitlines() == ["smtp"]

    all_yaml_result = server.run_client(
        "op",
        "list",
        "--yaml",
        command=arbiter_client_command.command,
    )

    assert all_yaml_result.returncode == 0
    assert all_yaml_result.stderr == ""
    assert _yaml_payload(all_yaml_result.stdout) == {"plugins": ["smtp"]}

    smtp_desc_result = server.run_client(
        "op",
        "desc",
        "smtp",
        command=arbiter_client_command.command,
    )

    assert smtp_desc_result.returncode == 0
    assert smtp_desc_result.stderr == ""
    smtp_desc = json.loads(smtp_desc_result.stdout)
    assert smtp_desc["kind"] == "plugin"
    assert smtp_desc["id"] == "smtp"
    assert [operation["id"] for operation in smtp_desc["operations"]] == [
        "smtp:send_email"
    ]

    smtp_result = server.run_client(
        "op",
        "list",
        "smtp",
        command=arbiter_client_command.command,
    )

    assert smtp_result.returncode == 0
    assert smtp_result.stderr == ""
    smtp_ops = json.loads(smtp_result.stdout)
    assert smtp_ops["kind"] == "ops"
    assert smtp_ops["plugin"] == "smtp"
    assert list(smtp_ops["operations"]) == ["smtp:send_email"]
    assert "id" not in smtp_ops["operations"]["smtp:send_email"]

    smtp_yaml_result = server.run_client(
        "op",
        "list",
        "smtp",
        "--yaml",
        command=arbiter_client_command.command,
    )

    assert smtp_yaml_result.returncode == 0
    assert smtp_yaml_result.stderr == ""
    smtp_yaml = _yaml_payload(smtp_yaml_result.stdout)
    assert isinstance(smtp_yaml, dict)
    assert smtp_yaml["kind"] == "ops"
    assert smtp_yaml["plugin"] == "smtp"
    assert list(smtp_yaml["operations"]) == ["smtp:send_email"]
    assert "id: smtp:send_email\n" not in smtp_yaml_result.stdout

    smtp_plain_result = server.run_client(
        "op",
        "list",
        "smtp",
        "--plain",
        command=arbiter_client_command.command,
    )

    assert smtp_plain_result.returncode == 0
    assert smtp_plain_result.stderr == ""
    assert smtp_plain_result.stdout.splitlines() == ["smtp:send_email"]


def test_arbiter_clients_require_explicit_stdout_for_artifacts(
    arbiter_client_command: ClientCommand,
) -> None:
    result = _run_client_command(
        arbiter_client_command.command,
        "artifact",
        "get",
        "http://127.0.0.1:9/artifact",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "requires --stdout" in result.stderr


def test_arbiter_clients_write_small_text_artifact_to_stdout(
    arbiter_client_command: ClientCommand,
) -> None:
    server = _start_artifact_http_server(ArtifactHTTPState())
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "get",
            server.url,
            "--stdout",
        )
    finally:
        server.close()

    assert result.returncode == 0
    assert result.stdout == "hello world\n"
    assert result.stderr == ""
    assert server.state.head_calls == 1
    assert server.state.get_calls == 1


def test_arbiter_clients_run_with_temp_artifact_command(
    arbiter_client_command: ClientCommand,
) -> None:
    body = b"hello temp\n"
    server = _start_artifact_http_server(
        ArtifactHTTPState(
            head_content_type="application/octet-stream",
            head_content_length=len(body),
            get_content_type="application/octet-stream",
            get_content_disposition='attachment; filename="sample.docx"',
            body=body,
        )
    )
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "with-temp",
            server.url,
            "--",
            sys.executable,
            "-c",
            "import pathlib, sys; path = pathlib.Path(sys.argv[1]); "
            "assert path.read_bytes() == b'hello temp\\n'; print(path)",
            "{}",
        )
    finally:
        server.close()

    assert result.returncode == 0
    temp_path = Path(result.stdout.strip())
    assert temp_path.suffix == ".docx"
    assert not temp_path.exists()
    assert result.stderr == ""
    assert server.state.get_calls == 1


def test_arbiter_clients_stream_binary_artifact_to_stdin_command(
    arbiter_client_command: ClientCommand,
) -> None:
    body = b"%PDF\x00\xff"
    server = _start_artifact_http_server(
        ArtifactHTTPState(
            head_content_type="application/pdf",
            head_content_length=len(body),
            get_content_type="application/pdf",
            body=body,
        )
    )
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "with-stdin",
            server.url,
            "--",
            sys.executable,
            "-c",
            "import sys; data = sys.stdin.buffer.read(); "
            "print(f'stdin:{len(data)}')",
        )
    finally:
        server.close()

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["stdin:6"]
    assert result.stderr == ""
    assert server.state.get_calls == 1


def test_arbiter_clients_save_binary_artifact_to_explicit_output_file(
    arbiter_client_command: ClientCommand,
    tmp_path: Path,
) -> None:
    body = b"%PDF\x00\xff"
    server = _start_artifact_http_server(
        ArtifactHTTPState(
            head_content_type="application/pdf",
            head_content_length=len(body),
            get_content_type="application/pdf",
            body=body,
        )
    )
    output_path = tmp_path / "attachment.pdf"
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "save",
            server.url,
            str(output_path),
        )
    finally:
        server.close()

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert output_path.read_bytes() == body
    assert server.state.head_calls == 0
    assert server.state.get_calls == 1


def test_arbiter_clients_reject_non_text_artifact_before_get(
    arbiter_client_command: ClientCommand,
) -> None:
    server = _start_artifact_http_server(
        ArtifactHTTPState(
            head_content_type="application/pdf",
            head_content_length=12,
        )
    )
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "get",
            server.url,
            "--stdout",
        )
    finally:
        server.close()

    assert result.returncode == 1
    assert result.stdout == ""
    assert "non-text artifact" in result.stderr
    assert server.state.head_calls == 1
    assert server.state.get_calls == 0


def test_arbiter_clients_reject_oversized_text_artifact_before_get(
    arbiter_client_command: ClientCommand,
) -> None:
    server = _start_artifact_http_server(
        ArtifactHTTPState(
            head_content_type="text/plain",
            head_content_length=13,
        )
    )
    try:
        result = _run_client_command(
            arbiter_client_command.command,
            "artifact",
            "get",
            server.url,
            "--stdout",
            "--max-bytes",
            "12",
        )
    finally:
        server.close()

    assert result.returncode == 1
    assert result.stdout == ""
    assert "limit is 12 bytes" in result.stderr
    assert server.state.head_calls == 1
    assert server.state.get_calls == 0
