from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


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


def load_studio_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "studio.py"
    spec = importlib.util.spec_from_file_location("studio", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["studio"] = module
    spec.loader.exec_module(module)
    return module


studio_config = load_studio_config()
studio = load_studio_tool()


def recording_run_file_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def spec_to_text(spec: Any) -> str:
    return json.dumps(spec, sort_keys=True)


def action_display(action: dict[str, Any]) -> str:
    if "commands" in action:
        return "\n".join(
            command.get("display", command["run"]) for command in action["commands"]
        )
    return action.get("display", action["run"])


def action_run(action: dict[str, Any]) -> str:
    if "commands" in action:
        return "\n".join(command["run"] for command in action["commands"])
    return action["run"]


def test_record_action_preserves_retimed_cast_before_record(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    retimed = tmp_path / "demo.retimed.cast"
    retimed.write_text("old retime\n", encoding="utf-8")
    saw_retimed_exists: list[bool] = []
    spec = {
        "id": "demo",
        "_recording_id": "demo",
        "outputs": {
            "cast": str(tmp_path / "demo.cast"),
            "audio": str(tmp_path / "demo.mp3"),
        },
    }

    monkeypatch.setattr(studio, "container_from_hydra_cfg", lambda cfg: {})
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    def fake_record(cfg: Any) -> int:
        saw_retimed_exists.append(retimed.exists())
        return 0

    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", fake_record)

    studio.run_record_action(
        studio.OmegaConf.create({"output_format": "json"}),
        "record",
        "record baseline cast",
    )

    assert saw_retimed_exists == [True]
    assert retimed.read_text(encoding="utf-8") == "old retime\n"


def test_studio_check_includes_retime_and_alignment_checks(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        studio,
        "run_record_action",
        lambda cfg, action, label=None: calls.append(("record", action)),
    )
    monkeypatch.setattr(
        studio,
        "run_audio_action",
        lambda cfg, action, label=None: calls.append(("audio", action)),
    )
    monkeypatch.setattr(
        studio,
        "run_retime_action",
        lambda cfg, action, label=None: calls.append(("retime", action)),
    )
    monkeypatch.setattr(
        studio,
        "run_align_action",
        lambda cfg, action, label=None: calls.append(("align", action)),
    )

    result = studio.run_check(studio.OmegaConf.create({}))

    assert result == 0
    assert calls == [
        ("record", "check"),
        ("audio", "check"),
        ("retime", "check"),
        ("align", "check"),
    ]


def test_default_config_composes_install_recording() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")

    assert spec["id"] == "install-and-bootstrap"
    assert spec["parameters"]["arbiter_source"] == "local"
    assert spec["parameters"]["arbiter_package"] == "arbiter-suite"
    assert spec["parameters"]["operator_venv_cache_retain"] == 8
    assert spec["vars"]["loopback_host"] == "127.0.0.1"
    assert spec["vars"]["staging_port"] == 18075
    assert spec["vars"]["installed_port"] == 8075
    assert spec["vars"]["staging_url"] == "https://127.0.0.1:18075"
    assert spec["vars"]["installed_url"] == "https://127.0.0.1:8075"
    assert spec["environment"]["variables"]["ARBITER_CINEMA_STAGING_URL"] == (
        "https://127.0.0.1:18075"
    )
    assert spec["style"]["typing"] is True
    assert spec["retime"]["typing_char_delay"] > 0
    assert "package_source" not in spec
    assert spec["_manifest_path"].endswith(
        "media/recording-scripts/install-and-bootstrap.md"
    )
    assert spec["script"] == "media/recording-scripts/install-and-bootstrap.md"
    assert spec["narration"]["scene"]["title"] == "Install Arbiter Server"
    assert spec["narration"]["beats"][0]["id"] == "overview"
    assert spec["narration"]["beats"][0]["text"] == (
        "In this tutorial, we will install Arbiter the standard way: stage it "
        "locally with Reploy, configure and test it, then install it as a "
        "permanent Docker service on the system."
    )
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


def test_narration_anchors_are_stripped_and_preserved() -> None:
    text, anchors, waits = studio_config.narration_text_and_anchors(
        "Prepare first. @install@ Install now."
    )

    assert text == "Prepare first. Install now."
    assert anchors == [
        {
            "id": "install",
            "marker": "@install@",
            "text_offset": len("Prepare first."),
        }
    ]
    assert waits == []


def test_narration_wait_markers_are_stripped_and_preserved() -> None:
    text, anchors, waits = studio_config.narration_text_and_anchors(
        "Run @list@ list options. @wait:list-options+250ms@ @add@ Add plugins."
    )

    assert text == "Run list options. Add plugins."
    assert [anchor["marker"] for anchor in anchors] == ["@list@", "@add@"]
    assert waits == [
        {
            "target": "list-options",
            "marker": "@wait:list-options+250ms@",
            "text_offset": len("Run list options."),
            "gap_seconds": 0.25,
        }
    ]


def test_narration_anchors_reject_mid_word_positions() -> None:
    for text in [
        "In@install@stall now.",
        "Install@install@ now.",
        "@install@Install now.",
        "Install @install@now.",
    ]:
        with pytest.raises(studio_config.StudioConfigError, match="separated"):
            studio_config.narration_text_and_anchors(text)


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


def test_recording_script_accepts_executable_beat_directive(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    script = tmp_path / "demo.md"
    script.write_text(
        """# Demo

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
recording:
  id: demo
  title: Demo
```

```yaml studio-directive
beat:
  id: one
  heading: One
  narration: One.
  caption: Show one.
  actions:
  - run: echo action
```
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(studio_config, "RECORDING_SCRIPT_DIR", tmp_path)

    spec = studio_config.recording_from_script("demo")

    assert spec["narration"]["beats"][0]["text"] == "One."
    assert spec["beats"] == [
        {
            "id": "one",
            "caption": "Show one.",
            "actions": [{"run": "echo action"}],
        }
    ]


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


def test_recording_script_rejects_long_structured_command_run(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    script = tmp_path / "demo.md"
    inline_run = "\n".join(f"          echo {index}" for index in range(11))
    script.write_text(
        f"""# Demo

```yaml studio-directive
scene: Demo
```

```yaml studio-directive
recording:
  id: demo
  title: Demo
  beats:
  - id: one
    actions:
    - commands:
      - run: |
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
        assert (
            "recording.beats.one.actions.1.commands.1.run has 11 non-empty lines"
            in message
        )
        assert "Move longer shell into an organized run_file" in message
    else:
        raise AssertionError("long structured command run should fail")


def test_install_recording_docker_progress_filter_preserves_stdin() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert (
        'recording_filter_docker_compose_progress() {\n  "$recording_python" -c'
        in setup_run
    )
    assert '"$recording_python" - <<' not in setup_run


def test_install_recording_selects_and_builds_bundle() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    prepare_bundle = next(
        beat for beat in spec["beats"] if beat["id"] == "prepare-bundle"
    )
    prepare_bundle_narration = next(
        beat for beat in spec["narration"]["beats"] if beat["id"] == "prepare-bundle"
    )
    action = prepare_bundle["actions"][0]

    assert "bundle list" in action_display(action)
    assert "bundle list" in action_run(action)
    assert "bundle add --name imap,smtp" in action_display(action)
    assert "bundle add --name imap,smtp" in action_run(action)
    assert "bundle build" in action_display(action)
    assert "bundle build" in action_run(action)
    assert action["commands"][0]["id"] == "list-options"
    assert action["commands"][0]["after"] == "@list@"
    assert action["commands"][1]["after"] == "@add@"
    assert action["commands"][2]["after"] == "@build@"
    assert action["commands"][2]["retime"] == "realtime"
    assert action["commands"][2]["post_command_pause"] == 0.0
    assert action["commands"][3]["run"] == "clear"
    assert action["commands"][3]["pre_command_pause"] == 2.0
    assert "display" not in action["commands"][3]
    assert action["commands"][3]["show_prompt_after"] is False
    assert "clear" not in prepare_bundle["guide"]["commands"]
    assert prepare_bundle_narration["waits"] == [
        {
            "target": "list-options",
            "marker": "@wait:list-options@",
            "text_offset": len(
                "Arbiter has a pluggable architecture. For this demo, we add "
                "the IMAP and SMTP plugins. "
                "First, list the available bundle options."
            ),
            "gap_seconds": 0.0,
        }
    ]


def test_install_recording_bootstraps_editable_arbiter_config() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    bootstrap_config = next(
        beat for beat in spec["beats"] if beat["id"] == "bootstrap-config"
    )
    action = bootstrap_config["actions"][0]

    assert action_display(action).splitlines() == [
        "reploy app bootstrap server",
        "reploy app bootstrap --plugins imap,smtp --account bot",
        "find conf -maxdepth 5 -type f | sort",
    ]
    assert action["commands"][0]["after"] == "@server@"
    assert action["commands"][1]["after"] == "@account@"
    assert action["commands"][2]["after"] == "@inspect@"
    assert action["expect"]["file_exists"] == [
        "./conf/arbiter-server.yaml",
        "./conf/plugins.yaml",
        "./conf/arbiter/account/imap/bot.yaml",
        "./conf/arbiter/policy/imap/bot_policy.yaml",
        "./conf/arbiter/account/smtp/bot.yaml",
        "./conf/arbiter/policy/smtp/bot_policy.yaml",
    ]


def test_install_recording_prepares_visible_cli_environment() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    install_reploy = next(
        beat for beat in spec["beats"] if beat["id"] == "install-reploy"
    )
    init_staging = next(beat for beat in spec["beats"] if beat["id"] == "init-staging")
    install_reploy_action = install_reploy["actions"][0]
    init_staging_action = init_staging["actions"][0]
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert spec["retime"]["post_enter_pause"] == 0.35
    assert [beat["id"] for beat in spec["beats"][:3]] == [
        "install-reploy",
        "init-staging",
        "prepare-bundle",
    ]
    assert install_reploy["caption"] == "Install Reploy with the script installer."
    assert install_reploy["guide"] == {
        "commands": [
            "curl -fsSL https://reploy.yadan.net/install.sh | sh",
        ],
        "success_hint": "The installer should report that Reploy is ready.",
    }
    assert "reploy_version" not in spec["parameters"]
    install_reploy_narration = next(
        beat for beat in spec["narration"]["beats"] if beat["id"] == "install-reploy"
    )
    overview_narration = next(
        beat for beat in spec["narration"]["beats"] if beat["id"] == "overview"
    )
    assert overview_narration["viewer_hold"] == 1.0
    assert install_reploy_narration["heading"] == "Install Reploy"
    assert install_reploy_narration["text"] == "First, install Reploy."
    assert "@install@" not in install_reploy_narration["text"]
    assert install_reploy_narration["anchors"] == [
        {
            "id": "install",
            "marker": "@install@",
            "text_offset": len("First,"),
        }
    ]
    assert "curl -fsSL https://reploy.yadan.net/install.sh | sh" in (
        action_display(install_reploy_action)
    )
    assert "| sh -s -- --version 0.4.0.dev1" not in (
        action_display(install_reploy_action)
    )
    assert action_run(install_reploy_action).strip().splitlines() == [
        "recording_install_reploy",
    ]
    assert install_reploy_action["commands"][0]["after"] == "@install@"
    assert install_reploy_action["commands"][0]["output"] == "suppress"
    assert install_reploy_action["commands"][0]["follow_along"] is True
    assert "expect" not in install_reploy_action
    assert action_display(init_staging_action).splitlines() == [
        "reploy stage arbiter-server",
        "cd reploy-staging",
    ]
    assert init_staging_action["commands"][0]["after"] == "@create@"
    assert "pre_enter_pause" not in init_staging_action["commands"][0]
    assert init_staging_action["commands"][0]["post_enter_pause"] == 1.5
    assert "post_enter_pause" not in init_staging_action["commands"][1]
    assert spec["setup"][0]["run_file"] == (
        "media/recording-scripts/install-and-bootstrap/setup-main.sh"
    )
    assert "recording_prepare_cli_env()" not in setup_run
    assert "recording_install_reploy()" in setup_run
    assert 'arbiter_source="${recording_param_arbiter_source:-local}"' in setup_run
    assert 'reploy_source="${recording_param_reploy_source:-local}"' in setup_run
    assert 'reploy_venv="${recording_param_reploy_venv:-../reploy/.venv}"' in setup_run
    assert "recording_param_reploy_version" not in setup_run
    assert "sh -s -- --version" not in setup_run
    assert 'curl -fsSL https://reploy.yadan.net/install.sh | sh' in setup_run
    assert "local Reploy binary not found; using script installer" in setup_run
    assert '"$HOME/.local/bin/reploy" --help' in setup_run
    assert 'export HOME="$recording_workspace"' in setup_run
    assert 'export PATH="$HOME/.local/bin:$PATH"' in setup_run
    assert '--to "$operator_venv/bin"' not in setup_run
    assert "arbiter_venv" not in setup_run
    assert "python3 -m venv arbiter_venv" not in spec_to_text(spec)
    assert "pip install arbiter-suite" not in spec_to_text(spec)


def test_install_recording_stages_with_current_reploy_command() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    init = next(beat for beat in spec["beats"] if beat["id"] == "init-staging")
    action = init["actions"][0]
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert action_display(action).splitlines() == [
        "reploy stage arbiter-server",
        "cd reploy-staging",
    ]
    assert "reploy init" not in action_display(action)
    assert "recording_stage_arbiter" not in action_run(action)
    assert "recording_stage_arbiter()" not in setup_run
    assert 'if [[ "$arbiter_source" == local ]]; then' in action["commands"][0]["run"]
    assert (
        'reploy stage "file:$recording_repo/server/src/arbiter_server/reploy/arbiter.blueprint.yaml"'
        in action["commands"][0]["run"]
    )
    assert "reploy stage arbiter-server" in action["commands"][0]["run"]
    assert action["commands"][1]["run"] == "cd reploy-staging"
    assert "recording_configure_staging_subnet" not in action_run(action)
    assert "recording_configure_staging_subnet()" not in setup_run


def test_local_arbiter_source_exposes_repo_root_for_docker_bundle_prepare() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert "unset ARBITER_REPO_ROOT" in setup_run
    assert "unset ARBITER_PYTHON" in setup_run
    assert 'export ARBITER_REPO_ROOT="$recording_repo"' in setup_run
    assert 'export ARBITER_PYTHON="$recording_python"' in setup_run
    assert "recording_prepare_bundle()" not in setup_run
    assert "recording_apply_mail_lab_config()" in setup_run
    assert '"$recording_repo/media/tools/mail_lab.py" \\' in setup_run
    assert "--host 0.0.0.0" in setup_run
    assert "--container-host host.docker.internal" in setup_run
    assert "bundle add-source" not in setup_run
    assert '"$operator_venv/bin/arbiter-server" version --json || return 1' in setup_run
    assert '"$operator_venv/bin/arbiter" --version || return 1' in setup_run
    assert '"$operator_venv/bin/reploy" --version || return 1' not in setup_run


def test_pypi_arbiter_source_uses_artifact_keyed_operator_venv_cache() -> None:
    spec = studio_config.load_recording_spec(
        "install-and-bootstrap",
        ["+script_params.arbiter_source=latest"],
    )
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert "recording_operator_wheelhouse_metadata()" in setup_run
    assert "recording_prepare_operator_venv_from_wheelhouse()" in setup_run
    assert "recording_prepare_pypi_operator_venv()" in setup_run
    assert (
        'recording_operator_venv_cache_root="$recording_repo/media/cache/operator-venvs"'
        in setup_run
    )
    assert "recording_operator_venv_is_healthy()" in setup_run
    assert '"$venv/bin/arbiter-server" "$venv/bin/arbiter"' in setup_run
    assert 'recording_prepare_pypi_operator_venv "$package_requirement"' in setup_run
    assert 'operator_venv="$recording_tmp/operator-venv"' in setup_run
    assert 'wheelhouse="$recording_tmp/operator-wheelhouse"' in setup_run
    assert "recording_run_operator_venv_step()" in setup_run
    assert 'recording_operator_venv_log="$recording_tmp/operator-venv.log"' in setup_run
    assert '"$recording_python" -m pip wheel \\' in setup_run
    assert "--wheel-dir" in setup_run
    assert '"$recording_python" -m pip download' not in setup_run
    assert "--no-cache-dir" in setup_run
    assert "cache_key" in setup_run
    assert '"$recording_python" -m venv "$cached_venv"' in setup_run
    assert '"$cached_venv/bin/python" -m pip install \\' in setup_run
    assert '--find-links "$wheelhouse"' in setup_run
    assert 'ln -sfn "$cached_venv" "$operator_venv"' in setup_run
    assert 'export ARBITER_CINEMA_OPERATOR_VENV_CACHE_KEY="$cache_key"' in (setup_run)
    assert (
        'operator_venv_cache_retain="${recording_param_operator_venv_cache_retain:-8}"'
        in setup_run
    )
    assert "recording_validate_operator_venv_cache_retain()" in setup_run
    assert "recording_prune_operator_venv_cache()" in setup_run
    assert 'touch "$ready_file"' in setup_run
    assert 'recording_prune_operator_venv_cache "$cache_key"' in setup_run
    assert 'rmdir "$lock_dir" 2>/dev/null || true\n        return 1' in setup_run
    assert "keep.add(current_cache_key)" in setup_run
    assert "reploy_package" not in setup_run


def test_local_arbiter_source_uses_dirty_source_artifact_keyed_operator_cache() -> None:
    spec = studio_config.load_recording_spec("install-and-bootstrap")
    setup_run = recording_run_file_text(spec["setup"][0]["run_file"])

    assert "recording_prepare_local_operator_venv()" in setup_run
    assert "recording_wheel_requirement()" in setup_run
    assert "recording_wheel_path()" in setup_run
    assert "command -v arbiter-server" not in setup_run
    assert "command -v arbiter >/dev/null" not in setup_run
    assert (
        "server\n    client\n    plugins/smtp\n    plugins/imap\n    meta/arbiter-suite"
        in setup_run
    )
    assert "build local operator wheel: $package_source" in setup_run
    assert "build local operator dependency wheelhouse" not in setup_run
    assert '"$recording_python" -m pip wheel \\' in setup_run
    assert '--wheel-dir "$wheelhouse"' in setup_run
    assert '"$source_dir"' in setup_run
    assert (
        'package_requirement="$(recording_wheel_requirement "$wheelhouse" "$arbiter_package")"'
        in setup_run
    )
    assert (
        'local_package_wheels+=("$(recording_wheel_path "$wheelhouse" "$package_name")")'
        in setup_run
    )
    assert '--find-links "$wheelhouse"' in setup_run
    assert "recording_prepare_operator_venv_from_wheelhouse \\" in setup_run
    assert '"$package_requirement" \\' in setup_run
    assert "reploy_package" not in setup_run
    assert 'export ARBITER_REPO_ROOT="$recording_repo"' in setup_run
    assert 'export ARBITER_PYTHON="$recording_python"' in setup_run


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
        studio_config.normalize_hydra_override("output=path=with=equals")
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
            "expected one of [build, check, clean, play, inspect, output, runs, list]"
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


def test_verbose_defaults_and_overrides_are_composed() -> None:
    default_config = studio_config.compose_studio_config("install-and-bootstrap")
    override_config = studio_config.compose_studio_config(
        "install-and-bootstrap",
        ["verbose=true"],
    )

    assert default_config["verbose"] is False
    assert override_config["verbose"] is True


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
