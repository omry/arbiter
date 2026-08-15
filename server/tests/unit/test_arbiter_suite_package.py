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


def test_reploy_blueprint_requires_current_environment_schema() -> None:
    blueprint = _load_server_blueprint()

    assert blueprint["blueprint"] == {
        "schema": 1,
        "version": "0.1.0",
        "requires_reploy": ">=0.7.0.dev1",
        "compatibility": {"platforms": ["linux/amd64", "linux/arm64"]},
    }
    assert "control_script" not in blueprint["environment"]


def test_reploy_blueprints_forward_option_based_bootstrap() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]
    application = environment["applications"]["application"]

    assert application["packages"]["python"]["requirements"] == ["arbiter-server"]
    assert application["executables"]["server"]["argv_prefix"] == [
        "--config-dir",
        "/deployment/conf",
        "--config-name",
        "arbiter-server",
    ]

    bootstrap = environment["commands"]["bootstrap"]
    assert bootstrap["native_command"] is True
    assert "deployed_command" not in bootstrap
    assert bootstrap["forward_flags"] == [
        "--plugin",
        "--plugins",
        "--account",
        "--accounts",
        "--policy",
        "--policies",
        "--variant",
        "--list-variants",
        "--force",
        "--dry-mode",
    ]
    assert bootstrap["argv"] == ["bootstrap"]
    assert bootstrap["order"] == ["binary", "prefix", "command", "forwarded"]


def test_reploy_blueprint_protects_control_files_and_preserves_runtime_data() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]
    docker = blueprint["docker"]

    assert environment["mounts"] == {
        "deployment": {
            "target": "/deployment",
            "writable": False,
            "update_policy": "preserve",
        },
        "data": {
            "target": "/data",
            "writable": True,
            "update_policy": "preserve",
        },
    }
    assert docker["mounts"] == {
        "deployment": {
            "extends": "environment.mounts.deployment",
            "mode": "managed-bind",
            "source": ".",
        },
        "data": {
            "extends": "environment.mounts.data",
            "mode": "managed-bind",
            "source": "data",
        },
    }
    suffix = environment["applications"]["application"]["executables"]["server"][
        "argv_suffix"
    ]
    assert "arbiter.storage.server_data_dir=/data/server" in suffix
    assert "arbiter.storage.plugin_data_dir=/data/plugins" in suffix


def test_reploy_blueprint_makes_non_mutating_commands_read_only() -> None:
    blueprint = _load_server_blueprint()
    commands = blueprint["environment"]["commands"]

    for name in ["config_show", "env_check", "version", "plugins_list"]:
        assert commands[name]["mounts"] == {
            "deployment": {"writable": False},
            "data": {"writable": False},
        }

    for name in ["serve", "config_check"]:
        assert "mounts" not in commands[name]

    for name in [
        "bootstrap_server",
        "bootstrap",
        "config_activate",
        "config_deactivate",
        "env_bootstrap",
    ]:
        assert commands[name]["mounts"] == {
            "deployment": {"writable": True},
        }


def test_reploy_blueprint_allows_public_and_local_mail_network_access() -> None:
    blueprint = _load_server_blueprint()

    assert blueprint["environment"]["runtime"] == {
        "network": {"public": "allow", "local": "allow"},
    }


def test_reploy_blueprints_expose_canonical_config_activate_command() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]

    commands = environment["commands"]
    assert "activate" not in commands
    activate = commands["config_activate"]
    assert activate["trigger"] == ["config", "activate"]
    assert activate["native_command"] is True
    assert activate["deployed_command"] is True
    assert activate["forward_flags"] == [
        "--plugin",
        "--plugins",
        "--account",
        "--accounts",
    ]
    assert activate["argv"] == ["config", "activate"]
    assert activate["order"] == ["binary", "prefix", "command", "forwarded"]


def test_reploy_blueprint_declares_install_hooks_and_success_message() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]

    assert environment["install"]["success"]["lines"] == [
        "server url: {{ environment.workload.endpoints.https.scheme }}://{{ reploy.workload.endpoints.https.publish.address }}:{{ reploy.workload.endpoints.https.publish.port }}",
        "client command: arbiter arbiter.url={{ environment.workload.endpoints.https.scheme }}://{{ reploy.workload.endpoints.https.publish.address }}:{{ reploy.workload.endpoints.https.publish.port }} info server",
    ]
    assert environment["workload"]["runtime"] == {
        "before_start": [
            {"actions": [{"environment": ["config", "check"]}]},
        ],
        "after_start": [
            {
                "requires": {"endpoints": ["https"]},
                "actions": [{"environment": ["config", "check", "--live"]}],
            },
        ],
    }


def test_reploy_blueprint_declares_install_defaults() -> None:
    blueprint = _load_server_blueprint()

    assert blueprint["environment"]["install"] == {
        "target": {"default_path": "/opt/{{ environment.id }}"},
        "system": {
            "account": {
                "user": "arbiter",
                "group": "arbiter",
                "on_missing": "create",
            }
        },
        "success": {
            "lines": [
                "server url: {{ environment.workload.endpoints.https.scheme }}://{{ reploy.workload.endpoints.https.publish.address }}:{{ reploy.workload.endpoints.https.publish.port }}",
                "client command: arbiter arbiter.url={{ environment.workload.endpoints.https.scheme }}://{{ reploy.workload.endpoints.https.publish.address }}:{{ reploy.workload.endpoints.https.publish.port }} info server",
            ]
        },
    }


def test_reploy_blueprint_uses_current_docker_service_shape() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]
    docker = blueprint["docker"]

    assert environment["base"] == {"image": "python:3.11-slim"}
    assert environment["workload"]["endpoints"]["https"] == {
        "scheme": "https",
        "port": 8075,
        "readiness": {"path": "/_health_", "tls_verify": False},
    }
    assert docker["workload"]["endpoints"]["https"] == {
        "extends": "environment.workload.endpoints.https",
        "bind": {"address": "0.0.0.0"},
        "publish": {
            "address": "127.0.0.1",
            "staging": 18075,
            "deployed": 8075,
        },
    }


def test_reploy_blueprint_exposes_deployed_operator_commands() -> None:
    blueprint = _load_server_blueprint()
    environment = blueprint["environment"]

    commands = environment["commands"]
    assert commands["bootstrap"]["native_command"] is True
    assert "deployed_command" not in commands["bootstrap"]
    assert commands["bootstrap_server"]["native_command"] is True
    assert "deployed_command" not in commands["bootstrap_server"]
    assert commands["env_bootstrap"]["native_command"] is True
    assert "deployed_command" not in commands["env_bootstrap"]

    deployed = {
        name for name, command in commands.items() if command.get("deployed_command")
    }
    assert deployed == {
        "config_activate",
        "config_check",
        "config_deactivate",
        "config_show",
        "env_check",
        "plugins_list",
        "version",
    }

    assert commands["bootstrap"]["trigger"] == ["bootstrap"]
    assert commands["bootstrap_server"]["trigger"] == ["bootstrap", "server"]
    assert commands["config_show"]["trigger"] == ["config", "show"]
    assert commands["env_bootstrap"]["trigger"] == ["env", "bootstrap"]
    assert commands["env_check"]["trigger"] == ["env", "check"]
    assert commands["plugins_list"]["trigger"] == ["plugins", "list"]
    assert commands["version"]["trigger"] == ["version"]
    assert commands["config_show"]["argv"] == ["config", "show"]
    assert commands["env_check"]["argv"] == ["env", "check"]
    assert commands["plugins_list"]["argv"] == ["plugins", "list"]
    assert commands["version"]["argv"] == ["version"]
    assert commands["plugins_list"]["order"] == [
        "binary",
        "prefix",
        "command",
        "forwarded",
    ]
    assert commands["version"]["order"] == [
        "binary",
        "prefix",
        "command",
        "forwarded",
    ]
