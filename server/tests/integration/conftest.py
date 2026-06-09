from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest


_MAX_START_ATTEMPTS = 3
_MAX_LOG_CHARS = 20_000


@dataclass(frozen=True)
class PluginAccount:
    plugin: str
    account: str


@dataclass
class RunningArbiterServer:
    config_dir: Path
    mcp_url: str
    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path
    stdout: str = ""
    stderr: str = ""

    def run_client(
        self,
        *args: str,
        command: Path,
        env: Mapping[str, str] | None = None,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        client_env = os.environ.copy()
        if env is not None:
            client_env.update(env)
        return subprocess.run(
            [str(command), *args, f"arbiter.mcp_url={self.mcp_url}"],
            check=False,
            env=client_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.refresh_output()

    def refresh_output(self) -> None:
        self.stdout = _read_log_tail(self.stdout_path)
        self.stderr = _read_log_tail(self.stderr_path)

    def diagnostics(self) -> str:
        self.refresh_output()
        return (
            f"stdout ({self.stdout_path}):\n{self.stdout}\n"
            f"stderr ({self.stderr_path}):\n{self.stderr}"
        )


class ArbiterServerStartError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LocalArbiterServerFactory:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path
        self._servers: list[RunningArbiterServer] = []

    def start(
        self,
        *,
        plugin_accounts: Sequence[PluginAccount] = (PluginAccount("smtp", "primary"),),
        env: Mapping[str, str] | None = None,
        overrides: Sequence[str] = (),
    ) -> RunningArbiterServer:
        config_dir = self._tmp_path / f"arbiter-server-{len(self._servers)}"
        server_env = _server_env(plugin_accounts, env)
        _bootstrap_server_config(config_dir, plugin_accounts, env=server_env)
        last_error: ArbiterServerStartError | None = None
        for attempt in range(1, _MAX_START_ATTEMPTS + 1):
            server = self._start_process(
                config_dir=config_dir,
                server_env=server_env,
                overrides=overrides,
                attempt=attempt,
            )
            try:
                self._wait_until_ready(server)
            except ArbiterServerStartError as exc:
                last_error = exc
                server.stop()
                if exc.retryable and attempt < _MAX_START_ATTEMPTS:
                    continue
                pytest.fail(str(exc))
            self._servers.append(server)
            return server

        assert last_error is not None
        pytest.fail(str(last_error))

    def close(self) -> None:
        for server in reversed(self._servers):
            server.stop()

    def _start_process(
        self,
        *,
        config_dir: Path,
        server_env: Mapping[str, str],
        overrides: Sequence[str],
        attempt: int,
    ) -> RunningArbiterServer:
        port = _free_tcp_port()
        stdout_path = config_dir / f"server-{attempt}.stdout.log"
        stderr_path = config_dir / f"server-{attempt}.stderr.log"
        with (
            stdout_path.open("w", encoding="utf-8") as stdout_file,
            stderr_path.open("w", encoding="utf-8") as stderr_file,
        ):
            process = subprocess.Popen(
                [
                    str(_arbiter_server_command()),
                    "--config-dir",
                    str(config_dir),
                    "--unsafe-skip-runtime-permission-checks",
                    "serve",
                    "arbiter.server.bind.host=127.0.0.1",
                    f"arbiter.server.bind.port={port}",
                    *overrides,
                ],
                env=server_env,
                text=True,
                stdout=stdout_file,
                stderr=stderr_file,
            )
        return RunningArbiterServer(
            config_dir=config_dir,
            mcp_url=f"http://127.0.0.1:{port}/mcp",
            process=process,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def _wait_until_ready(self, server: RunningArbiterServer) -> None:
        deadline = time.monotonic() + 15
        last_result: subprocess.CompletedProcess[str] | None = None
        last_timeout: subprocess.TimeoutExpired | None = None
        while time.monotonic() < deadline:
            if server.process.poll() is not None:
                server.stop()
                raise ArbiterServerStartError(
                    "arbiter-server exited before it became ready\n"
                    f"{server.diagnostics()}",
                    retryable=_looks_like_address_in_use(server.stderr),
                )
            try:
                last_result = server.run_client(
                    "mcp",
                    "call",
                    "version_info",
                    command=_arbiter_command(),
                    timeout=5,
                )
                last_timeout = None
            except subprocess.TimeoutExpired as exc:
                last_timeout = exc
                time.sleep(0.25)
                continue
            if last_result.returncode == 0:
                return
            time.sleep(0.25)

        server.stop()
        last_stdout = "" if last_result is None else last_result.stdout
        last_stderr = "" if last_result is None else last_result.stderr
        last_timeout_text = "" if last_timeout is None else f"{last_timeout}\n"
        raise ArbiterServerStartError(
            "arbiter-server did not become ready\n"
            f"last client timeout:\n{last_timeout_text}"
            f"last client stdout:\n{last_stdout}\n"
            f"last client stderr:\n{last_stderr}\n"
            f"{server.diagnostics()}",
            retryable=_looks_like_address_in_use(server.stderr),
        )


@pytest.fixture
def local_arbiter_server_factory(tmp_path: Path) -> Iterator[LocalArbiterServerFactory]:
    factory = LocalArbiterServerFactory(tmp_path)
    try:
        yield factory
    finally:
        factory.close()


def _arbiter_server_command() -> Path:
    command = os.environ.get("ARBITER_SERVER_COMMAND")
    if command:
        return Path(command)
    command_path = Path(sys.executable).with_name("arbiter-server")
    if os.name == "nt" and not command_path.exists():
        command_path = command_path.with_suffix(".exe")
    if not command_path.exists():
        raise AssertionError(f"arbiter-server console script not found: {command_path}")
    return command_path


def _arbiter_command() -> Path:
    command = os.environ.get("ARBITER_COMMAND")
    if command:
        return Path(command)
    command_path = Path(sys.executable).with_name("arbiter-py")
    if os.name == "nt" and not command_path.exists():
        command_path = command_path.with_suffix(".exe")
    if not command_path.exists():
        raise AssertionError(f"arbiter-py console script not found: {command_path}")
    return command_path


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_log_tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    if len(text) <= _MAX_LOG_CHARS:
        return text
    return f"... truncated to last {_MAX_LOG_CHARS} chars ...\n{text[-_MAX_LOG_CHARS:]}"


def _looks_like_address_in_use(stderr: str) -> bool:
    lower = stderr.lower()
    return any(
        marker in lower
        for marker in (
            "address already in use",
            "eaddrinuse",
            "errno 98",
            "winerror 10048",
            "only one usage of each socket address",
        )
    )


def _run_arbiter_server(
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_arbiter_server_command()), *args],
        check=False,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _bootstrap_server_config(
    config_dir: Path,
    plugin_accounts: Sequence[PluginAccount],
    *,
    env: Mapping[str, str],
) -> None:
    bootstrap = _run_arbiter_server(
        "--config-dir",
        str(config_dir),
        "bootstrap",
        "arbiter",
    )
    _assert_ok(bootstrap)
    for plugin_account in plugin_accounts:
        account = _run_arbiter_server(
            "--config-dir",
            str(config_dir),
            "bootstrap",
            "plugin",
            plugin_account.plugin,
            "account",
            plugin_account.account,
        )
        _assert_ok(account)
        activate = _run_arbiter_server(
            "--config-dir",
            str(config_dir),
            "config",
            "activate",
            "account",
            plugin_account.plugin,
            plugin_account.account,
        )
        _assert_ok(activate)
    check = _run_arbiter_server(
        "--config-dir",
        str(config_dir),
        "config",
        "check",
        env=env,
    )
    _assert_ok(check)


def _server_env(
    plugin_accounts: Sequence[PluginAccount],
    env: Mapping[str, str] | None,
) -> dict[str, str]:
    server_env = os.environ.copy()
    for plugin_account in plugin_accounts:
        if plugin_account.plugin == "smtp":
            suffix = plugin_account.account.upper().replace("-", "_")
            if not suffix.endswith("_ACCOUNT"):
                suffix = f"{suffix}_ACCOUNT"
            server_env.setdefault(f"SMTP_{suffix}_USERNAME", "test-user")
            server_env.setdefault(f"SMTP_{suffix}_PASSWORD", "test-password")
    if env is not None:
        server_env.update(env)
    return server_env


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"command failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
