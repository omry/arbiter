from __future__ import annotations

import importlib.util
import io
import sys
import time
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_tool_progress() -> Any:
    tools_dir = REPO_ROOT / "media" / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    path = tools_dir / "tool_progress.py"
    spec = importlib.util.spec_from_file_location("tool_progress", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool_progress"] = module
    spec.loader.exec_module(module)
    return module


tool_progress = load_tool_progress()


def write_event_when_reader_is_ready(pipe_path: Path, event: dict[str, Any]) -> None:
    deadline = time.monotonic() + 1
    while True:
        if tool_progress.write_progress_event(event, pipe_path=pipe_path):
            return
        if time.monotonic() > deadline:
            raise AssertionError("progress pipe reader did not become ready")
        time.sleep(0.01)


@pytest.mark.skipif(
    not tool_progress.progress_pipes_supported(),
    reason="progress pipes require os.mkfifo support",
)
def test_progress_pipe_reporter_reads_json_events(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    pipe_path = tmp_path / "progress.pipe"
    reporter = tool_progress.ProgressPipeReporter(
        pipe_path,
        enabled=True,
        on_event=events.append,
    )
    reporter.start()
    try:
        write_event_when_reader_is_ready(
            pipe_path,
            {"message": "hello", "phase": "status", "status": "info", "tool": "demo"},
        )
        deadline = time.monotonic() + 1
        while not events:
            if time.monotonic() > deadline:
                raise AssertionError("progress pipe event was not delivered")
            time.sleep(0.01)
    finally:
        reporter.stop()

    assert events == [
        {"message": "hello", "phase": "status", "status": "info", "tool": "demo"}
    ]


@pytest.mark.skipif(
    not tool_progress.progress_pipes_supported(),
    reason="progress pipes require os.mkfifo support",
)
def test_progress_pipe_reporter_keeps_pipe_ready_for_burst_writes(
    tmp_path: Path,
) -> None:
    events: list[dict[str, Any]] = []
    pipe_path = tmp_path / "progress.pipe"
    reporter = tool_progress.ProgressPipeReporter(
        pipe_path,
        enabled=True,
        on_event=events.append,
    )
    reporter.start()
    try:
        for index in range(5):
            assert tool_progress.write_progress_event(
                {
                    "index": index,
                    "message": f"event {index}",
                    "phase": "status",
                    "status": "info",
                    "tool": "demo",
                },
                pipe_path=pipe_path,
            )
        deadline = time.monotonic() + 1
        while len(events) < 5:
            if time.monotonic() > deadline:
                raise AssertionError("progress pipe burst events were not delivered")
            time.sleep(0.01)
    finally:
        reporter.stop()

    assert [event["index"] for event in events] == [0, 1, 2, 3, 4]


def test_tool_progress_writes_status_event_when_pipe_is_configured(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    events: list[dict[str, Any]] = []

    def fake_write_progress_event(event: dict[str, Any]) -> bool:
        events.append(event)
        return True

    monkeypatch.setattr(
        tool_progress,
        "write_progress_event",
        fake_write_progress_event,
    )
    progress = tool_progress.ToolProgress("audio")
    progress.status("pass", "audio ready", color="\033[32;1m")

    assert capsys.readouterr().out == ""
    assert events == [
        {
            "message": "audio ready",
            "phase": "status",
            "status": "pass",
            "tool": "audio",
        }
    ]


def test_tool_progress_prints_status_when_no_pipe(capsys: Any) -> None:
    progress = tool_progress.ToolProgress("audio")

    progress.status("pass", "audio ready", color="\033[32;1m", enabled=False)

    assert capsys.readouterr().out == "pass  audio ready\n"


def test_progress_bar_renderer_shows_bar_and_current_step() -> None:
    stream = io.StringIO()
    renderer = tool_progress.ProgressBarRenderer(
        stream=stream,
        width=10,
        interactive=True,
        enabled=False,
    )

    renderer.emit(
        {
            "current": 2,
            "message": "Create staging",
            "phase": "status",
            "status": "step",
            "total": 5,
        }
    )

    output = stream.getvalue()
    assert "progress [████░░░░░░] 2/5" in output
    assert "current Create staging" in output


def test_progress_bar_renderer_truncates_current_step_to_one_line() -> None:
    stream = io.StringIO()
    renderer = tool_progress.ProgressBarRenderer(
        stream=stream,
        width=10,
        columns=30,
        interactive=True,
        enabled=False,
    )

    renderer.emit(
        {
            "current": 1,
            "message": "This is a very long command that should not wrap",
            "phase": "status",
            "status": "cmd",
            "total": 2,
        }
    )

    output_lines = [
        line.removeprefix("\r\033[2K")
        for line in stream.getvalue().split("\n")
        if line
    ]
    assert len(output_lines) == 2
    assert output_lines[1] == "current This is a very long..."
    assert len(output_lines[0]) <= 30
    assert len(output_lines[1]) <= 30


def test_progress_bar_renderer_can_replay_summary_on_finish() -> None:
    stream = io.StringIO()
    renderer = tool_progress.ProgressBarRenderer(
        stream=stream,
        width=10,
        columns=40,
        interactive=True,
        enabled=False,
    )

    renderer.emit(
        {
            "current": 1,
            "message": "audio segments ready: 15",
            "phase": "status",
            "status": "pass",
            "total": 2,
        }
    )
    renderer.emit(
        {
            "current": 2,
            "message": "OpenAI TTS estimated cost this run: $0.00",
            "phase": "status",
            "status": "info",
            "total": 2,
        }
    )
    renderer.finish(replay=True)

    output = stream.getvalue()
    assert "\x1b[2F\x1b[2M" in output
    assert "pass  audio segments ready: 15\n" in output
    assert "info  OpenAI TTS estimated cost this run: $0.00\n" in output


def test_progress_bar_renderer_does_not_draw_bar_for_non_tty_stream() -> None:
    stream = io.StringIO()
    renderer = tool_progress.ProgressBarRenderer(
        stream=stream,
        width=10,
        enabled=False,
    )

    renderer.emit(
        {
            "current": 1,
            "message": "audio segments ready: 15",
            "phase": "status",
            "status": "pass",
            "total": 1,
        }
    )
    renderer.finish(replay=True)

    assert stream.getvalue() == "pass  audio segments ready: 15\n"
