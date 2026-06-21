from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_studio_config() -> Any:
    path = REPO_ROOT / "media" / "tools" / "studio_config.py"
    spec = importlib.util.spec_from_file_location("studio_config", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["studio_config"] = module
    spec.loader.exec_module(module)
    return module


studio_config = load_studio_config()


def test_default_config_composes_install_recording() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")

    assert spec["id"] == "install-and-bootstrap"
    assert spec["package_source"] == {
        "mode": "pypi",
        "package": "arbiter-suite",
        "version": "latest",
        "requirement": "",
    }
    assert spec["_manifest_path"].endswith(
        "media/conf/recording/install-and-bootstrap.yaml"
    )


def test_hydra_output_dir_is_attached_to_recording_spec() -> None:
    config = studio_config.compose_studio_config("install-and-bootstrap")
    spec = studio_config.recording_spec_from_config(
        config,
        recording_id="install-and-bootstrap",
        overrides=[],
        hydra_output_dir="/tmp/hydra-run",
    )

    assert spec["_hydra_output_dir"] == "/tmp/hydra-run"
    assert spec["_keep_hydra_output_dir"] is True


def test_package_source_requirement_override_is_composed() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["package_source.requirement=arbiter-suite==0.9.2.dev1"],
    )

    assert spec["package_source"]["requirement"] == "arbiter-suite==0.9.2.dev1"
    assert spec["_overrides"] == [
        "package_source.requirement=arbiter-suite==0.9.2.dev1"
    ]


def test_local_dev_profile_keeps_hydra_output_dir() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["profile=local-dev", "package_source=local"],
    )

    assert spec["profile"]["name"] == "local-dev"
    assert spec["package_source"]["mode"] == "local"
    assert spec["studio"]["keep_output_dir"] is True


def test_hydra_override_values_with_equals_are_quoted_for_composition() -> None:
    assert (
        studio_config.normalize_hydra_override(
            "package_source.requirement=arbiter-suite==0.9.2.dev1"
        )
        == "package_source.requirement='arbiter-suite==0.9.2.dev1'"
    )


def test_action_override_is_enum_validated() -> None:
    try:
        studio_config.compose_studio_config(
            "install-and-bootstrap",
            ["action=aa"],
        )
    except studio_config.StudioConfigError as exc:
        messages: list[str] = []
        cause: BaseException | None = exc
        while cause is not None:
            messages.append(str(cause))
            cause = cause.__cause__
        message = "\n".join(messages)
        assert "failed to compose media config" in message
        assert "action" in message
        assert "expected one of" in message
    else:
        raise AssertionError("invalid action should fail during composition")
