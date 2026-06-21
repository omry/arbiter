from __future__ import annotations

import importlib.util
import json
import os
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
    assert header["command"] == "media/tools/record.py --session test-recording"
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


def test_top_level_setup_emits_timeline_events(tmp_path: Path) -> None:
    timeline = tmp_path / "recording.timeline.jsonl"
    spec = minimal_spec(beats=[{"id": "show", "actions": [{"run": "true"}]}])
    spec["setup"] = [{"name": "prepare hidden state", "run": "true"}]

    result = run_rendered_session(spec, env={"ARBITER_CINEMA_TIMELINE": str(timeline)})

    assert result.returncode == 0, result.stderr
    events = record.read_timeline_events(timeline)
    setup_events = [
        event
        for event in events
        if event.get("check_id") == "setup_check_1"
    ]
    assert [event["phase"] for event in setup_events] == ["check_start", "check_end"]
    assert {event["beat"] for event in setup_events} == {"__setup__"}
    assert {event["check"] for event in setup_events} == {"prepare hidden state"}


def test_hidden_check_failure_requires_review_and_shows_output() -> None:
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
    assert "actual hidden output" in result.stderr


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

    result = run_rendered_session(
        spec, env={"ARBITER_CINEMA_FAILURE": str(failure)}
    )

    assert result.returncode == 1
    report = json.loads(failure.read_text(encoding="utf-8"))
    assert report["kind"] == "check"
    assert report["id"] == "verify_check_1"
    assert report["name"] == "hidden proof"
    assert report["message"] == "missing text: expected hidden output"
    assert report["output"] == "actual hidden output\n"


def test_record_reports_session_failure_sidecar(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cast = tmp_path / "recording.cast"
    spec = minimal_spec(beats=[{"id": "one", "actions": [{"run": "true"}]}])

    monkeypatch.setattr(record, "check_asciinema", lambda: "asciinema 3.2.0")

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
    assert "session failed during check 'prepare hidden state' (setup_check_1)" in message
    assert "reason: exited 1, expected 0" in message
    assert "real reason" in message


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
