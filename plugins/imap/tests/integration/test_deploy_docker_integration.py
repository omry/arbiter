from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("ARBITER_RUN_DOCKER_DEPLOY_TESTS") != "1",
    reason=(
        "set ARBITER_RUN_DOCKER_DEPLOY_TESTS=1 to run the Docker "
        "deployment integration test"
    ),
)

DEPLOYMENT_TEST_UID = "77"
DEPLOYMENT_TEST_SUBJECT = "Deployment IMAP smoke message"
DEPLOYMENT_TEST_SNIPPET = "Deployment smoke body with plugin-visible content."


def _load_imap_integration_module() -> Any:
    module_path = Path(__file__).with_name("test_imap_integration.py")
    spec = importlib.util.spec_from_file_location(
        "_arbiter_test_imap_integration",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load IMAP integration helpers: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _deployment_message_bytes() -> bytes:
    message = EmailMessage()
    message["From"] = "Deploy Sender <deploy-sender@example.com>"
    message["To"] = "Bot <bot@example.com>"
    message["Subject"] = DEPLOYMENT_TEST_SUBJECT
    message["Date"] = "Tue, 03 Mar 2026 12:00:00 +0000"
    message["Message-ID"] = "<deployment-smoke@example.com>"
    message.set_content(DEPLOYMENT_TEST_SNIPPET)
    return message.as_bytes()


def _start_deploy_imap_server(port: int) -> Any:
    runner = _load_imap_integration_module()._LocalIMAPServerRunner(
        port,
        host="0.0.0.0",
        close_host="127.0.0.1",
        search_uids=(DEPLOYMENT_TEST_UID,),
        messages={DEPLOYMENT_TEST_UID: _deployment_message_bytes()},
    )
    return runner.start()


@pytest.fixture
def imap_server(free_tcp_port: int) -> Iterator[Any]:
    server = _start_deploy_imap_server(free_tcp_port)
    try:
        yield server
    finally:
        server.stop.close()


def _command(name: str) -> Path:
    command = Path(sys.executable).with_name(name)
    if not command.exists():
        raise AssertionError(f"{name} console script not found: {command}")
    return command


def _run(
    args: list[str | Path],
    *,
    cwd: Path,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"command failed: {result.args}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _docker_available(repo_root: Path) -> bool:
    return (
        _run(["docker", "compose", "version"], cwd=repo_root).returncode == 0
        and _run(["docker", "info"], cwd=repo_root).returncode == 0
    )


def _docker_env_subnet(port: int) -> str:
    return f"172.31.{port % 200 + 1}.0/24"


def _configure_deploy_network(deploy_dir: Path, host_port: int) -> None:
    docker_env = deploy_dir / "docker.env"
    _replace_env_value(docker_env, "ARBITER_HOST_PORT", str(host_port))
    _replace_env_value(
        docker_env,
        "ARBITER_CONTAINER_NAME",
        f"arbiter-test-{host_port}",
    )
    _replace_env_value(
        docker_env,
        "ARBITER_DOCKER_NETWORK_NAME",
        f"arbiter-test-{host_port}",
    )
    _replace_env_value(
        docker_env,
        "ARBITER_DOCKER_BRIDGE_NAME",
        f"aa{host_port}",
    )
    _replace_env_value(
        docker_env,
        "ARBITER_DOCKER_SUBNET",
        _docker_env_subnet(host_port),
    )


def _docker_container_logs(
    *,
    repo_root: Path,
    host_port: int,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "logs", f"arbiter-test-{host_port}"],
        cwd=repo_root,
        timeout=20,
    )


def _docker_compose_logs(deploy_dir: Path) -> subprocess.CompletedProcess[str]:
    args: list[str | Path] = [
        "docker",
        "compose",
        "--env-file",
        deploy_dir / "docker.env",
        "-f",
        deploy_dir / "compose.yaml",
    ]
    if (deploy_dir / "compose.override.yaml").exists():
        args.extend(["-f", deploy_dir / "compose.override.yaml"])
    args.extend(["logs", "--no-color"])
    return _run(args, cwd=deploy_dir, timeout=20)


def _assert_deployment_preflight(
    *,
    repo_root: Path,
    helper: Path,
    deploy_dir: Path,
    install_target: Path,
    check_bundle: bool,
) -> None:
    info = _run([helper, "info"], cwd=repo_root, timeout=20)
    _assert_ok(info)
    assert f"deploy dir: {deploy_dir}" in info.stdout
    assert f"docker env file: {deploy_dir / 'docker.env'}" in info.stdout

    doctor = _run([helper, "doctor"], cwd=repo_root, timeout=20)
    _assert_ok(doctor)
    assert "ok: compose file exists:" in doctor.stdout
    assert "warn: skipping agent permission checks" in doctor.stdout

    preinstall = _run([helper, "doctor", "--preinstall"], cwd=repo_root, timeout=20)
    _assert_ok(preinstall)
    assert "ok: preinstall checks passed" in preinstall.stdout

    config_check = _run([helper, "config", "check"], cwd=repo_root, timeout=240)
    _assert_ok(config_check)

    if check_bundle:
        bundle_check = _run([helper, "bundle", "check"], cwd=repo_root, timeout=120)
        _assert_ok(bundle_check)
        assert "bundle check passed:" in bundle_check.stdout

    install_plan = _run(
        [
            helper,
            "install",
            "--dry-run",
            "--to",
            install_target,
            "--user",
            "arbiter",
            "--no-start",
        ],
        cwd=repo_root,
        timeout=120,
    )
    _assert_ok(install_plan)
    assert f"would copy deployment: {deploy_dir} -> {install_target}" in (
        install_plan.stdout
    )
    assert "would check candidate config before install:" in install_plan.stdout
    assert "would run: systemctl restart" not in install_plan.stdout
    assert install_plan.stderr == ""


def _write_imap_only_config(path: Path, imap_server: Any) -> None:
    path.write_text(
        "defaults:\n"
        "  - arbiter_app_config_schema\n"
        "  - /arbiter/server: http\n"
        "  - /arbiter/account/imap/schema@arbiter.account.imap.primary\n"
        "  - /arbiter/policy/imap/schema@arbiter.policy.imap.bot\n"
        "  - _self_\n"
        "\n"
        "arbiter:\n"
        "  server:\n"
        "    name: arbiter\n"
        "    bind:\n"
        "      host: 0.0.0.0\n"
        "      port: 8075\n"
        '      path: ""\n'
        "  account:\n"
        "    imap:\n"
        "      primary:\n"
        "        policy: bot\n"
        "        description: Docker deployment test IMAP account\n"
        "        host: host.docker.internal\n"
        f"        port: {imap_server.port}\n"
        "        username: user@example.com\n"
        "        password: secret\n"
        "        tls: none\n"
        "        verify_peer: true\n"
        "        timeout_seconds: 5.0\n"
        "        default_folder: INBOX\n"
        "        folders:\n"
        "          INBOX:\n"
        "            description: Test inbox.\n"
        "  policy:\n"
        "    imap:\n"
        "      bot:\n"
        "        folder_access:\n"
        "          rules:\n"
        '            - allow_glob: "*"\n'
        "        operation_defaults:\n"
        "          read: allow\n"
        "          search: allow\n"
        "          move: false\n"
        "          delete: deny\n"
        "          user_flags: {}\n",
        encoding="utf-8",
    )
    path.chmod(0o640)


def _wait_for_imap_operation(
    *,
    repo_root: Path,
    url: str,
    helper: Path,
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + 120
    last_result: subprocess.CompletedProcess[str] | None = None
    while time.monotonic() < deadline:
        last_result = _run(
            [
                _command("arbiter"),
                "op",
                "run",
                "imap:list_messages",
                "--args",
                '{"account":"primary","folder":"INBOX","limit":1}',
                f"arbiter.url={url}",
            ],
            cwd=repo_root,
            timeout=20,
        )
        if (
            last_result.returncode == 0
            and DEPLOYMENT_TEST_SUBJECT in last_result.stdout
        ):
            return last_result
        time.sleep(2)

    logs = _docker_compose_logs(helper.parent)
    raise AssertionError(
        "Docker deployment did not serve a successful IMAP operation\n"
        f"last stdout:\n{last_result.stdout if last_result else ''}\n"
        f"last stderr:\n{last_result.stderr if last_result else ''}\n"
        f"container logs:\n{logs.stdout}\n{logs.stderr}"
    )


def _run_delete_message(
    *,
    repo_root: Path,
    url: str,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            _command("arbiter"),
            "op",
            "run",
            "imap:delete_message",
            "--args",
            (
                '{"account":"primary","folder":"INBOX",'
                f'"message_id":"{DEPLOYMENT_TEST_UID}"}}'
            ),
            f"arbiter.url={url}",
        ],
        cwd=repo_root,
        timeout=20,
    )


def _assert_native_arbiter_client(repo_root: Path) -> None:
    result = _run([_command("arbiter"), "--version"], cwd=repo_root, timeout=20)
    _assert_ok(result)
    assert result.stdout.startswith("arbiter "), result.stdout


def _operation_payload(stdout: str) -> dict[str, Any]:
    payload = json.loads(stdout)
    if isinstance(payload, dict) and "account" in payload:
        return payload
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        content = payload.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                nested = json.loads(first["text"])
                if isinstance(nested, dict):
                    return nested
    raise AssertionError(f"unexpected operation payload: {payload!r}")


def _assert_imap_deployment_operation(
    *,
    repo_root: Path,
    url: str,
    helper: Path,
    imap_server: Any,
) -> None:
    _assert_native_arbiter_client(repo_root)
    result = _wait_for_imap_operation(
        repo_root=repo_root,
        url=url,
        helper=helper,
    )
    payload = _operation_payload(result.stdout)
    assert payload["account"] == "primary"
    assert payload["folder"] == "INBOX"
    message = payload["messages"][0]
    assert message["id"] == DEPLOYMENT_TEST_UID
    assert message["subject"] == DEPLOYMENT_TEST_SUBJECT
    assert message["from"] == "Deploy Sender <deploy-sender@example.com>"
    assert message["snippet"] == DEPLOYMENT_TEST_SNIPPET
    assert message["flags"] == ["SEEN"]
    assert "bot.followed_up" not in message["flags"]
    assert any("LOGIN user@example.com" in command for command in imap_server.commands)
    assert any(command.endswith('EXAMINE "INBOX"') for command in imap_server.commands)
    delete = _run_delete_message(repo_root=repo_root, url=url)
    assert delete.returncode == 1
    assert "delete_message is not allowed for account: primary" in delete.stderr
    assert not any(
        "UID STORE" in command and "\\Deleted" in command
        for command in imap_server.commands
    )
    assert not any("UID EXPUNGE" in command for command in imap_server.commands)


def _build_deploy_wheelhouse(repo_root: Path, wheelhouse: Path) -> dict[str, Path]:
    wheelhouse.mkdir()
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_root}:/repo:ro",
            "-v",
            f"{wheelhouse}:/wheelhouse",
            "-w",
            "/repo",
            "python:3.11-slim",
            "sh",
            "-lc",
            (
                "mkdir -p /tmp/arbiter-wheel-build && "
                "mkdir -p /tmp/arbiter-wheel-build/plugins && "
                "cp -a /repo/server /tmp/arbiter-wheel-build/ && "
                "cp -a /repo/plugins/smtp /repo/plugins/imap "
                "/tmp/arbiter-wheel-build/plugins/ && "
                "find /tmp/arbiter-wheel-build -type d "
                '\\( -name "*.egg-info" -o -name "__pycache__" \\) '
                "-prune -exec rm -rf {} + && "
                "python -m pip wheel --wheel-dir /wheelhouse "
                "/tmp/arbiter-wheel-build/server "
                "/tmp/arbiter-wheel-build/plugins/smtp "
                "/tmp/arbiter-wheel-build/plugins/imap"
            ),
        ],
        cwd=repo_root,
        timeout=240,
    )
    _assert_ok(result)

    wheel_names = {path.name: path for path in wheelhouse.glob("*.whl")}
    expected_prefixes = {
        "server": "arbiter_server-",
        "smtp": "arbiter_smtp-",
        "imap": "arbiter_imap-",
        "hydra": "hydra_core-",
    }
    wheels: dict[str, Path] = {}
    for label, prefix in expected_prefixes.items():
        matches = sorted(
            path for name, path in wheel_names.items() if name.startswith(prefix)
        )
        assert matches, f"wheelhouse is missing {label} wheel matching {prefix!r}"
        wheels[label] = matches[0]
    return wheels


def test_docker_deployment_serves_real_imap_operation(
    tmp_path: Path,
    free_tcp_port_factory: Callable[[], int],
    imap_server: Any,
) -> None:
    repo_root = _repo_root()
    if not _docker_available(repo_root):
        pytest.skip("docker compose is not available")

    deploy_dir = tmp_path / "deploy"
    init = _run(
        [
            _command("arbiter-server"),
            "deploy",
            "docker",
            f"docker.dir={deploy_dir}",
            "docker.requirement=/source/arbiter/server",
            "docker.requirement=/source/arbiter/plugins/smtp",
            "docker.requirement=/source/arbiter/plugins/imap",
            "init",
        ],
        cwd=repo_root,
        timeout=30,
    )
    _assert_ok(init)

    config_dir = deploy_dir / "conf"
    config_dir.mkdir(exist_ok=True)
    _write_imap_only_config(config_dir / "arbiter-server.yaml", imap_server)
    (config_dir / ".env").write_text("", encoding="utf-8")
    (config_dir / ".env").chmod(0o600)
    (deploy_dir / "compose.override.yaml").write_text(
        "services:\n"
        "  arbiter:\n"
        "    volumes:\n"
        f"      - {repo_root}:/source/arbiter:ro\n",
        encoding="utf-8",
    )
    host_port = free_tcp_port_factory()
    _configure_deploy_network(deploy_dir, host_port)

    helper = deploy_dir / "arbiter-docker"
    _assert_deployment_preflight(
        repo_root=repo_root,
        helper=helper,
        deploy_dir=deploy_dir,
        install_target=tmp_path / "install-target",
        check_bundle=False,
    )

    try:
        up = _run([helper, "up"], cwd=repo_root, timeout=240)
        _assert_ok(up)
        url = f"https://127.0.0.1:{host_port}"
        _assert_imap_deployment_operation(
            repo_root=repo_root,
            url=url,
            helper=helper,
            imap_server=imap_server,
        )
    finally:
        _run([helper, "down"], cwd=repo_root, timeout=60)


def test_docker_deployment_serves_real_imap_operation_from_wheelhouse(
    tmp_path: Path,
    free_tcp_port_factory: Callable[[], int],
) -> None:
    repo_root = _repo_root()
    if not _docker_available(repo_root):
        pytest.skip("docker compose is not available")

    wheelhouse = tmp_path / "wheels"
    wheels = _build_deploy_wheelhouse(repo_root, wheelhouse)
    imap_server = _start_deploy_imap_server(free_tcp_port_factory())
    deploy_dir = tmp_path / "deploy"
    try:
        init = _run(
            [
                _command("arbiter-server"),
                "deploy",
                "docker",
                f"docker.dir={deploy_dir}",
                f"docker.requirement=/wheels/{wheels['server'].name}",
                f"docker.requirement=/wheels/{wheels['smtp'].name}",
                f"docker.requirement=/wheels/{wheels['imap'].name}",
                "init",
            ],
            cwd=repo_root,
            timeout=30,
        )
        _assert_ok(init)

        deploy_wheelhouse = deploy_dir / "wheels"
        for wheel in wheelhouse.glob("*.whl"):
            shutil.copy2(wheel, deploy_wheelhouse / wheel.name)

        config_dir = deploy_dir / "conf"
        config_dir.mkdir(exist_ok=True)
        _write_imap_only_config(config_dir / "arbiter-server.yaml", imap_server)
        (config_dir / ".env").write_text("", encoding="utf-8")
        (config_dir / ".env").chmod(0o600)
        host_port = free_tcp_port_factory()
        _configure_deploy_network(deploy_dir, host_port)

        helper = deploy_dir / "arbiter-docker"
        _assert_deployment_preflight(
            repo_root=repo_root,
            helper=helper,
            deploy_dir=deploy_dir,
            install_target=tmp_path / "install-target",
            check_bundle=True,
        )
        up = _run([helper, "up"], cwd=repo_root, timeout=240)
        _assert_ok(up)
        url = f"https://127.0.0.1:{host_port}"
        _assert_imap_deployment_operation(
            repo_root=repo_root,
            url=url,
            helper=helper,
            imap_server=imap_server,
        )
        logs = _docker_container_logs(repo_root=repo_root, host_port=host_port)
        _assert_ok(logs)
        assert "Downloading " not in logs.stdout
        assert "Downloading " not in logs.stderr
    finally:
        try:
            if (helper := deploy_dir / "arbiter-docker").exists():
                _run([helper, "down"], cwd=repo_root, timeout=60)
        finally:
            imap_server.stop.close()
