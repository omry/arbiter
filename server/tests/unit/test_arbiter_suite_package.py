import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, cast

from omegaconf import OmegaConf

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[3]
SUITE_ROOT = REPO_ROOT / "meta" / "arbiter-suite"
SERVER_ROOT = REPO_ROOT / "server"
SUITE_PACK_PATH = "arbiter_suite/reploy"
SERVER_PACK_ROOT = SERVER_ROOT / "src" / "arbiter_server" / "reploy"
SERVER_PACK_PATH = "arbiter_server/reploy"


def _load_server_blueprint() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        OmegaConf.to_container(
            OmegaConf.load(SERVER_PACK_ROOT / "arbiter.blueprint.yaml"),
            resolve=False,
        ),
    )


def _build_suite_wheel(tmp_path: Path) -> Path:
    source = tmp_path / "arbiter-suite-src"
    shutil.copytree(
        SUITE_ROOT,
        source,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(source),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    return next(tmp_path.glob("arbiter_suite-*.whl"))


def _build_server_wheel(tmp_path: Path) -> Path:
    source = tmp_path / "arbiter-server-src"
    shutil.copytree(
        SERVER_ROOT,
        source,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(source),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    return next(tmp_path.glob("arbiter_server-*.whl"))


def test_suite_wheel_does_not_contain_reploy_blueprint(tmp_path: Path) -> None:
    wheel_path = _build_suite_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_prefix = SUITE_PACK_PATH + "/"
        assert not any(name.startswith(packaged_prefix) for name in wheel.namelist())


def test_server_wheel_contains_minimal_reploy_deployment_pack(tmp_path: Path) -> None:
    wheel_path = _build_server_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_prefix = SERVER_PACK_PATH + "/"
        packaged_files = {
            name.removeprefix(packaged_prefix)
            for name in wheel.namelist()
            if name.startswith(packaged_prefix) and not name.endswith("/")
        }
        source_files = {
            path.relative_to(SERVER_PACK_ROOT).as_posix()
            for path in SERVER_PACK_ROOT.rglob("*")
            if path.is_file()
        }
        assert packaged_files == source_files
        for relative_path in source_files:
            assert (
                wheel.read(packaged_prefix + relative_path)
                == (SERVER_PACK_ROOT / relative_path).read_bytes()
            )


def test_reploy_blueprints_forward_option_based_bootstrap() -> None:
    expected = (
        "    bootstrap:\n"
        "      trigger:\n"
        "        - bootstrap\n"
        "      app_command: true\n"
        "      forward_args: true\n"
        "      container:\n"
        "        argv:\n"
        "          - arbiter-server\n"
        "          - --config-dir\n"
        "          - /config/conf\n"
        "          - --config-name\n"
        "          - ${ARBITER_CONFIG_NAME}\n"
        "          - bootstrap\n"
    )
    assert expected in (SERVER_PACK_ROOT / "arbiter.blueprint.yaml").read_text(
        encoding="utf-8"
    )


def test_reploy_blueprint_preserves_top_level_env_file() -> None:
    blueprint = _load_server_blueprint()
    install = blueprint["install"]
    docker = blueprint["docker"]

    assert docker["deployment_dirs"]["config"] == "conf"
    assert ".arbiter.env" in install["upgrade"]["artifacts"]["config"]["paths"]


def test_reploy_blueprints_expose_activate_alias() -> None:
    blueprint = _load_server_blueprint()
    docker = blueprint["docker"]
    assert isinstance(docker, dict)

    activate = docker["commands"]["activate"]
    assert activate["trigger"] == ["activate"]
    assert activate["app_command"] is True
    assert activate["forward_args"] is True
    assert activate["container"]["argv"][-2:] == ["config", "activate"]


def test_reploy_blueprint_declares_install_hooks_and_success_message() -> None:
    blueprint = _load_server_blueprint()
    docker = blueprint["docker"]
    assert isinstance(docker, dict)

    assert docker["install"] == {
        "success": {
            "vars": {
                "server_url": {
                    "server_url": True,
                }
            },
            "lines": [
                "server url: ${server_url}",
                "client command: arbiter arbiter.url=${server_url} info server",
            ],
        },
        "hooks": {
            "before_start": [{"app": ["config", "check"]}],
            "after_start": [
                {"health_check": {"wait": True}},
                {"app": ["config", "check", "--live"]},
            ],
        },
    }


def test_reploy_blueprint_declares_install_defaults() -> None:
    blueprint = _load_server_blueprint()

    assert blueprint["install"] == {
        "target": {"default_path": "/opt/{{ app.id }}"},
        "owner": {"user": "arbiter", "group": "arbiter"},
        "ports": {
            "deployed": {
                "https": {
                    "host_bind": "127.0.0.1",
                    "host_port": 8075,
                    "container_port": 8075,
                }
            },
            "staging": {
                "https": {
                    "host_bind": "127.0.0.1",
                    "host_port": 18075,
                    "container_port": 8075,
                }
            },
        },
        "upgrade": {
            "artifacts": {
                "config": {"default": "preserve", "paths": ["conf/", ".arbiter.env"]},
                "data": {"default": "preserve", "paths": ["data/"]},
            }
        },
    }


def test_reploy_blueprint_uses_current_docker_service_shape() -> None:
    blueprint = _load_server_blueprint()
    docker = blueprint["docker"]
    assert isinstance(docker, dict)

    assert docker["service"] == {
        "image": "python:3.11-slim",
        "container_host": "0.0.0.0",
    }


def test_reploy_blueprint_exposes_deployed_operator_commands() -> None:
    blueprint = _load_server_blueprint()
    docker = blueprint["docker"]
    assert isinstance(docker, dict)

    commands = docker["commands"]
    deployed = {
        name
        for name, command in commands.items()
        if command.get("deployed_command") is True
    }
    assert {
        "activate",
        "config_activate",
        "config_check",
        "config_deactivate",
        "config_show",
        "env_bootstrap",
        "env_check",
        "plugins_list",
        "version",
    }.issubset(deployed)

    for command_name in deployed:
        assert commands[command_name]["app_command"] is True

    assert commands["config_show"]["trigger"] == ["config", "show"]
    assert commands["env_check"]["trigger"] == ["env", "check"]
    assert commands["plugins_list"]["trigger"] == ["plugins", "list"]
    assert commands["version"]["trigger"] == ["version"]
