from __future__ import annotations

import importlib.util
import json
import os
import pytest
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_record_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "record.py"
    spec = importlib.util.spec_from_file_location("record", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["record"] = module
    spec.loader.exec_module(module)
    return module


record = load_record_tool()


def minimal_spec(*, beats: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "test-recording",
        "title": "Test Recording",
        "outputs": {"cast": "website/static/casts/test.cast"},
        "_hydra_output_dir": "/tmp/arbiter-media-test-runs/test-recording",
        "_keep_hydra_output_dir": False,
        "style": {"color": False, "typing": False},
        "environment": {"working_directory": "."},
        "beats": beats,
    }


def write_cast(path: Path, events: list[tuple[float, str]]) -> None:
    lines = [
        json.dumps(
            {
                "version": 3,
                "term": {"cols": 80, "rows": 24},
                "timestamp": 1,
                "title": "test cast",
            }
        )
    ]
    lines.extend(json.dumps([delay, "o", text]) for delay, text in events)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_cast_event_times(path: Path) -> list[float]:
    absolute_time = 0.0
    times: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        event = json.loads(line)
        absolute_time += event[0]
        times.append(round(absolute_time, 3))
    return times


def write_recorded_header(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "term": {"cols": 80, "rows": 24},
                "timestamp": 1,
                "idle_time_limit": 4.0,
                "command": "/home/example/project/.venv/bin/python media/tools/record.py",
                "env": {"SHELL": "/bin/zsh"},
                "title": "test cast",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record.timeline_path_for_cast(path).write_text("", encoding="utf-8")


def run_rendered_session(
    spec: dict[str, Any], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    script = record.render_session_script(spec)
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        ["bash"],
        input=script,
        cwd=REPO_ROOT,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_session_path_uses_invoking_python_bin_without_resolving_symlink() -> None:
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])

    script = record.render_session_script(spec)

    expected_path = Path(record.sys.executable).parent
    expected_export = f'export PATH={record.shell_quote(expected_path)}:"$PATH"'
    assert expected_export in script


def test_session_exports_configured_environment_variables() -> None:
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    spec["environment"]["variables"] = {
        "ARBITER_CINEMA_STAGING_SUBNET": "10.213.240.0/24",
        "ARBITER_CINEMA_TEST_FLAG": True,
    }

    script = record.render_session_script(spec)

    assert "export ARBITER_CINEMA_STAGING_SUBNET=10.213.240.0/24" in script
    assert "export ARBITER_CINEMA_TEST_FLAG=True" in script


def test_requirements_search_invoking_python_bin(
    tmp_path: Path, monkeypatch: Any
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_command = bin_dir / "fake-recording-command"
    fake_command.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_command.chmod(0o755)
    monkeypatch.setattr(record.sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", "")

    record.check_required_commands(
        {"requirements": {"commands": ["fake-recording-command"]}}
    )


def test_record_omits_idle_limit_by_default_and_sanitizes_header(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        write_recorded_header(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.record(
        spec, dry_run=False, check_only=False, output_override=str(cast)
    )

    assert result == 0
    assert "--idle-time-limit" not in commands[0]
    assert "--headless" in commands[0]
    header = json.loads(cast.read_text(encoding="utf-8").splitlines()[0])
    assert (
        header["command"]
        == "media/tools/record.py recording=test-recording step=session"
    )
    assert "env" not in header
    assert "idle_time_limit" not in header


def test_record_headed_override_omits_headless_arg(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    spec["capture"] = {"headless": True}
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        write_recorded_header(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.record(
        spec,
        dry_run=False,
        check_only=False,
        output_override=str(cast),
        headed=True,
    )

    assert result == 0
    assert "--headless" not in commands[0]


def test_record_stages_cast_and_timeline_before_replacing_outputs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    timeline = record.timeline_path_for_cast(cast)
    cast.write_text("old cast\n", encoding="utf-8")
    timeline.write_text("old timeline\n", encoding="utf-8")
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    run_dir = tmp_path / "runs" / "test-recording" / "20260616-160412"
    spec["_hydra_output_dir"] = str(run_dir)
    staged_casts: list[Path] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        staged_cast = Path(command[-1])
        staged_casts.append(staged_cast)
        assert staged_cast != cast
        assert staged_cast.name.startswith(".recording.cast.recording-")
        assert cast.read_text(encoding="utf-8") == "old cast\n"
        assert timeline.read_text(encoding="utf-8") == "old timeline\n"
        write_recorded_header(staged_cast)
        record.timeline_path_for_cast(staged_cast).write_text(
            '{"phase": "done"}\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.record(
        spec, dry_run=False, check_only=False, output_override=str(cast)
    )

    assert result == 0
    assert staged_casts
    assert not staged_casts[0].exists()
    assert not record.timeline_path_for_cast(staged_casts[0]).exists()
    assert (run_dir / "recording.cast").exists()
    assert (run_dir / "recording.timeline.jsonl").exists()
    assert (
        json.loads(cast.read_text(encoding="utf-8").splitlines()[0])["command"]
        == "media/tools/record.py recording=test-recording step=session"
    )
    assert timeline.read_text(encoding="utf-8") == '{"phase": "done"}\n'


def test_record_preserves_failed_artifacts_when_postprocess_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    run_dir = tmp_path / "runs" / "test-recording" / "20260619-115350"
    spec["_hydra_output_dir"] = str(run_dir)
    staged_casts: list[Path] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        staged_cast = Path(command[-1])
        staged_casts.append(staged_cast)
        write_recorded_header(staged_cast)
        record.timeline_path_for_cast(staged_cast).write_text(
            '{"phase": "done"}\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0)

    def fail_postprocess(cast_path: Path, intervals: list[tuple[float, float]]) -> None:
        raise record.RecordingError("postprocess failed")

    monkeypatch.setattr(record.subprocess, "run", fake_run)
    monkeypatch.setattr(record, "strip_cast_intervals", fail_postprocess)

    with pytest.raises(record.RecordingError, match="postprocess failed"):
        record.record(spec, dry_run=False, check_only=False, output_override=str(cast))

    assert staged_casts
    assert not staged_casts[0].exists()
    assert not record.timeline_path_for_cast(staged_casts[0]).exists()
    assert (run_dir / "failed.cast").exists()
    assert (run_dir / "failed.timeline.jsonl").exists()
    assert not cast.exists()


def test_record_interrupt_removes_staged_artifacts_and_keeps_outputs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    timeline = record.timeline_path_for_cast(cast)
    cast.write_text("old cast\n", encoding="utf-8")
    timeline.write_text("old timeline\n", encoding="utf-8")
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    staged_casts: list[Path] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        staged_cast = Path(command[-1])
        staged_casts.append(staged_cast)
        staged_cast.write_text("partial cast\n", encoding="utf-8")
        record.timeline_path_for_cast(staged_cast).write_text(
            "partial timeline\n", encoding="utf-8"
        )
        record.failure_path_for_cast(staged_cast).write_text(
            "partial failure\n", encoding="utf-8"
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    try:
        record.record(spec, dry_run=False, check_only=False, output_override=str(cast))
    except record.RecordingInterrupted as exc:
        message = str(exc)
    else:
        raise AssertionError("interrupted recording should raise RecordingInterrupted")

    assert "recording cancelled by user" in message
    assert f"output was not updated: {cast}" in message
    assert cast.read_text(encoding="utf-8") == "old cast\n"
    assert timeline.read_text(encoding="utf-8") == "old timeline\n"
    assert staged_casts
    assert not staged_casts[0].exists()
    assert not record.timeline_path_for_cast(staged_casts[0]).exists()
    assert not record.failure_path_for_cast(staged_casts[0]).exists()


def test_record_interrupt_returncode_removes_staged_artifacts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    cast.write_text("old cast\n", encoding="utf-8")
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    staged_casts: list[Path] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        staged_cast = Path(command[-1])
        staged_casts.append(staged_cast)
        staged_cast.write_text("partial cast\n", encoding="utf-8")
        record.timeline_path_for_cast(staged_cast).write_text(
            "partial timeline\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 130)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    try:
        record.record(spec, dry_run=False, check_only=False, output_override=str(cast))
    except record.RecordingInterrupted as exc:
        message = str(exc)
    else:
        raise AssertionError("interrupted recording should raise RecordingInterrupted")

    assert "recording cancelled by user" in message
    assert cast.read_text(encoding="utf-8") == "old cast\n"
    assert staged_casts
    assert not staged_casts[0].exists()
    assert not record.timeline_path_for_cast(staged_casts[0]).exists()


def test_main_reports_keyboard_interrupt_without_traceback(
    monkeypatch: Any, capsys: Any
) -> None:
    def fake_record(*args: Any, **kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(record, "record", fake_record)
    monkeypatch.setattr(
        record.sys,
        "argv",
        ["record.py", "recording=install-and-bootstrap"],
    )

    with pytest.raises(SystemExit) as exc_info:
        record.main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 130
    assert "interrupted: recording cancelled by user" in captured.err
    assert "Traceback" not in captured.err


def test_record_preserves_explicit_idle_limit(tmp_path: Path, monkeypatch: Any) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    spec["capture"] = {"idle_time_limit": 3.0}
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        write_recorded_header(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.record(
        spec, dry_run=False, check_only=False, output_override=str(cast)
    )

    assert result == 0
    assert commands[0][commands[0].index("--idle-time-limit") + 1] == "3.0"
    header = json.loads(cast.read_text(encoding="utf-8").splitlines()[0])
    assert header["idle_time_limit"] == 3.0


def test_baseline_compressed_disables_session_typing() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "one",
                "actions": [{"run": "true"}],
                "viewer_hold": 3.0,
            }
        ]
    )
    spec["capture"] = {"baseline_compressed": True}
    spec["style"] = {"color": False, "typing": True}

    script = record.render_session_script(spec)

    assert "recording_baseline_compressed=1" in script
    assert "recording_typing=0" in script
    assert 'if [[ "$recording_baseline_compressed" != 1 ]]; then' in script


def test_script_parameters_export_session_variables() -> None:
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    spec["parameters"] = {
        "arbiter_source": "latest",
        "arbiter_package": "arbiter-suite",
    }

    script = record.render_session_script(spec)

    assert "recording_param_arbiter_source=latest" in script
    assert "recording_param_arbiter_package=arbiter-suite" in script
    assert "recording_operator_venv_cache_root=" in script
    assert "media/cache/operator-venvs" in script


def test_script_parameter_names_must_be_shell_safe() -> None:
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    spec["parameters"] = {"not-safe": "value"}

    try:
        record.validate_manifest(spec)
    except record.RecordingError as exc:
        assert "parameters keys must be shell-safe names" in str(exc)
    else:
        raise AssertionError("invalid parameter name should fail validation")


def test_visible_commands_cannot_consume_remaining_session_stdin(
    tmp_path: Path,
) -> None:
    consumed = tmp_path / "consumed-stdin.txt"
    run_dir = tmp_path / "run"
    spec = minimal_spec(
        beats=[
            {
                "id": "reads-stdin",
                "actions": [{"run": f"cat > {shlex.quote(str(consumed))}"}],
            },
            {
                "id": "still-runs",
                "actions": [{"run": "printf 'second beat ran\\n'"}],
            },
        ]
    )
    spec["_hydra_output_dir"] = str(run_dir)

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert consumed.read_text(encoding="utf-8") == ""
    assert "second beat ran" in result.stdout


def test_setup_can_update_postmortem_entrypoint(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    workspace = run_dir / "operator-workspace"
    venv = run_dir / "operator-venv"
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["_hydra_output_dir"] = str(run_dir)
    spec["_keep_hydra_output_dir"] = True
    spec["setup"] = [
        {
            "name": "prepare postmortem workspace",
            "run": f"""
              mkdir -p {shlex.quote(str(workspace))} {shlex.quote(str(venv / "bin"))}
              : > {shlex.quote(str(venv / "bin" / "activate"))}
              recording_write_postmortem_entrypoint {shlex.quote(str(workspace))} {shlex.quote(str(venv))}
              cd {shlex.quote(str(workspace))}
            """,
            "expect": {"file_exists": ["$recording_postmortem_path"]},
        }
    ]

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    entrypoint = run_dir / "enter"
    metadata_path = run_dir / "enter.json"
    assert os.access(entrypoint, os.X_OK)
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "entrypoint": str(entrypoint),
        "run_dir": str(run_dir),
        "venv": str(venv),
        "workdir": str(workspace),
    }
    script = entrypoint.read_text(encoding="utf-8")
    assert f"workdir={shlex.quote(str(workspace))}" in script
    assert f"venv={shlex.quote(str(venv))}" in script
    assert "export ARBITER_CINEMA_POSTMORTEM=1" in script
    assert "export ARBITER_CINEMA_RUN_ID=run" in script
    assert "[arbiter-recorder:run]" in script
    assert 'ZDOTDIR="$zsh_dir" exec "$shell_path" -i' in script
    assert 'exec "$shell_path" --rcfile "$bashrc" -i' in script


def test_inspect_run_invokes_postmortem_entrypoint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["_hydra_output_dir"] = str(tmp_path / "runs" / "test-recording" / "current")
    run_dir = tmp_path / "runs" / "test-recording" / "20260616-160412"
    run_dir.mkdir(parents=True)
    entrypoint = run_dir / "enter"
    venv = run_dir / "operator-venv"
    workspace = run_dir / "operator-workspace"
    entrypoint.write_text("#!/usr/bin/env bash\n# old prompt\n", encoding="utf-8")
    (run_dir / "enter.json").write_text(
        json.dumps(
            {
                "entrypoint": str(entrypoint),
                "run_dir": str(run_dir),
                "venv": str(venv),
                "workdir": str(workspace),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    assert record.inspect_run(spec, run_id="20260616-160412") == 0
    assert commands == [[str(entrypoint)]]
    assert "[arbiter-recorder:20260616-160412]" in entrypoint.read_text(
        encoding="utf-8"
    )


def test_run_id_lookup_uses_media_runs_when_current_job_is_studio_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["_hydra_output_dir"] = str(
        tmp_path
        / "media"
        / "studio-runs"
        / "output"
        / "test-recording"
        / "20260619-051900"
    )
    run_dir = tmp_path / "media" / "runs" / "test-recording" / "20260619-051840"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    assert record.run_dir_for_id(spec, "20260619-051840") == run_dir


def test_inspect_without_run_id_uses_latest_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    old_run = tmp_path / "media" / "runs" / "install" / "20260616-150000"
    new_run = tmp_path / "media" / "runs" / "install" / "20260616-160000"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    old_entrypoint = old_run / "enter"
    new_entrypoint = new_run / "enter"
    old_entrypoint.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    new_entrypoint.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    for run_dir in [old_run, new_run]:
        (run_dir / "enter.json").write_text(
            json.dumps(
                {
                    "entrypoint": str(run_dir / "enter"),
                    "run_dir": str(run_dir),
                    "venv": "",
                    "workdir": str(run_dir / "operator-workspace"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    assert record.inspect_run(None, run_id=None) == 0
    assert commands == [[str(new_entrypoint)]]


def test_inspect_without_run_id_ignores_latest_empty_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    good_run = tmp_path / "media" / "runs" / "install" / "20260619-045842"
    empty_run = tmp_path / "media" / "runs" / "install" / "20260619-050114"
    good_run.mkdir(parents=True)
    empty_run.mkdir(parents=True)
    entrypoint = good_run / "enter"
    entrypoint.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (good_run / "enter.json").write_text(
        json.dumps(
            {
                "entrypoint": str(entrypoint),
                "run_dir": str(good_run),
                "venv": "",
                "workdir": str(good_run / "operator-workspace"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    assert record.inspect_run({"id": "install"}, run_id=None) == 0
    assert commands == [[str(entrypoint)]]


def test_output_run_prints_captured_output_when_not_interactive(
    tmp_path: Path, capsys: Any
) -> None:
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["_hydra_output_dir"] = str(tmp_path / "runs" / "test-recording" / "current")
    run_dir = tmp_path / "runs" / "test-recording" / "20260616-160412"
    run_dir.mkdir(parents=True)
    output_path = run_dir / "stage_server_1.out"
    output_path.write_text("captured output\n", encoding="utf-8")
    (run_dir / "failure.json").write_text(
        json.dumps({"output_path": str(output_path)}) + "\n",
        encoding="utf-8",
    )

    assert record.output_run(spec, run_id="20260616-160412") == 0
    assert capsys.readouterr().out == "captured output\n"


def test_output_without_run_id_uses_latest_run(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    old_run = tmp_path / "media" / "runs" / "install" / "20260616-150000"
    new_run = tmp_path / "media" / "runs" / "install" / "20260616-160000"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    old_output = old_run / "stage.out"
    new_output = new_run / "stage.out"
    old_output.write_text("old output\n", encoding="utf-8")
    new_output.write_text("new output\n", encoding="utf-8")
    (old_run / "failure.json").write_text(
        json.dumps({"output_path": str(old_output)}) + "\n",
        encoding="utf-8",
    )
    (new_run / "failure.json").write_text(
        json.dumps({"output_path": str(new_output)}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    assert record.output_run(None, run_id=None) == 0
    assert capsys.readouterr().out == "new output\n"


def test_output_without_run_id_ignores_latest_empty_run(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    good_run = tmp_path / "media" / "runs" / "install" / "20260619-045842"
    empty_run = tmp_path / "media" / "runs" / "install" / "20260619-050114"
    good_run.mkdir(parents=True)
    empty_run.mkdir(parents=True)
    output = good_run / "stage.out"
    output.write_text("real failure\n", encoding="utf-8")
    (good_run / "failure.json").write_text(
        json.dumps({"output_path": str(output)}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    assert record.output_run({"id": "install"}, run_id=None) == 0
    assert capsys.readouterr().out == "real failure\n"


def test_play_run_finds_preserved_cast_by_run_id(
    tmp_path: Path, monkeypatch: Any
) -> None:
    run_dir = tmp_path / "media" / "runs" / "test-recording" / "20260616-160412"
    run_dir.mkdir(parents=True)
    cast = run_dir / "recording.cast"
    cast.write_text("cast\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.play_recording(
        None,
        run_id="20260616-160412",
        cast_override=None,
    )

    assert result == 0
    assert commands == [["asciinema", "play", str(cast)]]


def test_play_without_run_id_uses_latest_preserved_cast(
    tmp_path: Path, monkeypatch: Any
) -> None:
    old_run = tmp_path / "media" / "runs" / "one" / "20260616-150000"
    new_run = tmp_path / "media" / "runs" / "two" / "20260616-160000"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    old_cast = old_run / "recording.cast"
    new_cast = new_run / "failed.cast"
    old_cast.write_text("old\n", encoding="utf-8")
    new_cast.write_text("new\n", encoding="utf-8")
    os.utime(old_cast, (100.0, 100.0))
    os.utime(new_cast, (200.0, 200.0))
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.play_recording(None, run_id=None, cast_override=None)

    assert result == 0
    assert commands == [["asciinema", "play", str(new_cast)]]


def test_play_without_run_id_ignores_latest_empty_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    good_run = tmp_path / "media" / "runs" / "install" / "20260619-045842"
    empty_run = tmp_path / "media" / "runs" / "install" / "20260619-050114"
    good_run.mkdir(parents=True)
    empty_run.mkdir(parents=True)
    cast = good_run / "failed.cast"
    cast.write_text("cast\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.play_recording(
        {"id": "install"},
        run_id=None,
        cast_override=None,
    )

    assert result == 0
    assert commands == [["asciinema", "play", str(cast)]]


def test_play_without_run_id_filters_to_explicit_recording(
    tmp_path: Path, monkeypatch: Any
) -> None:
    other_run = tmp_path / "media" / "runs" / "other" / "20260616-170000"
    install_run = tmp_path / "media" / "runs" / "install" / "20260616-160000"
    other_run.mkdir(parents=True)
    install_run.mkdir(parents=True)
    other_cast = other_run / "recording.cast"
    install_cast = install_run / "recording.cast"
    other_cast.write_text("other\n", encoding="utf-8")
    install_cast.write_text("install\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    result = record.play_recording(
        {"id": "install"},
        run_id=None,
        cast_override=None,
    )

    assert result == 0
    assert commands == [["asciinema", "play", str(install_cast)]]


def test_collect_run_jobs_reports_success_and_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    success_run = tmp_path / "media" / "runs" / "install" / "20260619-030000"
    failure_run = tmp_path / "media" / "runs" / "install" / "20260619-031000"
    success_run.mkdir(parents=True)
    failure_run.mkdir(parents=True)
    write_cast(
        success_run / "recording.cast",
        [(1.0, "first"), (2.25, "second")],
    )
    (failure_run / "failed.cast").write_text("failed cast\n", encoding="utf-8")
    (failure_run / "failure.json").write_text(
        json.dumps(
            {
                "step_name": "Start staging",
                "message": "missing text: URL",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    jobs = record.collect_run_jobs(
        "install",
        now=record.datetime(2026, 6, 19, 3, 15, 0),
    )

    assert jobs == [
        {
            "job_id": "20260619-031000",
            "age": "5m ago",
            "age_seconds": 300,
            "type": "install",
            "result": "failed",
            "length": None,
            "length_seconds": None,
            "reason": "Start staging: missing text: URL",
        },
        {
            "job_id": "20260619-030000",
            "age": "15m ago",
            "age_seconds": 900,
            "type": "install",
            "result": "success",
            "length": "3.2s",
            "length_seconds": 3.25,
            "reason": None,
        },
    ]


def test_list_run_jobs_outputs_table_and_json(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    run_dir = tmp_path / "media" / "runs" / "install" / "20260619-030000"
    run_dir.mkdir(parents=True)
    write_cast(run_dir / "recording.cast", [(1.5, "output")])
    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    assert (
        record.list_run_jobs(
            recording_id=None,
            output_format="text",
            now=record.datetime(2026, 6, 19, 3, 15, 0),
        )
        == 0
    )
    table = capsys.readouterr().out
    assert "job_id" in table
    assert "age" in table
    assert "type" in table
    assert "result" in table
    assert "length" in table
    assert "reason" in table
    assert "20260619-030000" in table
    assert "15m ago" in table
    assert "success" in table
    assert "1.5s" in table

    assert (
        record.list_run_jobs(
            recording_id=None,
            output_format="json",
            now=record.datetime(2026, 6, 19, 3, 15, 0),
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data[0]["job_id"] == "20260619-030000"
    assert data[0]["age"] == "15m ago"
    assert data[0]["age_seconds"] == 900
    assert data[0]["type"] == "install"
    assert data[0]["result"] == "success"
    assert data[0]["length_seconds"] == 1.5


def test_collect_run_jobs_limits_by_time_and_count(
    tmp_path: Path, monkeypatch: Any
) -> None:
    for run_id in [
        "20260619-024000",
        "20260619-025000",
        "20260619-030000",
        "20260619-031000",
    ]:
        run_dir = tmp_path / "media" / "runs" / "install" / run_id
        run_dir.mkdir(parents=True)
        write_cast(run_dir / "recording.cast", [(1.0, "output")])
    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    jobs = record.collect_run_jobs(
        "install",
        since=record.parse_runs_since("30m"),
        limit=2,
        now=record.datetime(2026, 6, 19, 3, 15, 0),
    )

    assert [job["job_id"] for job in jobs] == [
        "20260619-031000",
        "20260619-030000",
    ]


def test_action_runs_filters_only_when_recording_is_explicit(monkeypatch: Any) -> None:
    calls: list[tuple[str | None, str, object, object]] = []

    monkeypatch.setattr(
        record,
        "control_config_from_hydra_cfg",
        lambda _cfg: {
            "action": "runs",
            "output_format": "text",
            "runs_since": "30m",
            "runs_limit": 5,
        },
    )
    monkeypatch.setattr(
        record,
        "list_run_jobs",
        lambda *, recording_id, output_format, since, limit: calls.append(
            (recording_id, output_format, since, limit)
        )
        or 0,
    )

    monkeypatch.setattr(
        record,
        "spec_from_hydra_cfg",
        lambda _cfg: {"id": "install", "_overrides": ["action=runs"]},
    )
    assert record.run_tool_from_hydra_cfg(object()) == 0

    monkeypatch.setattr(
        record,
        "spec_from_hydra_cfg",
        lambda _cfg: {
            "id": "install",
            "_overrides": ["recording=install", "action=runs"],
        },
    )
    assert record.run_tool_from_hydra_cfg(object()) == 0

    assert calls == [
        (None, "text", record.timedelta(minutes=30), 5),
        ("install", "text", record.timedelta(minutes=30), 5),
    ]


def test_action_play_without_explicit_recording_uses_latest_run(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[dict[str, Any] | None, str | None, str | None]] = []

    monkeypatch.setattr(
        record,
        "control_config_from_hydra_cfg",
        lambda _cfg: {"action": "play", "run_id": None, "cast": None},
    )
    monkeypatch.setattr(
        record,
        "spec_from_hydra_cfg",
        lambda _cfg: {"_overrides": ["action=play"], "outputs": {"cast": "out.cast"}},
    )

    def fake_play(
        spec: dict[str, Any] | None,
        *,
        run_id: str | None,
        cast_override: str | None,
    ) -> int:
        calls.append((spec, run_id, cast_override))
        return 0

    monkeypatch.setattr(record, "play_recording", fake_play)

    assert record.run_tool_from_hydra_cfg(object()) == 0
    assert calls == [(None, None, None)]


def test_action_inspect_without_explicit_recording_uses_latest_run(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[dict[str, Any] | None, str | None]] = []

    monkeypatch.setattr(
        record,
        "control_config_from_hydra_cfg",
        lambda _cfg: {"action": "inspect", "run_id": None},
    )
    monkeypatch.setattr(
        record,
        "spec_from_hydra_cfg",
        lambda _cfg: {"_overrides": ["action=inspect"], "id": "install"},
    )

    def fake_inspect(spec: dict[str, Any] | None, *, run_id: str | None) -> int:
        calls.append((spec, run_id))
        return 0

    monkeypatch.setattr(record, "inspect_run", fake_inspect)

    assert record.run_tool_from_hydra_cfg(object()) == 0
    assert calls == [(None, None)]


def test_action_output_without_explicit_recording_uses_latest_run(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[dict[str, Any] | None, str | None]] = []

    monkeypatch.setattr(
        record,
        "control_config_from_hydra_cfg",
        lambda _cfg: {"action": "output", "run_id": None},
    )
    monkeypatch.setattr(
        record,
        "spec_from_hydra_cfg",
        lambda _cfg: {"_overrides": ["action=output"], "id": "install"},
    )

    def fake_output(spec: dict[str, Any] | None, *, run_id: str | None) -> int:
        calls.append((spec, run_id))
        return 0

    monkeypatch.setattr(record, "output_run", fake_output)

    assert record.run_tool_from_hydra_cfg(object()) == 0
    assert calls == [(None, None)]


def test_play_run_reports_ambiguous_run_id(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "media" / "runs" / "one" / "20260616-160412").mkdir(parents=True)
    (tmp_path / "media" / "runs" / "two" / "20260616-160412").mkdir(parents=True)

    monkeypatch.setattr(record, "REPO_ROOT", tmp_path)

    try:
        record.play_recording(None, run_id="20260616-160412", cast_override=None)
    except record.RecordingError as exc:
        assert "ambiguous across recordings" in str(exc)
        assert "add recording=<id>" in str(exc)
    else:
        raise AssertionError("ambiguous run id should require recording")


def test_timeline_progress_messages_use_setup_stage_and_command_labels() -> None:
    spec = minimal_spec(
        beats=[
            {"id": "init", "caption": "Create staging."},
            {"id": "run", "caption": "Start server."},
        ]
    )
    beat_indexes, beat_count = record.beat_progress_index(spec)

    assert (
        record.progress_message_for_event(
            {
                "phase": "check_start",
                "beat": "__setup__",
                "check": "Prepare workspace",
            },
            beat_indexes=beat_indexes,
            beat_count=beat_count,
        )
        == "setup: Prepare workspace"
    )
    assert (
        record.progress_message_for_event(
            {
                "phase": "caption_start",
                "beat": "run",
                "caption": "Start server.",
            },
            beat_indexes=beat_indexes,
            beat_count=beat_count,
        )
        == "stage 2/2: Start server."
    )
    assert (
        record.progress_message_for_event(
            {
                "phase": "command_run_start",
                "beat": "run",
                "command": "./arbiter-docker up\n./arbiter-docker test",
            },
            beat_indexes=beat_indexes,
            beat_count=beat_count,
        )
        == "running: ./arbiter-docker up"
    )


def test_multiline_display_renders_separate_prompts() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "install",
                "caption": "Install things.",
                "actions": [
                    {
                        "display": "python3 -m venv .venv\n.venv/bin/python -m pip install arbiter-suite\n",
                        "run": "printf 'installed\\n'",
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "$ python3 -m venv .venv\n" in result.stdout
    assert "$ .venv/bin/python -m pip install arbiter-suite\n" in result.stdout
    assert "installed\n" in result.stdout


def test_multiline_action_outputs_after_each_command() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "inspect",
                "actions": [
                    {
                        "display": "echo first\necho second\n",
                        "run": "printf 'first\\n'\nprintf 'second\\n'\n",
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    first_prompt_text = "$ echo first\n"
    second_prompt_text = "$ echo second\n"
    first_prompt = result.stdout.index(first_prompt_text)
    first_output = result.stdout.index("first\n", first_prompt + len(first_prompt_text))
    second_prompt = result.stdout.index(second_prompt_text)
    second_output = result.stdout.index(
        "second\n", second_prompt + len(second_prompt_text)
    )
    assert first_prompt < first_output < second_prompt < second_output


def test_color_enabled_recording_forces_color_environment() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "show",
                "actions": [
                    {
                        "run": (
                            'printf "color=%s term=%s force=%s\\n" '
                            '"$ARBITER_COLOR" "$TERM" "$FORCE_COLOR"'
                        )
                    }
                ],
            }
        ]
    )
    spec["style"] = {"color": True, "typing": False}

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "color=always term=xterm-256color force=1\n" in result.stdout


def test_output_expectations_match_ansi_stripped_text() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "check",
                "actions": [
                    {
                        "run": "printf '\\033[94mserver\\033[0m: \\033[32mpass\\033[0m\\n'",
                        "expect": {
                            "output_contains": ["server: pass"],
                            "output_regex": ["server: pass"],
                        },
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr


def test_script_cleanup_runs_after_success(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    target = tmp_path / "cleanup-target"
    spec = minimal_spec(
        beats=[
            {
                "id": "work",
                "actions": [
                    {
                        "run": (
                            f"touch {shlex.quote(str(target))}\n"
                            f"test -e {shlex.quote(str(target))}"
                        )
                    }
                ],
            }
        ]
    )
    spec["_hydra_output_dir"] = str(run_dir)
    spec["_keep_hydra_output_dir"] = True
    spec["cleanup"] = [
        {
            "name": "Remove target",
            "run": (f"rm -f {shlex.quote(str(target))}\n" "printf 'cleanup ran\\n'"),
        }
    ]

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert not target.exists()
    stdout_log = (run_dir / "stdout").read_text(encoding="utf-8")
    assert "::: cleanup cleanup_1 start Remove target\n" in stdout_log
    assert "::: cleanup cleanup_1 end status=0\n" in stdout_log
    assert "cleanup ran\n" in stdout_log
    assert not (run_dir / "cleanup_1.out").exists()
    assert not (run_dir / "cleanup_1.name").exists()
    assert not (run_dir / "cleanup_1.status").exists()
    assert "cleanup ran" not in result.stdout


def test_script_cleanup_runs_after_action_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    target = tmp_path / "cleanup-target"
    spec = minimal_spec(
        beats=[
            {
                "id": "work",
                "actions": [{"run": (f"touch {shlex.quote(str(target))}\n" "false")}],
            }
        ]
    )
    spec["_hydra_output_dir"] = str(run_dir)
    spec["_keep_hydra_output_dir"] = True
    spec["cleanup"] = [
        {
            "name": "Remove target",
            "run": (f"rm -f {shlex.quote(str(target))}\n" "printf 'cleanup ran\\n'"),
        }
    ]

    result = run_rendered_session(spec)

    assert result.returncode == 1
    assert not target.exists()
    stdout_log = (run_dir / "stdout").read_text(encoding="utf-8")
    assert "::: cleanup cleanup_1 start Remove target\n" in stdout_log
    assert "::: cleanup cleanup_1 end status=0\n" in stdout_log
    assert "cleanup ran\n" in stdout_log
    assert not (run_dir / "cleanup_1.out").exists()
    assert not (run_dir / "cleanup_1.name").exists()
    assert not (run_dir / "cleanup_1.status").exists()
    assert "cleanup ran" not in result.stdout


def test_visible_output_hides_pip_package_summary_but_keeps_action_log(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    spec = minimal_spec(
        beats=[
            {
                "id": "stage-server",
                "actions": [
                    {
                        "display": "simulate package install\n",
                        "run": (
                            "printf 'before\\n'\n"
                            "printf 'Installing collected packages: a, b\\n'\n"
                            "printf 'Successfully installed a-1 b-2\\n'\n"
                            "printf 'after\\n'\n"
                        ),
                    }
                ],
            }
        ]
    )
    spec["_hydra_output_dir"] = str(run_dir)
    spec["_keep_hydra_output_dir"] = True

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "before\n" in result.stdout
    assert "after\n" in result.stdout
    assert "Installing collected packages" not in result.stdout
    assert "Successfully installed" not in result.stdout
    action_log = (run_dir / "stdout").read_text(encoding="utf-8")
    assert "::: action stage_server_1 start beat=stage-server\n" in action_log
    assert "Installing collected packages: a, b\n" in action_log
    assert "Successfully installed a-1 b-2\n" in action_log
    assert not (run_dir / "stage_server_1.out").exists()


def test_stage_markers_use_single_run_logs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    spec = minimal_spec(
        beats=[
            {
                "id": "prepare-cli",
                "actions": [{"run": "printf 'prepared\\n'"}],
            },
            {
                "id": "stage-server",
                "checks": [{"name": "hidden proof", "run": "printf 'checked\\n'"}],
            },
        ]
    )
    spec["_hydra_output_dir"] = str(run_dir)
    spec["_keep_hydra_output_dir"] = True

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    stdout_log = (run_dir / "stdout").read_text(encoding="utf-8")
    stderr_log = (run_dir / "stderr").read_text(encoding="utf-8")
    expected_markers = [
        "::: stage 1/2 start beat=prepare-cli\n",
        "::: stage 1/2 end beat=prepare-cli\n",
        "::: stage 2/2 start beat=stage-server\n",
        "::: stage 2/2 end beat=stage-server\n",
    ]
    for marker in expected_markers:
        assert marker in stdout_log
        assert marker in stderr_log
    assert "prepared\n" in stdout_log
    assert "checked\n" in stdout_log
    assert not list(run_dir.glob("*.out"))
    assert not list(run_dir.glob("*.stderr"))


def test_continuation_display_renders_without_extra_prompt() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "serve",
                "actions": [
                    {
                        "display": "arbiter-server serve \\\n  arbiter.server.bind.host=127.0.0.1\n",
                        "run": "printf 'started\\n'",
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "$ arbiter-server serve \\\n" in result.stdout
    assert "  arbiter.server.bind.host=127.0.0.1\n" in result.stdout
    assert "$ arbiter.server.bind.host=127.0.0.1" not in result.stdout


def test_hidden_check_success_is_not_recorded() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "actions": [{"run": "printf 'visible action\\n'"}],
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'hidden check output\\n'",
                        "expect": {"output_contains": ["hidden check output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "visible action\n" in result.stdout
    assert "hidden check output" not in result.stdout
    assert "hidden check output" not in result.stderr


def test_hidden_check_success_emits_timeline_events(tmp_path: Path) -> None:
    timeline = tmp_path / "recording.timeline.jsonl"
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "actions": [{"run": "printf 'visible action\\n'"}],
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'hidden check output\\n'",
                        "expect": {"output_contains": ["hidden check output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_TIMELINE": str(timeline)})

    assert result.returncode == 0, result.stderr
    events = record.read_timeline_events(timeline)
    check_events = [
        event for event in events if event["phase"] in {"check_start", "check_end"}
    ]
    assert [event["phase"] for event in check_events] == [
        "check_start",
        "check_end",
    ]
    assert {event["beat"] for event in check_events} == {"verify"}
    assert {event["check_id"] for event in check_events} == {"verify_check_1"}
    assert {event["check"] for event in check_events} == {"hidden proof"}
    assert check_events[0]["time"] <= check_events[1]["time"]


def test_visible_timeline_events_include_command_and_hold(tmp_path: Path) -> None:
    timeline = tmp_path / "recording.timeline.jsonl"
    spec = minimal_spec(
        beats=[
            {
                "id": "show",
                "caption": "Show a command.",
                "actions": [{"display": "echo hi\n", "run": "printf 'hi\\n'"}],
                "viewer_hold": 0.0,
            }
        ]
    )
    spec["capture"] = {"baseline_compressed": True}

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_TIMELINE": str(timeline)})

    assert result.returncode == 0, result.stderr
    events = record.read_timeline_events(timeline)
    phases = [event["phase"] for event in events]
    assert phases == [
        "beat_start",
        "caption_start",
        "caption_end",
        "action_start",
        "command_prompt_start",
        "command_prompt_end",
        "command_run_start",
        "command_run_end",
        "action_end",
        "hold_start",
        "hold_end",
        "beat_end",
    ]
    command_events = [
        event for event in events if event["phase"].startswith("command_")
    ]
    assert {event["beat"] for event in command_events} == {"show"}
    assert {event["action_id"] for event in command_events} == {"show_1"}
    assert {event["chunk_index"] for event in command_events} == {0}
    assert command_events[0]["command"] == "echo hi"
    hold_events = [event for event in events if event["phase"].startswith("hold_")]
    assert {event["seconds"] for event in hold_events} == {0.0}


def test_top_level_setup_runs_before_visible_actions_and_is_hidden() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "show",
                "actions": [{"run": 'printf "visible=%s\\n" "$MAIL_LAB_READY"'}],
            }
        ]
    )
    spec["setup"] = [
        {
            "name": "prepare hidden state",
            "run": 'MAIL_LAB_READY=yes; export MAIL_LAB_READY; printf "hidden setup\\n"',
            "expect": {"output_contains": ["hidden setup"]},
        }
    ]

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "visible=yes\n" in result.stdout
    assert "hidden setup" not in result.stdout
    assert "hidden setup" not in result.stderr


def test_top_level_setup_can_load_run_file(tmp_path: Path) -> None:
    setup_file = tmp_path / "setup.sh"
    setup_file.write_text(
        'MAIL_LAB_READY=yes; export MAIL_LAB_READY; printf "hidden setup\\n"\n',
        encoding="utf-8",
    )
    spec = minimal_spec(
        beats=[
            {
                "id": "show",
                "actions": [{"run": 'printf "visible=%s\\n" "$MAIL_LAB_READY"'}],
            }
        ]
    )
    spec["setup"] = [
        {
            "name": "prepare hidden state",
            "run_file": str(setup_file),
            "expect": {"output_contains": ["hidden setup"]},
        }
    ]

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "visible=yes\n" in result.stdout
    assert "hidden setup" not in result.stdout
    assert "hidden setup" not in result.stderr


def test_beat_actions_and_checks_can_load_run_file(tmp_path: Path) -> None:
    action_file = tmp_path / "action.sh"
    action_file.write_text('printf "action file ran\\n"\n', encoding="utf-8")
    check_file = tmp_path / "check.sh"
    check_file.write_text('printf "check file ran\\n"\n', encoding="utf-8")
    spec = minimal_spec(
        beats=[
            {
                "id": "show",
                "actions": [
                    {
                        "display": "run action helper",
                        "run_file": str(action_file),
                    }
                ],
                "checks": [
                    {
                        "name": "check helper",
                        "run_file": str(check_file),
                        "expect": {"output_contains": ["check file ran"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 0, result.stderr
    assert "action file ran" in result.stdout


def test_top_level_setup_rejects_run_and_run_file_together(tmp_path: Path) -> None:
    setup_file = tmp_path / "setup.sh"
    setup_file.write_text("true\n", encoding="utf-8")
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["setup"] = [{"run": "true", "run_file": str(setup_file)}]

    with pytest.raises(record.RecordingError, match="either run or run_file"):
        record.validate_manifest(spec)


def test_top_level_setup_rejects_missing_run_file(tmp_path: Path) -> None:
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["setup"] = [{"run_file": str(tmp_path / "missing.sh")}]

    with pytest.raises(record.RecordingError, match="run_file does not exist"):
        record.validate_manifest(spec)


def test_top_level_setup_emits_timeline_events(tmp_path: Path) -> None:
    timeline = tmp_path / "recording.timeline.jsonl"
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["setup"] = [{"name": "prepare hidden state", "run": "true"}]

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_TIMELINE": str(timeline)})

    assert result.returncode == 0, result.stderr
    events = record.read_timeline_events(timeline)
    setup_events = [
        event for event in events if event.get("check_id") == "setup_check_1"
    ]
    assert [event["phase"] for event in setup_events] == ["check_start", "check_end"]
    assert {event["beat"] for event in setup_events} == {"__setup__"}
    assert {event["check"] for event in setup_events} == {"prepare hidden state"}


def test_top_level_setup_failure_writes_failure_report(tmp_path: Path) -> None:
    failure = tmp_path / "recording.failure.json"
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["setup"] = [
        {
            "name": "prepare hidden state",
            "run": (
                "recording_setup_main() { "
                "printf 'setup stderr\\n' >&2; "
                "return 1; "
                "}; "
                "recording_setup_main"
            ),
        }
    ]

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_FAILURE": str(failure)})

    assert result.returncode == 1
    report = json.loads(failure.read_text(encoding="utf-8"))
    assert report["kind"] == "check"
    assert report["id"] == "setup_check_1"
    assert report["name"] == "prepare hidden state"
    assert report["message"] == "exited 1, expected 0"
    assert (
        "::: check setup_check_1 start beat=__setup__ name=prepare hidden state\n"
        in report["stderr"]
    )
    assert "setup stderr\n" in report["stderr"]
    assert report["output_path"].endswith("/stdout")
    assert report["stderr_path"].endswith("/stderr")


def test_visible_action_failure_reports_exit_before_output_gate() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "stage-server",
                "actions": [
                    {
                        "run": "printf 'container name already owned\\n'; false",
                        "expect": {"output_contains": ["URL: http://127.0.0.1:18075"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 1
    assert "recording gate failed: stage_server_1 exited 1, expected 0" in result.stderr
    assert "missing text: URL: http://127.0.0.1:18075" not in result.stderr


def test_hidden_check_failure_reports_exit_before_output_gate() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'hidden stdout\\n'; false",
                        "expect": {"output_contains": ["expected hidden output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 1
    assert "recording check failed: hidden proof exited 1, expected 0" in result.stderr
    assert "missing text: expected hidden output" not in result.stderr


def test_hidden_check_failure_requires_review_without_dumping_stdout() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "actions": [{"run": "printf 'visible action\\n'"}],
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'actual hidden output\\n'",
                        "expect": {"output_contains": ["expected hidden output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 1
    assert "visible action\n" in result.stdout
    assert (
        "recording check failed: hidden proof missing text: expected hidden output"
        in result.stderr
    )
    assert "actual hidden output" not in result.stderr


def test_hidden_check_failure_shows_stderr_only() -> None:
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": (
                            "printf 'hidden stdout\\n'; "
                            "printf 'hidden stderr\\n' >&2"
                        ),
                        "expect": {"output_contains": ["expected hidden output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec)

    assert result.returncode == 1
    assert "--- stderr ---" in result.stderr
    assert "hidden stderr" in result.stderr
    assert "hidden stdout" not in result.stderr
    assert "hidden stdout" not in result.stdout


def test_hidden_check_failure_writes_failure_report(tmp_path: Path) -> None:
    failure = tmp_path / "recording.failure.json"
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'actual hidden output\\n'",
                        "expect": {"output_contains": ["expected hidden output"]},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_FAILURE": str(failure)})

    assert result.returncode == 1
    report = json.loads(failure.read_text(encoding="utf-8"))
    assert report["kind"] == "check"
    assert report["id"] == "verify_check_1"
    assert report["name"] == "hidden proof"
    assert report["message"] == "missing text: expected hidden output"
    assert (
        "::: check verify_check_1 start beat=verify name=hidden proof\n"
        in report["output"]
    )
    assert "actual hidden output\n" in report["output"]
    assert report["output_path"].endswith("/stdout")
    assert report["stderr_path"].endswith("/stderr")
    assert report["postmortem_path"].endswith("/enter")


def test_hidden_check_exit_writes_failure_report(tmp_path: Path) -> None:
    failure = tmp_path / "recording.failure.json"
    spec = minimal_spec(
        beats=[
            {
                "id": "verify",
                "checks": [
                    {
                        "name": "hidden proof",
                        "run": "printf 'before exit\\n'; exit 7",
                        "expect": {"exit_code": 0},
                    }
                ],
            }
        ]
    )

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_FAILURE": str(failure)})

    assert result.returncode == 1
    report = json.loads(failure.read_text(encoding="utf-8"))
    assert report["kind"] == "check"
    assert report["id"] == "verify_check_1"
    assert report["name"] == "hidden proof"
    assert report["message"] == "exited 7, expected 0"
    assert (
        "::: check verify_check_1 start beat=verify name=hidden proof\n"
        in report["output"]
    )
    assert "before exit\n" in report["output"]
    assert report["output_path"].endswith("/stdout")
    assert report["stderr_path"].endswith("/stderr")


def test_record_reports_session_failure_sidecar(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])
    run_dir = tmp_path / "runs" / "test-recording" / "20260616-160412"
    spec["_hydra_output_dir"] = str(run_dir)

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")
    output_path = tmp_path / "hidden.out"
    stderr_path = tmp_path / "hidden.stderr"
    postmortem_path = tmp_path / "enter"
    output_path.write_text("real reason\n", encoding="utf-8")
    stderr_path.write_text("stderr reason\n", encoding="utf-8")
    postmortem_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        write_recorded_header(Path(command[-1]))
        record.failure_path_for_cast(Path(command[-1])).write_text(
            json.dumps(
                {
                    "kind": "check",
                    "id": "setup_check_1",
                    "name": "prepare hidden state",
                    "message": "exited 1, expected 0",
                    "output": "real reason\n",
                    "output_path": str(output_path),
                    "stderr": "stderr reason\n",
                    "stderr_path": str(stderr_path),
                    "run_dir": str(tmp_path),
                    "postmortem_path": str(postmortem_path),
                    "recording_id": "install-and-bootstrap",
                    "run_id": "20260616-160412",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(record.subprocess, "run", fake_run)

    try:
        record.record(spec, dry_run=False, check_only=False, output_override=str(cast))
    except record.RecordingError as exc:
        message = str(exc)
    else:
        raise AssertionError("recording failure should raise RecordingError")

    assert "asciinema recording failed with exit code 1" in message
    assert (
        "session failed during check 'prepare hidden state' (setup_check_1)" in message
    )
    assert "reason: exited 1, expected 0" in message
    assert "stderr reason" in message
    assert "real reason" not in message
    assert "captured output:" not in message
    assert "view with: less" not in message
    assert "run_id: 20260616-160412" in message
    assert (run_dir / "failed.cast").exists()
    assert (run_dir / "failed.timeline.jsonl").exists()
    assert (
        "media/tools/record.py recording=install-and-bootstrap "
        "action=inspect run_id=20260616-160412" in message
    )
    assert "media/tools/record.py action=play run_id=20260616-160412" in message
    assert (
        "media/tools/record.py recording=install-and-bootstrap "
        "action=output run_id=20260616-160412" in message
    )


def test_recording_failure_formatter_colorizes_status_lines(tmp_path: Path) -> None:
    failure = tmp_path / "recording.failure.json"
    failure.write_text(
        json.dumps(
            {
                "kind": "action",
                "id": "stage_server_1",
                "name": "stage_server_1",
                "message": "exited 1, expected 0",
                "recording_id": "install-and-bootstrap",
                "run_id": "20260619-052228",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    message = record.format_recording_failure(
        returncode=1,
        command=["asciinema", "record"],
        cast_path=tmp_path / "recording.cast",
        timeline_path=tmp_path / "recording.timeline.jsonl",
        failure_path=failure,
        color=True,
    )

    assert "\033[31;1masciinema recording failed with exit code 1\033[0m" in message
    assert "\033[31;1mreason: exited 1, expected 0\033[0m" in message
    assert "\033[36;1mrun_id: 20260619-052228\033[0m" in message
    assert "reason: exited 1, expected 0" in message


def test_check_intervals_are_removed_without_compressing_runtime(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "recording.cast"
    write_cast(
        cast,
        [
            (0.0, "$ echo before\r\n"),
            (1.0, "before\r\n"),
            # A hidden check happens from t=1.5s to t=3.5s. The following
            # visible command should shift earlier by 2s, but its own 10s
            # observed runtime must remain 10s.
            (3.0, "$ sleep 10\r\n"),
            (10.0, "done\r\n"),
        ],
    )

    record.strip_cast_intervals(cast, [(1.5, 3.5)])

    assert read_cast_event_times(cast) == [0.0, 1.0, 2.0, 12.0]


def test_leading_check_interval_can_overlap_first_visible_output(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "recording.cast"
    write_cast(
        cast,
        [
            # Asciinema can start the visible cast clock after the recorder's
            # wall-clock timeline, so a long setup check may appear to contain
            # the first visible frame. Clip that leading setup interval rather
            # than treating the first caption as leaked hidden output.
            (1.2, "# first visible caption\r\n"),
            (0.1, "$ echo after setup\r\n"),
        ],
    )

    record.strip_cast_intervals(cast, [(0.1, 2.0)])

    assert read_cast_event_times(cast) == [0.1, 0.2]


def test_short_check_interval_with_visible_output_is_treated_as_clock_jitter(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "recording.cast"
    write_cast(
        cast,
        [
            (0.0, "$ echo before\r\n"),
            (2.0, "briefly overlaps tiny hidden poll\r\n"),
            (2.0, "$ echo after\r\n"),
        ],
    )

    record.strip_cast_intervals(cast, [(1.95, 2.05)])

    assert read_cast_event_times(cast) == [0.0, 2.0, 4.0]


def test_check_interval_with_visible_output_requires_review(tmp_path: Path) -> None:
    cast = tmp_path / "recording.cast"
    write_cast(
        cast,
        [
            (0.0, "$ echo before\r\n"),
            (2.0, "background output\r\n"),
            (2.0, "$ echo after\r\n"),
        ],
    )

    try:
        record.strip_cast_intervals(cast, [(1.5, 3.5)])
    except record.RecordingError as exc:
        assert "visible cast output overlaps hidden check interval" in str(exc)
    else:
        raise AssertionError("hidden check interval with visible output should fail")


def test_timeline_check_intervals_are_paired_and_merged() -> None:
    intervals = record.check_intervals_from_timeline(
        [
            {"phase": "check_start", "check_id": "a", "time": 1.0},
            {"phase": "check_start", "check_id": "b", "time": 1.5},
            {"phase": "check_end", "check_id": "a", "time": 2.0},
            {"phase": "check_end", "check_id": "b", "time": 3.0},
        ]
    )

    assert intervals == [(1.0, 3.0)]
