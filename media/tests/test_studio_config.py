from __future__ import annotations

import importlib.util
import os
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


def recording_run_file_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def action_display(action: dict[str, Any]) -> str:
    return action.get("display", action["run"])


def test_default_config_composes_install_recording() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")

    assert spec["id"] == "install-and-bootstrap"
    assert spec["parameters"]["arbiter_source"] == "latest"
    assert spec["parameters"]["arbiter_package"] == "arbiter-suite"
    assert spec["vars"]["loopback_host"] == "127.0.0.1"
    assert spec["vars"]["staging_port"] == 18075
    assert spec["vars"]["installed_port"] == 8075
    assert spec["vars"]["staging_url"] == "https://127.0.0.1:18075"
    assert spec["vars"]["installed_url"] == "https://127.0.0.1:8075"
    assert spec["environment"]["variables"]["ARBITER_CINEMA_STAGING_URL"] == (
        "https://127.0.0.1:18075"
    )
    assert "package_source" not in spec
    assert spec["_manifest_path"].endswith(
        "media/recording-scripts/install-and-bootstrap.md"
    )
    assert spec["script"] == "media/recording-scripts/install-and-bootstrap.md"
    assert spec["narration"]["scene"]["title"] == "Install Arbiter Server"
    assert spec["narration"]["beats"][0]["id"] == "overview"
    assert spec["narration"]["source_script"] == (
        "media/recording-scripts/install-and-bootstrap.md"
    )


def test_studio_directive_blocks_resolve_omegaconf_interpolation() -> None:
    blocks = studio_config.studio_directive_blocks(
        """# Demo

```yaml studio-directive
recording:
  id: demo
  title: ${recording.id}
  outputs:
    cast: website/static/casts/${recording.id}.cast
```
"""
    )

    assert blocks == [
        {
            "recording": {
                "id": "demo",
                "title": "demo",
                "outputs": {"cast": "website/static/casts/demo.cast"},
            }
        }
    ]


def test_recording_script_accepts_inline_run_at_line_limit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    script = tmp_path / "demo.md"
    inline_run = "\n".join(f"        echo {index}" for index in range(10))
    script.write_text(
        f"""# Demo

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
recording:
  id: demo
  title: Demo
  setup:
  - name: Prepare
    run: |
{inline_run}
  beats:
  - id: one
    actions:
    - run: echo action
```

```yaml studio-directive
beat:
  id: one
  heading: One
  narration: One.
```
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config, "RECORDING_SCRIPT_DIR", tmp_path)

    spec = studio_config.recording_from_script("demo")

    assert studio_config.inline_run_line_count(spec["setup"][0]["run"]) == 10
    assert spec["beats"][0]["actions"][0]["run"] == "echo action"


def test_recording_script_rejects_long_inline_run(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    script = tmp_path / "demo.md"
    inline_run = "\n".join(f"        echo {index}" for index in range(11))
    script.write_text(
        f"""# Demo

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
recording:
  id: demo
  title: Demo
  setup:
  - name: Prepare
    run: |
{inline_run}
```

```yaml studio-directive
beat:
  id: one
  heading: One
  narration: One.
```
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config, "RECORDING_SCRIPT_DIR", tmp_path)

    try:
        studio_config.recording_from_script("demo")
    except studio_config.StudioConfigError as exc:
        message = str(exc)
        assert "recording.setup.1.run has 11 non-empty lines" in message
        assert "Move longer shell into an organized run_file" in message
    else:
        raise AssertionError("long inline run should fail")


def test_install_recording_uses_current_client_discovery_command() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    client_discovery = next(
        beat for beat in spec["beats"] if beat["id"] == "client-discovery"
    )
    action = client_discovery["actions"][0]

    plugin_command = "arbiter arbiter.url=https://127.0.0.1:18075 info plugins | jq ."

    assert plugin_command in action_display(action)
    assert plugin_command in action["run"]
    assert action_display(action).count("arbiter.url=") == 4
    assert action["run"].count("arbiter.url=") == 4
    assert "smtp:send_email" in action["expect"]["output_contains"]


def test_install_recording_checks_config_before_starting_staging() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    stage_server = next(beat for beat in spec["beats"] if beat["id"] == "stage-server")
    action = stage_server["actions"][0]

    assert "./arbiter-docker config check" in action_display(action)
    assert "./arbiter-docker config check" in action["run"]
    assert action["expect"]["output_regex"][:3] == [
        r"server:\s+pass",
        r"imap:\s+pass",
        r"smtp:\s+pass",
    ]
    assert "https://127.0.0.1:18075" in action["expect"]["output_contains"]


def test_install_recording_inspects_selected_bundle_before_prepare() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    prepare_bundle = next(
        beat for beat in spec["beats"] if beat["id"] == "prepare-bundle"
    )
    action = prepare_bundle["actions"][0]

    assert "bundle list" in action["display"]
    assert "bundle list" in action["run"]
    assert "bundle prepare" in action["display"]
    assert "recording_prepare_bundle" in action["run"]
    assert "bundle add imap" not in action["display"]
    assert "bundle add smtp" not in action["display"]
    assert "bundle add imap" not in action["run"]
    assert "bundle add smtp" not in action["run"]


def test_install_recording_prepares_visible_cli_environment() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    prepare_cli = next(beat for beat in spec["beats"] if beat["id"] == "prepare-cli")
    action = prepare_cli["actions"][0]
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert "python3 -m venv arbiter_venv" in action["display"]
    assert "arbiter_venv/bin/python -m pip install arbiter-suite" in action["display"]
    assert "source arbiter_venv/bin/activate" in action["display"]
    assert "arbiter-server version" in action["display"]
    assert "recording_prepare_cli_env" in action["run"]
    assert "./arbiter_venv/bin/activate" in action["expect"]["file_exists"]
    assert spec["setup"][0]["run_file"] == (
        "media/recording-scripts/install-and-bootstrap/setup-main.sh"
    )
    assert "recording_prepare_cli_env()" in setup_run
    assert 'ln -sfn "$operator_venv" arbiter_venv' in setup_run


def test_install_recording_edits_bot_access_and_shows_demo_credentials() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    edit_access = next(
        beat for beat in spec["beats"] if beat["id"] == "edit-bot-access"
    )
    edit_action = edit_access["actions"][0]
    bootstrap_env = next(
        beat for beat in spec["beats"] if beat["id"] == "bootstrap-env"
    )
    env_action = bootstrap_env["actions"][0]

    assert "$EDITOR conf/arbiter/account/imap/bot.yaml" in edit_action["display"]
    assert "recording_apply_mail_lab_config" in edit_action["run"]
    assert "delete: allow" in edit_action["expect"]["output_contains"]
    assert "folder_append: allow" in edit_action["expect"]["output_contains"]
    assert "recording_apply_mail_lab_config --update-env" in env_action["run"]
    assert (
        "IMAP_BOT_ACCOUNT_PASSWORD=bot-password"
        in env_action["expect"]["output_contains"]
    )


def test_install_recording_sends_and_fetches_a_self_addressed_message() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    send = next(beat for beat in spec["beats"] if beat["id"] == "send-test-message")
    fetch = next(beat for beat in spec["beats"] if beat["id"] == "fetch-test-message")

    send_action = send["actions"][0]
    send_check = send["checks"][0]
    fetch_action = fetch["actions"][0]

    assert "smtp:send_email" in action_display(send_action)
    assert "bot@example.test" in action_display(send_action)
    assert "install-smoke-1" in action_display(send_action)
    assert send_check["run_file"] == (
        "media/recording-scripts/install-and-bootstrap/"
        "wait-for-delivered-message.sh"
    )
    assert "imap:search_messages" in action_display(fetch_action)
    assert "imap:get_message" in action_display(fetch_action)
    assert "Hello from Arbiter staging." in fetch_action["expect"]["output_contains"]


def test_install_recording_preinstall_doctor_checks_codex_agent_user() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    preinstall = next(
        beat for beat in spec["beats"] if beat["id"] == "preinstall-check"
    )
    action = preinstall["actions"][0]

    assert (
        "./arbiter-docker doctor --preinstall --agent-user codex"
        in action_display(action)
    )
    assert "./arbiter-docker doctor --preinstall --agent-user codex" in action["run"]


def test_install_recording_install_helper_is_aux_file() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    install = next(beat for beat in spec["beats"] if beat["id"] == "install-server")
    action = install["actions"][0]
    install_run = recording_run_file_text(action["run_file"])

    assert action["run_file"] == (
        "media/recording-scripts/install-and-bootstrap/install-server.sh"
    )
    assert "sudo ./arbiter-docker install" in action["display"]
    assert 'exec fakeroot "$@"' in install_run
    assert "rewrite_install_output()" in install_run


def test_local_arbiter_source_exposes_repo_root_for_docker_bundle_prepare() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["+script_params.arbiter_source=local"],
    )
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert "unset ARBITER_REPO_ROOT" in setup_run
    assert "unset ARBITER_PYTHON" in setup_run
    assert 'export ARBITER_REPO_ROOT="$recording_repo"' in setup_run
    assert 'export ARBITER_PYTHON="$recording_python"' in setup_run
    assert "recording_prepare_bundle()" in setup_run
    assert "recording_apply_mail_lab_config()" in setup_run
    assert './arbiter-docker bundle add-source "$recording_repo/server"' in setup_run
    assert 'local_packages = {"arbiter-server", "arbiter-imap", "arbiter-smtp"}' in (
        setup_run
    )
    assert "arbiter-server version --json || return 1" in setup_run
    assert "arbiter --version || return 1" in setup_run


def test_pypi_arbiter_source_uses_cached_operator_venv() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])
    cached_call = (
        'cached_operator_venv="$(recording_cached_operator_venv '
        '"$package_requirement")"'
    )

    assert "recording_cached_operator_venv()" in setup_run
    assert "recording_operator_venv_cache_root" in setup_run
    assert "recording_operator_venv_is_healthy()" in setup_run
    assert cached_call in setup_run
    assert 'ln -sfn "$cached_operator_venv" "$operator_venv"' in setup_run
    assert '"$recording_python" -m venv "$cached_venv"' in setup_run
    assert 'mv "$tmp_dir" "$cache_dir"' not in setup_run
    assert '"$recording_python" -m venv "$tmp_dir/venv"' not in setup_run
    assert (
        'operator_venv="$recording_tmp/operator-venv"\n'
        '  "$recording_python" -m venv "$operator_venv"'
    ) not in setup_run


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


def test_studio_run_dir_separates_recording_runs_from_helper_jobs() -> None:
    assert (
        studio_config.studio_run_dir(
            "build",
            None,
            False,
            "install-and-bootstrap",
            "20260619-050114",
        )
        == "media/runs/install-and-bootstrap/20260619-050114"
    )
    assert (
        studio_config.studio_run_dir(
            "inspect",
            None,
            False,
            "install-and-bootstrap",
            "20260619-050114",
        )
        == "media/studio-runs/inspect/install-and-bootstrap/20260619-050114"
    )
    assert (
        studio_config.studio_run_dir(
            "build",
            None,
            True,
            "install-and-bootstrap",
            "20260619-050114",
        )
        == "media/studio-runs/build/install-and-bootstrap/20260619-050114"
    )
    assert (
        studio_config.studio_run_dir(
            "build",
            "session",
            False,
            "install-and-bootstrap",
            "20260619-050114",
        )
        == "media/runs/install-and-bootstrap/20260619-050114"
    )


def test_script_parameter_version_override_is_composed() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["+script_params.arbiter_source=0.9.2.dev1"],
    )

    assert spec["parameters"]["arbiter_source"] == "0.9.2.dev1"
    assert spec["parameters"]["arbiter_package"] == "arbiter-suite"
    assert spec["_overrides"] == ["+script_params.arbiter_source=0.9.2.dev1"]


def test_local_script_parameter_keeps_hydra_output_dir() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["+script_params.arbiter_source=local"],
    )

    assert "profile" not in spec
    assert spec["parameters"]["arbiter_source"] == "local"
    assert spec["studio"]["keep_output_dir"] is True


def test_unknown_script_parameter_override_fails() -> None:
    try:
        studio_config.load_recording_spec(
            "install-and-bootstrap",
            ["+script_params.not_a_parameter=value"],
        )
    except studio_config.StudioConfigError as exc:
        assert "unknown script parameter(s): not_a_parameter" in str(exc)
    else:
        raise AssertionError("unknown script parameter should fail")


def test_hydra_override_values_with_equals_are_quoted_for_composition() -> None:
    assert (
        studio_config.normalize_hydra_override(
            "output=path=with=equals"
        )
        == "output='path=with=equals'"
    )


def test_action_override_is_public_enum_validated() -> None:
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
        assert (
            "expected one of [build, check, play, inspect, output, runs, list]"
            in message
        )
        assert "audio_generate" not in message
    else:
        raise AssertionError("invalid action should fail during composition")


def test_audio_publish_step_is_enum_valid() -> None:
    config = studio_config.compose_studio_config(
        "install-and-bootstrap",
        ["step=audio_publish"],
    )

    assert config["step"] == "audio_publish"


def test_output_format_override_is_composed() -> None:
    config = studio_config.compose_studio_config(
        "install-and-bootstrap",
        ["step=audio_dry_run", "output_format=json"],
    )

    assert config["step"] == "audio_dry_run"
    assert config["output_format"] == "json"


def test_runs_limit_defaults_and_overrides_are_composed() -> None:
    default_config = studio_config.compose_studio_config("install-and-bootstrap")
    override_config = studio_config.compose_studio_config(
        "install-and-bootstrap",
        ["action=runs", "runs_since=30m", "runs_limit=25"],
    )

    assert default_config["runs_since"] is None
    assert default_config["runs_limit"] == 10
    assert override_config["runs_since"] == "30m"
    assert override_config["runs_limit"] == 25


def test_load_configured_env_file_loads_missing_values_without_override(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    env_file = tmp_path / "studio.env"
    env_file.write_text(
        """
# comments and export syntax are accepted
export STUDIO_ENV_LOADED='from file'
STUDIO_ENV_KEEP=from-file
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("STUDIO_ENV_LOADED", raising=False)
    monkeypatch.setenv("STUDIO_ENV_KEEP", "from shell")

    loaded = studio_config.load_configured_env_file(
        {
            "load_env_file": True,
            "env_file": str(env_file),
            "env_override": False,
        }
    )

    assert os.environ["STUDIO_ENV_LOADED"] == "from file"
    assert os.environ["STUDIO_ENV_KEEP"] == "from shell"
    assert loaded == {"STUDIO_ENV_LOADED": "from file"}


def test_load_configured_env_file_can_override_existing_values(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    env_file = tmp_path / "studio.env"
    env_file.write_text("STUDIO_ENV_OVERRIDE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_ENV_OVERRIDE", "from-shell")

    loaded = studio_config.load_configured_env_file(
        {
            "load_env_file": True,
            "env_file": str(env_file),
            "env_override": True,
        }
    )

    assert os.environ["STUDIO_ENV_OVERRIDE"] == "from-file"
    assert loaded == {"STUDIO_ENV_OVERRIDE": "from-file"}


def test_load_configured_env_file_can_be_disabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    env_file = tmp_path / "studio.env"
    env_file.write_text("STUDIO_ENV_DISABLED=from-file\n", encoding="utf-8")
    monkeypatch.delenv("STUDIO_ENV_DISABLED", raising=False)

    loaded = studio_config.load_configured_env_file(
        {
            "load_env_file": False,
            "env_file": str(env_file),
            "env_override": True,
        }
    )

    assert loaded == {}
    assert "STUDIO_ENV_DISABLED" not in os.environ


def test_studio_public_actions_are_composed() -> None:
    for action in [
        "build",
        "check",
        "play",
        "inspect",
        "output",
        "runs",
        "list",
    ]:
        config = studio_config.compose_studio_config(
            "install-and-bootstrap",
            [f"action={action}"],
        )
        assert config["action"] == action


def test_studio_internal_steps_are_composed() -> None:
    for step in [
        "record_check",
        "retime_check",
        "audio_generate",
        "audio_publish",
        "align_check",
    ]:
        config = studio_config.compose_studio_config(
            "install-and-bootstrap",
            [f"step={step}"],
        )
        assert config["step"] == step
