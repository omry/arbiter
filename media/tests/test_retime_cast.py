from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_retime_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "retime_cast.py"
    spec = importlib.util.spec_from_file_location("retime_cast", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["retime_cast"] = module
    spec.loader.exec_module(module)
    return module


retime_cast = load_retime_tool()


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


def write_timeline(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def read_output_events(path: Path) -> list[tuple[float, str]]:
    absolute = 0.0
    events: list[tuple[float, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        delay, event_type, payload = json.loads(line)
        absolute += float(delay)
        if event_type == "o":
            events.append((round(absolute, 3), payload))
    return events


def test_retime_synthesizes_command_typing_and_pauses(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Show a command.\r\n\r\n"),
            (0.2, "$ hi\r\n"),
            (0.19, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {"phase": "command_prompt_start", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.25},
            {"phase": "command_prompt_end", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.35},
            {"phase": "command_run_start", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.36},
            {"phase": "command_run_end", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.5},
            {"phase": "hold_start", "beat": "show", "seconds": 1.5, "time": 0.51},
            {"phase": "hold_end", "beat": "show", "seconds": 1.5, "time": 0.51},
            {"phase": "beat_end", "beat": "show", "time": 0.51},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.1,
            typing_space_delay=0.1,
            typing_punctuation_delay=0.1,
            typing_newline_delay=0.0,
            post_enter_pause=0.4,
            post_command_pause=0.6,
        ),
    )

    events = read_output_events(output)
    prompt_tokens = [payload for _time, payload in events if payload in {"$", " ", "h", "i"}]
    assert prompt_tokens == ["$", " ", "h", "i"]
    prompt_times = [time for time, payload in events if payload in {"$", " ", "h", "i"}]
    assert prompt_times == [0.3, 0.4, 0.5, 0.6]
    ok_time = next(time for time, payload in events if payload == "ok\r\n")
    assert ok_time >= 0.95
    assert events[-1][0] >= 3.0
    assert "".join(payload for _time, payload in events) == (
        "# Show a command.\r\n\r\n$ hi\r\nok\r\n"
    )


def test_retime_matches_prompt_near_timeline_without_absorbing_output(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Caption.\r\n\r\n"),
            (0.15, "$ hi"),
            (0.0, "\r\n"),
            (0.35, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "command_prompt_start", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.5},
            {"phase": "command_prompt_end", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.52},
            {"phase": "command_run_start", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.53},
            {"phase": "command_run_end", "beat": "show", "action_id": "show_1", "chunk_index": 0, "time": 0.54},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.1,
            typing_space_delay=0.1,
            typing_punctuation_delay=0.1,
            typing_newline_delay=0.0,
            post_enter_pause=0.4,
            post_command_pause=0.6,
        ),
    )

    events = read_output_events(output)
    prompt_tokens = [payload for _time, payload in events if payload in {"$", " ", "h", "i"}]
    assert prompt_tokens == ["$", " ", "h", "i"]
    assert "".join(payload for _time, payload in events) == "# Caption.\r\n\r\n$ hi\r\nok\r\n"


def test_retime_matches_split_multiline_prompt_by_command(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Caption.\r\n\r\n"),
            (0.2, "$ first\r\n"),
            (0.0, "$ second"),
            (0.0, "\r\n"),
            (0.3, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": "fallback",
                "command": "first\nsecond\n",
                "time": 0.3,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": "fallback",
                "command": "first\nsecond\n",
                "time": 0.32,
            },
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.1,
            typing_space_delay=0.1,
            typing_punctuation_delay=0.1,
            typing_newline_delay=0.0,
            post_enter_pause=0.4,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    prompt_tokens = [payload for _time, payload in events if payload == "$"]
    assert prompt_tokens == ["$", "$"]
    assert "".join(payload for _time, payload in events) == (
        "# Caption.\r\n\r\n$ first\r\n$ second\r\nok\r\n"
    )


def test_retime_does_not_reuse_nearby_prompt_for_different_command(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Caption.\r\n\r\n"),
            (0.2, "$ first\r\n"),
            (0.1, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "missing-command",
                "time": 0.32,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "missing-command",
                "time": 0.34,
            },
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.1,
            typing_space_delay=0.1,
            typing_punctuation_delay=0.1,
            typing_newline_delay=0.0,
            post_enter_pause=0.4,
            post_command_pause=0.0,
        ),
    )

    assert "".join(payload for _time, payload in read_output_events(output)) == (
        "# Caption.\r\n\r\n$ first\r\nok\r\n"
    )


def test_retime_matches_each_prompt_by_command_when_prompts_are_close(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "$ first\r\n"),
            (0.05, "one\r\n"),
            (0.05, "$ second\r\n"),
            (0.05, "two\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.1,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.11,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.12,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.16,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "time": 0.26,
            },
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.01,
            typing_space_delay=0.01,
            typing_punctuation_delay=0.01,
            typing_newline_delay=0.0,
            post_enter_pause=0.1,
            post_command_pause=0.1,
        ),
    )

    text = "".join(payload for _time, payload in read_output_events(output))

    assert text == "$ first\r\none\r\n$ second\r\ntwo\r\n"
    assert text.count("$ first") == 1
    assert text.count("$ second") == 1
    assert text.index("$ first") < text.index("one")
    assert text.index("one") < text.index("$ second")
    assert text.index("$ second") < text.index("two")


def test_retime_extends_beat_to_audio_duration(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Intro.\r\n"),
            (0.39, "ready\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "intro", "time": 0.0},
            {"phase": "beat_end", "beat": "intro", "time": 0.5},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.0,
            typing_space_delay=0.0,
            typing_punctuation_delay=0.0,
            typing_newline_delay=0.0,
            post_enter_pause=0.0,
            post_command_pause=0.0,
        ),
        audio_durations={"intro": 2.0},
    )

    events = read_output_events(output)
    assert events[-1] == (2.0, "")
    assert "".join(payload for _time, payload in events) == "# Intro.\r\nready\r\n"


def test_retime_audio_duration_counts_existing_viewer_hold(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(cast, [(0.49, "ready\r\n")])
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "intro", "time": 0.0},
            {"phase": "hold_start", "beat": "intro", "seconds": 1.5, "time": 0.5},
            {"phase": "hold_end", "beat": "intro", "seconds": 1.5, "time": 0.5},
            {"phase": "beat_end", "beat": "intro", "time": 0.5},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(
            typing_char_delay=0.0,
            typing_space_delay=0.0,
            typing_punctuation_delay=0.0,
            typing_newline_delay=0.0,
            post_enter_pause=0.0,
            post_command_pause=0.0,
        ),
        audio_durations={"intro": 1.0},
    )

    assert read_output_events(output)[-1] == (2.0, "")


def test_read_audio_segment_durations(tmp_path: Path) -> None:
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {"id": "intro", "duration": 1.25},
                    {"id": "setup", "duration": 0},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert retime_cast.read_audio_segment_durations(metadata) == {
        "intro": 1.25,
        "setup": 0.0,
    }


def test_retime_uses_manifest_paths_and_rules(tmp_path: Path, monkeypatch: Any) -> None:
    cast = tmp_path / "demo.cast"
    timeline = tmp_path / "demo.timeline.jsonl"
    output = tmp_path / "demo.presentation.cast"
    write_cast(cast, [(0.1, "$ x\r\n")])
    write_timeline(
        timeline,
        [
            {"phase": "command_prompt_start", "beat": "demo", "action_id": "demo_1", "chunk_index": 0, "time": 0.05},
            {"phase": "command_prompt_end", "beat": "demo", "action_id": "demo_1", "chunk_index": 0, "time": 0.15},
        ],
    )
    monkeypatch.setattr(
        retime_cast,
        "container_from_hydra_cfg",
        lambda cfg: {"action": "check"},
    )
    monkeypatch.setattr(
        retime_cast,
        "load_recording_spec_from_hydra_cfg",
        lambda cfg: {
            "_recording_id": "demo",
            "id": "demo",
            "title": "Demo",
            "outputs": {
                "cast": str(cast),
                "retimed_cast": str(output),
            },
            "retime": {"post_enter_pause": 0.2},
            "beats": [{"id": "demo", "actions": [{"run": "true"}]}],
        },
    )

    result = retime_cast.run_tool_from_hydra_cfg(object())

    assert result == 0
    assert retime_cast.timeline_path_for_cast(cast) == timeline
