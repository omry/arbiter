from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


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


def joined_output(events: list[tuple[float, str]]) -> str:
    return "".join(payload for _time, payload in events)


def time_when_plain_contains(events: list[tuple[float, str]], needle: str) -> float:
    text = ""
    for time, payload in events:
        text += retime_cast.plain_terminal_text(payload)
        if needle in text:
            return time
    raise AssertionError(f"output never contained {needle!r}")


def payload_time_after(
    events: list[tuple[float, str]], payload: str, after_time: float
) -> float:
    for time, event_payload in events:
        if time > after_time and event_payload == payload:
            return time
    raise AssertionError(f"payload {payload!r} not found after {after_time}")


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
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.25,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.35,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.36,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.5,
            },
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
    prompt_done = time_when_plain_contains(events, "$ hi")
    assert prompt_done >= time_when_plain_contains(events, "# Show a command.")
    ok_time = next(time for time, payload in events if payload == "ok\r\n")
    assert ok_time >= prompt_done + 0.4
    assert events[-1][0] >= 3.0
    assert joined_output(events) == ("# Show a command.\r\n\r\n$ hi\r\nok\r\n")


def test_retime_command_can_override_post_enter_pause(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Run command.\r\n\r\n"),
            (0.1, "$ hi\r\n"),
            (0.1, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "post_enter_pause": 0.7,
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "post_enter_pause": 0.7,
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "display": "hi",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.31},
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
            post_enter_pause=0.1,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    done_time = next(time for time, payload in events if payload == "done\r\n")
    assert done_time == 0.9


def test_retime_command_can_override_post_command_pause(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Run commands.\r\n\r\n"),
            (0.1, "$ first\r\n"),
            (0.1, "done\r\n"),
            (0.1, "$ second\r\n"),
            (0.1, "again\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "post_command_pause": 2.0,
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "post_command_pause": 2.0,
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.35,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.36,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.37,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "status": 0,
                "time": 0.45,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.46},
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
            post_command_pause=0.1,
        ),
    )

    events = read_output_events(output)
    done_time = time_when_plain_contains(events, "done")
    second_time = time_when_plain_contains(events, "$ second")
    assert second_time >= done_time + 2.0


def test_retime_command_can_override_pre_command_pause(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Run commands.\r\n\r\n"),
            (0.1, "$ first\r\n"),
            (0.1, "done\r\n"),
            (0.1, "$ second\r\n"),
            (0.1, "again\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "pre_command_pause": 2.0,
                "time": 0.35,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "pre_command_pause": 2.0,
                "time": 0.36,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.37,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "status": 0,
                "time": 0.45,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.46},
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
            post_command_pause=0.1,
        ),
    )

    events = read_output_events(output)
    done_time = time_when_plain_contains(events, "done")
    second_prompt_time = next(
        time
        for time, payload in events
        if time > done_time and retime_cast.plain_terminal_text(payload) == "$"
    )
    second_time = time_when_plain_contains(events, "$ second")
    assert second_prompt_time < done_time + 1.0
    assert second_time >= second_prompt_time + 2.0


def test_retime_clear_output_belongs_to_clear_command_when_prompt_is_erased(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    clear_screen = "\033[H\033[2J\033[3J"
    write_cast(
        cast,
        [
            (0.1, "# Run commands.\r\n\r\n"),
            (0.1, "$ build\r\n"),
            (0.1, "done\r\n"),
            (0.1, clear_screen),
            (0.1, "# Next section.\r\n\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "build",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "build",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "build",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "clear",
                "pre_command_pause": 2.0,
                "show_prompt_after": False,
                "time": 0.35,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "clear",
                "pre_command_pause": 2.0,
                "show_prompt_after": False,
                "time": 0.36,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "clear",
                "time": 0.37,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "status": 0,
                "time": 0.45,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.46},
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
    )

    events = read_output_events(output)
    done_time = time_when_plain_contains(events, "done")
    clear_time = time_when_plain_contains(events, "$ clear")
    payloads = [payload for _time, payload in events]
    combined = "".join(payloads)
    assert clear_time >= done_time + 2.0
    assert combined.index("$ clear\r\n") < combined.index(clear_screen)


def test_retime_command_can_pause_before_enter(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Run command.\r\n\r\n"),
            (0.1, "$ clear\r\n"),
            (0.1, "\033[H\033[2J\033[3J"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "pre_enter_pause": 0.75,
                "post_enter_pause": 0.0,
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "pre_enter_pause": 0.75,
                "post_enter_pause": 0.0,
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "display": "clear",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.31},
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
    )

    events = read_output_events(output)
    clear_time = time_when_plain_contains(events, "$ clear")
    enter_time = payload_time_after(events, "\r", clear_time)
    clear_screen_time = next(time for time, payload in events if "\033[2J" in payload)
    assert enter_time == clear_time + 0.75
    assert clear_screen_time == enter_time


def test_retime_command_can_suppress_trailing_prompt(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install Reploy.\r\n\r\n"),
            (0.1, "$ clear\r\n"),
            (0.1, "\033[H\033[2J"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "clear",
                "prompt": "$",
                "show_prompt_after": False,
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "clear",
                "prompt": "$",
                "show_prompt_after": False,
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "clear",
                "display": "clear",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "status": "0",
                "time": 0.3,
            },
            {"phase": "hold_start", "beat": "install", "seconds": "1.0", "time": 0.31},
            {"phase": "hold_end", "beat": "install", "seconds": "1.0", "time": 1.31},
            {"phase": "beat_end", "beat": "install", "time": 1.32},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(post_enter_pause=0.0),
    )

    payloads = [payload for _time, payload in read_output_events(output)]
    assert payloads.count("$") == 1
    combined = "".join(payloads)
    assert "\033[H\033[2J" in combined
    assert combined.index("$ clear\r\n") < combined.index("\033[H\033[2J")
    assert "\033[H\033[2J\r\n" not in combined
    assert payloads[-1] != "$"


def test_retime_keeps_ansi_clear_output_with_clear_command(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    clear_screen = "\033[H\033[2J\033[3J"
    write_cast(
        cast,
        [
            (0.1, "# Create staging.\r\n\r\n"),
            (0.1, "$ clear\r\n"),
            (0.05, clear_screen),
            (0.1, "$ next\r\n"),
            (0.05, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "clear",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "next",
                "time": 0.35,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "next",
                "time": 0.36,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "command": "next",
                "time": 0.37,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 1,
                "status": 0,
                "time": 0.45,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.46},
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
    )

    payloads = [payload for _time, payload in read_output_events(output)]
    combined = "".join(payloads)
    assert combined.index("$ clear\r\n") < combined.index(clear_screen)
    assert f"{clear_screen}\r\n" not in combined
    clear_index = payloads.index(clear_screen)
    next_prompt_index = next(
        index
        for index, payload in enumerate(payloads)
        if payload == "n" and index > clear_index
    )
    done_index = payloads.index("done\r\n")
    assert clear_index < next_prompt_index < done_index


def test_retime_keeps_split_caption_newline_before_prompt(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\r\n\033[36;1m# Split caption.\033[0m"),
            (0.0, "\r\n\r\n"),
            (0.2, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "caption_start",
                "beat": "show",
                "caption": "Split caption.",
                "time": 0.1,
            },
            {
                "phase": "caption_end",
                "beat": "show",
                "caption": "Split caption.",
                "time": 0.1,
            },
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "display": "hi",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "status": 0,
                "time": 0.3,
            },
            {"phase": "beat_end", "beat": "show", "time": 0.31},
        ],
    )

    retime_cast.retime_cast(
        cast_path=cast,
        timeline_path=timeline,
        output_path=output,
        rules=retime_cast.TimingRules(post_enter_pause=0.0),
    )

    plain = retime_cast.plain_terminal_text(
        "".join(payload for _time, payload in read_output_events(output))
    )

    assert "# Split caption.\n\n$ hi" in plain


def test_retime_synthesizes_command_from_timeline_without_captured_prompt(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Show a command.\r\n\r\n"),
            (0.2, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "prompt": "$",
                "time": 0.25,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "prompt": "$",
                "time": 0.26,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.27,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.3,
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
    assert time_when_plain_contains(events, "$ hi") >= time_when_plain_contains(
        events, "# Show a command."
    )
    assert joined_output(events) == ("# Show a command.\r\n\r\n$ hi\r\nok\r\n$")


def test_retime_displays_command_before_its_captured_output(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install.\r\n\r\n"),
            (0.02, "installed\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {"phase": "caption_start", "beat": "install", "time": 0.1},
            {"phase": "caption_end", "beat": "install", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "installer",
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "installer",
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "time": 0.3,
            },
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
            post_enter_pause=0.25,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    prompt_time = next(time for time, payload in events if payload == "$")
    output_time = next(time for time, payload in events if payload == "installed\r\n")

    assert prompt_time < output_time
    assert output_time >= prompt_time + 0.25


def test_retime_multiline_display_command_completes_before_output(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Stage.\r\n\r\n"),
            (0.01, "created\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "stage", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "stage",
                "action_id": "stage_1",
                "chunk_index": "fallback",
                "command": "reploy stage arbiter-server\ncd reploy-staging\n",
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "stage",
                "action_id": "stage_1",
                "chunk_index": "fallback",
                "command": "reploy stage arbiter-server\ncd reploy-staging\n",
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "stage",
                "action_id": "stage_1",
                "chunk_index": "fallback",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "stage",
                "action_id": "stage_1",
                "chunk_index": "fallback",
                "time": 0.3,
            },
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
            post_enter_pause=0.25,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    joined = "".join(payload for _time, payload in events)
    first_line_index = joined.index("$ reploy stage arbiter-server\r\n")
    second_line_index = joined.index("$ cd reploy-staging\r\n")
    output_index = joined.index("created\r\n")

    assert first_line_index < second_line_index < output_index


def test_retime_does_not_leak_hidden_run_command_into_visible_prompt(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install.\r\n\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl -fsSL https://reploy.yadan.net/install.sh",
                "prompt": "$",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl -fsSL https://reploy.yadan.net/install.sh",
                "prompt": "$",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": ("curl -fsSL https://reploy.yadan.net/install.sh | sh"),
                "output_mode": "suppress",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "time": 0.3,
            },
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
            post_enter_pause=0.1,
            post_command_pause=0.0,
        ),
    )

    visible = "".join(payload for _time, payload in read_output_events(output))
    assert "$ curl -fsSL https://reploy.yadan.net/install.sh\r\n" in visible
    assert "sh -s -- --version" not in visible


def test_retime_puts_next_prompt_on_new_line_after_output_without_newline(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Inspect files.\r\n\r\n"),
            (0.1, "$ first\r\n"),
            (0.1, "value-without-newline"),
            (0.1, "$ second\r\n"),
            (0.1, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "inspect", "time": 0.0},
            {
                "phase": "caption_start",
                "beat": "inspect",
                "caption": "Inspect files.",
                "time": 0.1,
            },
            {
                "phase": "caption_end",
                "beat": "inspect",
                "caption": "Inspect files.",
                "time": 0.1,
            },
            {
                "phase": "command_prompt_start",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 0,
                "command": "first",
                "time": 0.2,
            },
            {
                "phase": "command_run_start",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 0,
                "time": 0.21,
            },
            {
                "phase": "command_run_end",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 0,
                "time": 0.31,
            },
            {
                "phase": "command_prompt_start",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.4,
            },
            {
                "phase": "command_prompt_end",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 1,
                "command": "second",
                "time": 0.4,
            },
            {
                "phase": "command_run_start",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 1,
                "time": 0.41,
            },
            {
                "phase": "command_run_end",
                "beat": "inspect",
                "action_id": "inspect_1",
                "chunk_index": 1,
                "time": 0.5,
            },
            {"phase": "beat_end", "beat": "inspect", "time": 0.6},
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
            minimum_section_spacing=0.0,
        ),
    )

    text = "".join(payload for _time, payload in read_output_events(output))
    assert "value-without-newline\r\n$ second" in text
    assert "value-without-newline$ second" not in text


def test_retime_keeps_second_command_in_beat_after_suppressed_command(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install.\r\n\r\n"),
            (0.1, "$ curl\r\n"),
            (1.0, "$ reploy\r\n"),
            (0.1, "Usage: reploy COMMAND\r\n"),
            (0.1, "# Next.\r\n\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {"phase": "caption_start", "beat": "install", "time": 0.1},
            {"phase": "caption_end", "beat": "install", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "output_mode": "suppress",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "time": 1.1,
            },
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 1,
                "command": "reploy",
                "time": 1.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 1,
                "command": "reploy",
                "time": 1.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 1,
                "time": 1.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 1,
                "time": 1.3,
            },
            {"phase": "beat_end", "beat": "install", "time": 1.31},
            {"phase": "beat_start", "beat": "next", "time": 1.32},
            {"phase": "caption_start", "beat": "next", "time": 1.4},
            {"phase": "caption_end", "beat": "next", "time": 1.41},
            {"phase": "beat_end", "beat": "next", "time": 1.42},
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
            post_enter_pause=0.1,
            post_command_pause=0.0,
        ),
    )

    visible = "".join(payload for _time, payload in read_output_events(output))
    assert visible.index("$ curl\r\n") < visible.index("$ reploy\r\n")
    assert visible.index("$ reploy\r\n") < visible.index("Usage: reploy COMMAND\r\n")
    assert visible.index("Usage: reploy COMMAND\r\n") < visible.index("# Next.")


def test_retime_synthesizes_typing_for_virtualenv_prompt(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Prepare.\r\n\r\n"),
            (0.1, "(arbiter_venv) $ python3 -m venv arbiter_venv\r\n"),
            (0.1, "(arbiter_venv) $ source arbiter_venv/bin/activate"),
            (0.0, "\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "prepare",
                "action_id": "prepare_1",
                "chunk_index": 0,
                "command": "python3 -m venv arbiter_venv",
                "time": 0.18,
            },
            {
                "phase": "command_prompt_end",
                "beat": "prepare",
                "action_id": "prepare_1",
                "chunk_index": 0,
                "command": "python3 -m venv arbiter_venv",
                "time": 0.22,
            },
            {
                "phase": "command_prompt_start",
                "beat": "prepare",
                "action_id": "prepare_1",
                "chunk_index": 1,
                "command": "source arbiter_venv/bin/activate",
                "time": 0.28,
            },
            {
                "phase": "command_prompt_end",
                "beat": "prepare",
                "action_id": "prepare_1",
                "chunk_index": 1,
                "command": "source arbiter_venv/bin/activate",
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
            post_enter_pause=0.0,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    assert any(payload == "p" for _time, payload in events)
    assert any(payload == "s" for _time, payload in events)
    assert not any(
        payload == "(arbiter_venv) $ python3 -m venv arbiter_venv\r\n"
        for _time, payload in events
    )


def test_retime_synthesizes_typing_when_prompt_event_precedes_timeline_interval(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install.\r\n\r\n"),
            (0.1, "$ curl\r\n"),
            (0.1, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {"phase": "caption_start", "beat": "install", "time": 0.1},
            {"phase": "caption_end", "beat": "install", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl",
                "time": 0.8,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "command": "curl",
                "time": 0.82,
            },
            {"phase": "beat_end", "beat": "install", "time": 0.9},
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
            post_enter_pause=0.0,
            post_command_pause=0.0,
        ),
    )

    events = read_output_events(output)
    assert time_when_plain_contains(events, "$ curl") >= time_when_plain_contains(
        events, "# Install."
    )
    assert "$ curl\r\n" not in [payload for _time, payload in events]


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
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.5,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.52,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.53,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.54,
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
            post_command_pause=0.6,
        ),
    )

    events = read_output_events(output)
    assert time_when_plain_contains(events, "$ hi") >= time_when_plain_contains(
        events, "# Caption."
    )
    assert joined_output(events) == "# Caption.\r\n\r\n$ hi\r\nok\r\n"


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
    assert prompt_tokens == ["$", "$", "$"]
    assert "".join(payload for _time, payload in events) == (
        "# Caption.\r\n\r\n$ first\r\n$ second\r\nok\r\n$"
    )


def test_retime_synthesizes_command_without_reusing_nearby_prompt(
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
        "# Caption.\r\n\r\n$ missing-command\r\nok\r\n$"
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

    assert text == "$ first\r\none\r\n$ second\r\ntwo\r\n$"
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
            typing_char_delay=0.1,
            typing_space_delay=0.1,
            typing_punctuation_delay=0.1,
            typing_newline_delay=0.0,
            post_enter_pause=0.0,
            post_command_pause=0.0,
        ),
        audio_durations={"intro": 2.0},
    )

    events = read_output_events(output)
    assert time_when_plain_contains(events, "ready") >= time_when_plain_contains(
        events, "# Intro."
    )
    assert events[-1] == (2.0, "")
    assert joined_output(events) == "# Intro.\r\nready\r\n"


def test_retime_wait_marker_delays_later_audio_anchor_until_command_finishes(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Bundle.\r\n\r\n"),
            (0.1, "$ reploy bundle list-options\r\n"),
            (0.1, "$ reploy bundle add --name imap,smtp\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "bundle", "time": 0.0},
            {"phase": "caption_start", "beat": "bundle", "time": 0.1},
            {
                "phase": "command_prompt_start",
                "beat": "bundle",
                "action_id": "bundle_1",
                "command_id": "list-options",
                "chunk_index": 0,
                "command": "reploy bundle list-options",
                "after": "@list@",
                "post_enter_pause": 1.0,
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "bundle",
                "action_id": "bundle_1",
                "command_id": "list-options",
                "chunk_index": 0,
                "command": "reploy bundle list-options",
                "after": "@list@",
                "post_enter_pause": 1.0,
                "time": 0.25,
            },
            {
                "phase": "command_run_start",
                "beat": "bundle",
                "action_id": "bundle_1",
                "command_id": "list-options",
                "chunk_index": 0,
                "output_mode": "suppress",
                "time": 0.25,
            },
            {
                "phase": "command_run_end",
                "beat": "bundle",
                "action_id": "bundle_1",
                "command_id": "list-options",
                "chunk_index": 0,
                "time": 0.3,
            },
            {
                "phase": "command_prompt_start",
                "beat": "bundle",
                "action_id": "bundle_1",
                "chunk_index": 1,
                "command": "reploy bundle add --name imap,smtp",
                "after": "@add@",
                "time": 0.35,
            },
            {
                "phase": "command_prompt_end",
                "beat": "bundle",
                "action_id": "bundle_1",
                "chunk_index": 1,
                "command": "reploy bundle add --name imap,smtp",
                "after": "@add@",
                "time": 0.4,
            },
            {"phase": "beat_end", "beat": "bundle", "time": 0.5},
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
        audio_durations={
            "bundle": retime_cast.AudioSegmentTiming(
                segment_id="bundle",
                duration=1.5,
                anchor_seconds={"@list@": 0.1, "@add@": 0.4},
                waits=(
                    retime_cast.AudioWaitTiming(
                        target="list-options",
                        marker="@wait:list-options+200ms@",
                        seconds=0.4,
                        gap_seconds=0.2,
                    ),
                ),
            )
        },
    )

    events = read_output_events(output)
    prompt_times = [time for time, payload in events if payload == "$"]

    assert prompt_times[1] < 1.4
    assert (
        time_when_plain_contains(events, "$ reploy bundle add --name imap,smtp") == 1.4
    )


def test_retime_spaces_caption_sections_by_audio_duration(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\x1b[36;1m# First\x1b[0m\r\n\r\n"),
            (0.2, "first output\r\n"),
            (0.1, "\x1b[36;1m# Second\x1b[0m\r\n\r\n"),
            (0.1, "second output\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "first", "time": 0.0},
            {"phase": "caption_start", "beat": "first", "time": 0.1},
            {"phase": "beat_end", "beat": "first", "time": 10.0},
            {"phase": "beat_start", "beat": "second", "time": 10.1},
            {"phase": "caption_start", "beat": "second", "time": 10.2},
            {"phase": "beat_end", "beat": "second", "time": 10.3},
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
            post_enter_pause=0.1,
            post_command_pause=0.0,
        ),
        audio_durations={"first": 5.0, "second": 3.0},
    )

    events = read_output_events(output)
    first_time = time_when_plain_contains(events, "# First")
    second_time = time_when_plain_contains(events, "# Second")

    assert first_time == 0.1
    assert second_time == 5.1
    assert events[-1][0] == 8.1


def test_retime_can_apply_fixed_minimum_section_spacing(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\x1b[36;1m# First\x1b[0m\r\n\r\n"),
            (0.2, "first output\r\n"),
            (0.1, "\x1b[36;1m# Second\x1b[0m\r\n\r\n"),
            (0.1, "second output\r\n"),
            (0.1, "\x1b[36;1m# Third\x1b[0m\r\n\r\n"),
            (0.1, "third output\r\n"),
        ],
    )
    write_timeline(timeline, [])

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
            minimum_section_spacing=2.0,
        ),
        audio_durations={"first": 99.0, "second": 99.0, "third": 99.0},
    )

    events = read_output_events(output)
    caption_times = [
        time_when_plain_contains(events, "# First"),
        time_when_plain_contains(events, "# Second"),
        time_when_plain_contains(events, "# Third"),
    ]

    assert caption_times == [0.1, 2.1, 4.1]
    assert events[-1][0] == 4.2


def test_retime_section_spacing_counts_viewer_hold_as_existing_spacing(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\x1b[36;1m# First\x1b[0m\r\n\r\n"),
            (0.1, "first output\r\n"),
            (0.1, "\x1b[36;1m# Second\x1b[0m\r\n\r\n"),
            (0.1, "second output\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "first", "time": 0.0},
            {"phase": "caption_start", "beat": "first", "time": 0.1},
            {"phase": "hold_start", "beat": "first", "seconds": 3.0, "time": 0.2},
            {"phase": "hold_end", "beat": "first", "seconds": 3.0, "time": 0.2},
            {"phase": "beat_end", "beat": "first", "time": 0.2},
            {"phase": "beat_start", "beat": "second", "time": 0.3},
            {"phase": "caption_start", "beat": "second", "time": 0.3},
            {"phase": "beat_end", "beat": "second", "time": 0.4},
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
            minimum_section_spacing=4.0,
        ),
    )

    events = read_output_events(output)
    first_output_time = next(
        time for time, payload in events if payload == "first output\r\n"
    )
    second_caption_time = time_when_plain_contains(events, "# Second")

    assert first_output_time == 0.2
    assert 4.2 <= second_caption_time <= 4.21


def test_retime_aligns_hidden_setup_timeline_to_visible_cast(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Caption.\r\n\r\n"),
            (0.1, "$ hi\r\n"),
            (0.1, "ok\r\n"),
            (1.0, "\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "check_start", "beat": "__setup__", "time": 0.0},
            {"phase": "check_end", "beat": "__setup__", "time": 8.0},
            {"phase": "beat_start", "beat": "show", "time": 8.05},
            {"phase": "caption_start", "beat": "show", "time": 8.1},
            {"phase": "caption_end", "beat": "show", "time": 8.11},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "time": 8.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "time": 8.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 8.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 8.3,
            },
            {"phase": "beat_end", "beat": "show", "time": 8.31},
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
            post_enter_pause=0.2,
            post_command_pause=0.0,
        ),
        audio_durations={"show": 2.0},
    )

    events = read_output_events(output)
    prompt_done = time_when_plain_contains(events, "$ hi")
    assert prompt_done >= time_when_plain_contains(events, "# Caption.")
    assert any(payload == "ok\r\n" for _time, payload in events)
    assert any(payload == "$" for time, payload in events if time > prompt_done)
    assert events[-1][0] >= 2.0
    assert joined_output(events) == "# Caption.\r\n\r\n$ hi\r\nok\r\n$"


def test_retime_shows_idle_prompt_during_tail_hold(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Caption.\r\n\r\n"),
            (0.1, "$ hi\r\n"),
            (0.1, "ok\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "show", "time": 0.0},
            {"phase": "caption_start", "beat": "show", "time": 0.1},
            {"phase": "caption_end", "beat": "show", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "command": "hi",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "show",
                "action_id": "show_1",
                "chunk_index": 0,
                "time": 0.3,
            },
            {"phase": "hold_start", "beat": "show", "seconds": 1.0, "time": 0.31},
            {"phase": "hold_end", "beat": "show", "seconds": 1.0, "time": 0.32},
            {"phase": "beat_end", "beat": "show", "time": 0.33},
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
    )

    events = read_output_events(output)

    ok_time = next(time for time, payload in events if payload == "ok\r\n")
    idle_prompt_time = next(
        time for time, payload in events if payload == "$" and time > ok_time
    )
    assert idle_prompt_time == ok_time + 0.001
    assert events[-1][0] >= events[-2][0] + 1.0


def test_retime_viewer_hold_and_audio_duration_use_longer_duration(
    tmp_path: Path,
) -> None:
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
        audio_durations={"intro": 99.0},
    )

    assert read_output_events(output)[-1] == (99.0, "")


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


def test_read_audio_segment_timings_resolves_anchor_from_word_timestamps(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.intro.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "Intro. Install now.",
                "words": [
                    {"word": "Intro.", "start": 0.0, "end": 0.4},
                    {"word": "Install", "start": 1.25, "end": 1.6},
                    {"word": "now.", "start": 1.7, "end": 2.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "intro",
                        "duration": 2.5,
                        "text": "Intro. Install now.",
                        "anchors": [
                            {
                                "id": "install",
                                "marker": "@install@",
                                "text_offset": len("Intro. "),
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["intro"].duration == 2.5
    assert timings["intro"].anchor_seconds == {"@install@": 1.25}


def test_read_audio_segment_timings_tolerates_tts_word_misspelling(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.bundle.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "Arbiter has plugins. List options.",
                "words": [
                    {"word": "Aubitor", "start": 0.0, "end": 0.35},
                    {"word": "has", "start": 0.35, "end": 0.55},
                    {"word": "plugins", "start": 0.55, "end": 0.9},
                    {"word": "List", "start": 1.25, "end": 1.5},
                    {"word": "options.", "start": 1.5, "end": 1.9},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "bundle",
                        "duration": 2.5,
                        "text": "Arbiter has plugins. List options.",
                        "anchors": [
                            {
                                "id": "list",
                                "marker": "@list@",
                                "text_offset": len("Arbiter has plugins. "),
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["bundle"].anchor_seconds == {"@list@": 1.25}


def test_timestamp_word_spans_aligns_transcript_substitution_to_script(
    tmp_path: Path,
) -> None:
    timestamp_path = tmp_path / "audio.bootstrap.timestamps.json"
    text = (
        "This file is the Hydra entry point: it selects the server schema, "
        "plugin list, and the env file used at startup. Next, bootstrap SMTP."
    )

    spans = retime_cast.timestamp_word_spans(
        text=text,
        words=[
            {"word": "This", "start": 0.0},
            {"word": "file", "start": 0.2},
            {"word": "is", "start": 0.4},
            {"word": "the", "start": 0.6},
            {"word": "Hydra", "start": 0.8},
            {"word": "entry", "start": 1.0},
            {"word": "point", "start": 1.2},
            {"word": "it", "start": 1.4},
            {"word": "selects", "start": 1.6},
            {"word": "the", "start": 1.8},
            {"word": "server", "start": 2.0},
            {"word": "schema", "start": 2.2},
            {"word": "plugin", "start": 2.4},
            {"word": "list", "start": 2.6},
            {"word": "and", "start": 2.8},
            {"word": "the", "start": 3.0},
            {"word": "end", "start": 3.2},
            {"word": "file", "start": 3.4},
            {"word": "used", "start": 3.6},
        ],
        timestamp_path=timestamp_path,
    )

    source_by_time = {seconds: text[start:end] for start, end, seconds in spans}
    assert source_by_time[3.2] == "env"
    assert source_by_time[3.6] == "used"


def test_timestamp_word_spans_rejects_low_confidence_alignment(
    tmp_path: Path,
) -> None:
    timestamp_path = tmp_path / "audio.bootstrap.timestamps.json"

    with pytest.raises(
        retime_cast.RetimeError,
        match="could not align audio timestamps",
    ):
        retime_cast.timestamp_word_spans(
            text="Create the config and check it.",
            words=[
                {"word": "completely", "start": 0.0},
                {"word": "different", "start": 0.2},
                {"word": "speech", "start": 0.4},
            ],
            timestamp_path=timestamp_path,
        )


def test_timestamp_word_spans_omits_weak_pairs_from_anchor_spans(
    tmp_path: Path,
) -> None:
    timestamp_path = tmp_path / "audio.bootstrap.timestamps.json"
    text = "Alpha beta target next omega."

    spans = retime_cast.timestamp_word_spans(
        text=text,
        words=[
            {"word": "Alpha", "start": 0.0},
            {"word": "beta", "start": 0.4},
            {"word": "wrong", "start": 0.8},
            {"word": "next", "start": 1.2},
            {"word": "omega", "start": 1.6},
        ],
        timestamp_path=timestamp_path,
    )

    source_by_time = {seconds: text[start:end] for start, end, seconds in spans}
    assert source_by_time == {
        0.0: "Alpha",
        0.4: "beta",
        1.2: "next",
        1.6: "omega",
    }


def test_read_audio_segment_timings_uses_aligned_script_word_for_anchor(
    tmp_path: Path,
) -> None:
    source_text = (
        "This file is the Hydra entry point: it selects the server schema, "
        "plugin list, and the env file used at startup. Next, bootstrap SMTP."
    )
    timestamps = tmp_path / "audio.bootstrap.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": source_text,
                "words": [
                    {"word": "This", "start": 0.0},
                    {"word": "file", "start": 0.2},
                    {"word": "is", "start": 0.4},
                    {"word": "the", "start": 0.6},
                    {"word": "Hydra", "start": 0.8},
                    {"word": "entry", "start": 1.0},
                    {"word": "point", "start": 1.2},
                    {"word": "it", "start": 1.4},
                    {"word": "selects", "start": 1.6},
                    {"word": "the", "start": 1.8},
                    {"word": "server", "start": 2.0},
                    {"word": "schema", "start": 2.2},
                    {"word": "plugin", "start": 2.4},
                    {"word": "list", "start": 2.6},
                    {"word": "and", "start": 2.8},
                    {"word": "the", "start": 3.0},
                    {"word": "end", "start": 3.2},
                    {"word": "file", "start": 3.4},
                    {"word": "used", "start": 3.6},
                    {"word": "at", "start": 3.8},
                    {"word": "startup", "start": 4.0},
                    {"word": "Next", "start": 4.5},
                    {"word": "bootstrap", "start": 4.8},
                    {"word": "SMTP", "start": 5.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "bootstrap",
                        "duration": 5.5,
                        "text": source_text,
                        "anchors": [
                            {
                                "id": "next",
                                "marker": "@next@",
                                "text_offset": source_text.index("Next"),
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["bootstrap"].anchor_seconds == {"@next@": 4.5}


def test_read_audio_segment_timings_rejects_stale_timestamp_source_text(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.intro.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "Old narration.",
                "words": [
                    {"word": "Old", "start": 0.0, "end": 0.2},
                    {"word": "narration.", "start": 0.2, "end": 0.6},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "intro",
                        "duration": 1.0,
                        "text": "New narration.",
                        "anchors": [{"id": "new", "marker": "@new@", "text_offset": 0}],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        retime_cast.RetimeError,
        match="audio timestamp source text is stale",
    ):
        retime_cast.read_audio_segment_timings(metadata)


def test_read_audio_segment_timings_resolves_wait_markers_from_word_timestamps(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.bundle.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "List options. Add plugins.",
                "words": [
                    {"word": "List", "start": 0.0, "end": 0.2},
                    {"word": "options.", "start": 0.25, "end": 0.5},
                    {"word": "Add", "start": 0.75, "end": 1.0},
                    {"word": "plugins.", "start": 1.1, "end": 1.4},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "bundle",
                        "duration": 1.6,
                        "text": "List options. Add plugins.",
                        "anchors": [
                            {"id": "add", "marker": "@add@", "text_offset": 14}
                        ],
                        "waits": [
                            {
                                "target": "list-options",
                                "marker": "@wait:list-options+250ms@",
                                "text_offset": len("List options."),
                                "gap_seconds": 0.25,
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["bundle"].anchor_seconds == {"@add@": 0.75}
    assert timings["bundle"].waits == (
        retime_cast.AudioWaitTiming(
            target="list-options",
            marker="@wait:list-options+250ms@",
            seconds=0.75,
            gap_seconds=0.25,
        ),
    )


def test_write_audio_presentation_waits_merges_concrete_waits(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "bundle",
                        "duration": 2.0,
                        "waits": [
                            {
                                "target": "list-options",
                                "marker": "@wait:list-options@",
                                "text_offset": 10,
                                "gap_seconds": 0.0,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    retime_cast.write_audio_presentation_waits(
        metadata,
        {
            "bundle": [
                {
                    "target": "list-options",
                    "marker": "@wait:list-options@",
                    "audio_second": 0.4,
                    "presentation_start": 1.1,
                    "presentation_end": 1.6,
                    "pause_seconds": 0.5,
                    "gap_seconds": 0.0,
                }
            ]
        },
    )

    payload = json.loads(metadata.read_text(encoding="utf-8"))

    assert payload["segments"][0]["waits"] == [
        {
            "target": "list-options",
            "marker": "@wait:list-options@",
            "text_offset": 10,
            "audio_second": 0.4,
            "presentation_start": 1.1,
            "presentation_end": 1.6,
            "pause_seconds": 0.5,
            "gap_seconds": 0.0,
        }
    ]


def test_read_audio_segment_timings_uses_script_text_for_transcription_drift(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.intro.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "First, install Reploy.",
                "words": [
                    {"word": "First", "start": 0.0, "end": 0.72},
                    {"word": "install", "start": 1.04, "end": 1.52},
                    {"word": "Reaploy", "start": 1.52, "end": 2.24},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "install-reploy",
                        "duration": 2.71,
                        "text": "First, install Reploy.",
                        "anchors": [
                            {
                                "id": "install",
                                "marker": "@install@",
                                "text_offset": len("First, "),
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["install-reploy"].anchor_seconds == {"@install@": 1.04}


def test_read_audio_segment_timings_resolves_end_anchor_to_segment_duration(
    tmp_path: Path,
) -> None:
    timestamps = tmp_path / "audio.intro.timestamps.json"
    timestamps.write_text(
        json.dumps(
            {
                "source_text": "Create staging.",
                "words": [
                    {"word": "Create", "start": 0.0, "end": 0.4},
                    {"word": "staging.", "start": 0.5, "end": 0.9},
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "audio.json"
    metadata.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": "intro",
                        "duration": 1.2,
                        "text": "Create staging.",
                        "anchors": [
                            {
                                "id": "create",
                                "marker": "@create@",
                                "text_offset": len("Create staging."),
                            }
                        ],
                        "timestamps": str(timestamps),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    timings = retime_cast.read_audio_segment_timings(metadata)

    assert timings["intro"].anchor_seconds == {"@create@": 1.2}


def test_retime_delays_command_until_audio_anchor(tmp_path: Path) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\x1b[36;1m# Install Reploy\x1b[0m\r\n\r\n"),
            (0.1, "$ curl\r\n"),
            (0.1, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install-reploy", "time": 0.0},
            {"phase": "caption_start", "beat": "install-reploy", "time": 0.1},
            {"phase": "caption_end", "beat": "install-reploy", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "command": "curl",
                "after": "@install@",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "command": "curl",
                "after": "@install@",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.3,
            },
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
            post_enter_pause=0.1,
            post_command_pause=0.0,
        ),
        audio_durations={
            "install-reploy": retime_cast.AudioSegmentTiming(
                segment_id="install-reploy",
                duration=3.0,
                anchor_seconds={"@install@": 2.0},
            )
        },
    )

    events = read_output_events(output)
    prompt_time = next(time for time, payload in events if payload == "$")
    command_time = time_when_plain_contains(events, "$ curl")
    output_time = next(time for time, payload in events if payload == "done\r\n")

    assert 0.1 < prompt_time < 0.2
    assert command_time >= 2.0
    assert command_time < output_time
    assert output_time >= command_time + 0.1


def test_realtime_command_output_preserves_recorded_start_delay() -> None:
    run_interval = retime_cast.TimelineInterval(
        start=10.0,
        end=15.0,
        start_event={},
        end_event={},
    )
    output_event = retime_cast.CastEvent(
        index=1,
        absolute_time=12.5,
        event_type="o",
        payload="building...\r",
    )
    normal = retime_cast.PresentationCommand(
        key=("beat", "action_1", "0"),
        prompt_interval=run_interval,
        run_interval=run_interval,
        prompt_payload="",
        output_mode="real",
        fake_output="",
        retime="normal",
        output_span=retime_cast.OutputSpan((output_event,)),
    )
    realtime = retime_cast.PresentationCommand(
        key=("beat", "action_1", "0"),
        prompt_interval=run_interval,
        run_interval=run_interval,
        prompt_payload="",
        output_mode="real",
        fake_output="",
        retime="realtime",
        output_span=retime_cast.OutputSpan((output_event,)),
    )
    scheduled: list[retime_cast.ScheduledEvent] = []

    retime_cast.schedule_command_output(
        scheduled=scheduled,
        command=normal,
        output_start=30.0,
        order=1.0,
    )
    retime_cast.schedule_command_output(
        scheduled=scheduled,
        command=realtime,
        output_start=40.0,
        order=2.0,
    )

    assert scheduled[0].absolute_time == 30.0
    assert scheduled[1].absolute_time == 42.5


def test_retime_audio_anchor_delays_command_without_delaying_caption(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "\x1b[36;1m# Install Reploy\x1b[0m\r\n\r\n"),
            (0.02, "done\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install-reploy", "time": 0.0},
            {"phase": "caption_start", "beat": "install-reploy", "time": 0.1},
            {"phase": "caption_end", "beat": "install-reploy", "time": 0.11},
            {
                "phase": "command_prompt_start",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "command": "curl",
                "after": "@install@",
                "time": 0.12,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "command": "curl",
                "after": "@install@",
                "time": 0.13,
            },
            {
                "phase": "command_run_start",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.14,
            },
            {
                "phase": "command_run_end",
                "beat": "install-reploy",
                "action_id": "install-reploy_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.2,
            },
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
        audio_durations={
            "install-reploy": retime_cast.AudioSegmentTiming(
                segment_id="install-reploy",
                duration=2.5,
                anchor_seconds={"@install@": 1.0},
            )
        },
    )

    events = read_output_events(output)
    caption_time = time_when_plain_contains(events, "# Install Reploy")
    prompt_time = next(time for time, payload in events if payload == "$")
    command_time = time_when_plain_contains(events, "$ curl")
    joined = joined_output(events)

    assert caption_time == 0.1
    assert 0.1 < prompt_time < 0.2
    assert command_time >= 1.0
    assert "$ curl\r\n" in joined
    assert "$$ curl" not in joined


def test_raw_fallback_does_not_pull_later_output_into_captioned_beat() -> None:
    events = [
        retime_cast.CastEvent(
            index=1,
            absolute_time=0.1,
            event_type="o",
            payload="\x1b[36;1m# Stage server\x1b[0m\r\n\r\n",
        ),
        retime_cast.CastEvent(
            index=2,
            absolute_time=8.0,
            event_type="o",
            payload="late output from next beat\r\n",
        ),
        retime_cast.CastEvent(
            index=3,
            absolute_time=9.0,
            event_type="o",
            payload="\x1b[36;1m# Discover capabilities\x1b[0m\r\n\r\n",
        ),
    ]

    raw_events = retime_cast.raw_events_for_beat(
        events=events,
        beat_start=0.0,
        beat_end=2.0,
        after_index=1,
        before_index=3,
        removed_event_indexes=set(),
    )

    assert raw_events == ()


def test_output_span_does_not_use_synthetic_next_prompt_as_boundary() -> None:
    first_key = ("first", "first_1", "0")
    second_key = ("second", "second_1", "0")
    first_interval = retime_cast.TimelineInterval(
        start=0.0,
        end=0.1,
        start_event={
            "beat": first_key[0],
            "action_id": first_key[1],
            "chunk_index": first_key[2],
        },
        end_event={},
    )
    second_interval = retime_cast.TimelineInterval(
        start=10.0,
        end=10.1,
        start_event={
            "beat": second_key[0],
            "action_id": second_key[1],
            "chunk_index": second_key[2],
        },
        end_event={},
    )
    run_interval = retime_cast.TimelineInterval(
        start=0.2,
        end=1.0,
        start_event={},
        end_event={},
    )
    first_output = retime_cast.CastEvent(
        index=1,
        absolute_time=0.8,
        event_type="o",
        payload="first output\r\n",
    )
    later_output = retime_cast.CastEvent(
        index=2,
        absolute_time=2.0,
        event_type="o",
        payload="second output\r\n",
    )

    span = retime_cast.output_span_for_command(
        events=[first_output, later_output],
        prompt_intervals=[first_interval, second_interval],
        command_index=0,
        run_interval=run_interval,
        beat_anchor_times={},
        beat_end_times={},
        baseline_prompt_start_by_key={second_key: second_interval.start},
        matched_prompt_keys=set(),
        caption_source_windows={},
        removed_event_indexes=set(),
        assigned_event_indexes=set(),
    )

    assert span.events == (first_output,)


def test_output_span_uses_shifted_next_prompt_boundary_within_beat() -> None:
    first_key = ("beat", "action_1", "0")
    second_key = ("beat", "action_1", "1")
    first_interval = retime_cast.TimelineInterval(
        start=10.0,
        end=10.1,
        start_event={
            "beat": first_key[0],
            "action_id": first_key[1],
            "chunk_index": first_key[2],
        },
        end_event={},
    )
    second_interval = retime_cast.TimelineInterval(
        start=10.5,
        end=10.6,
        start_event={
            "beat": second_key[0],
            "action_id": second_key[1],
            "chunk_index": second_key[2],
        },
        end_event={},
    )
    run_interval = retime_cast.TimelineInterval(
        start=10.2,
        end=10.9,
        start_event={},
        end_event={},
    )
    first_output = retime_cast.CastEvent(
        index=1,
        absolute_time=5.3,
        event_type="o",
        payload="first output\r\n",
    )
    second_output = retime_cast.CastEvent(
        index=2,
        absolute_time=5.7,
        event_type="o",
        payload="second output\r\n",
    )

    span = retime_cast.output_span_for_command(
        events=[first_output, second_output],
        prompt_intervals=[first_interval, second_interval],
        command_index=0,
        run_interval=run_interval,
        beat_anchor_times={"beat": 10.0},
        beat_end_times={},
        baseline_prompt_start_by_key={second_key: second_interval.start},
        matched_prompt_keys=set(),
        caption_source_windows={
            "beat": retime_cast.CaptionSourceWindow(
                payload=None,
                index=None,
                time=5.0,
                next_index=None,
                next_time=None,
            )
        },
        removed_event_indexes=set(),
        assigned_event_indexes=set(),
    )

    assert span.events == (first_output,)


def test_retime_keeps_anchor_beat_open_until_narration_finishes(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Install.\r\n\r\n"),
            (0.1, "$ curl\r\n"),
            (0.1, "done\r\n"),
            (0.1, "# Next.\r\n\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "install", "time": 0.0},
            {
                "phase": "command_prompt_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.2,
            },
            {
                "phase": "command_prompt_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.21,
            },
            {
                "phase": "command_run_start",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.22,
            },
            {
                "phase": "command_run_end",
                "beat": "install",
                "action_id": "install_1",
                "chunk_index": 0,
                "after": "@install@",
                "time": 0.3,
            },
            {"phase": "beat_end", "beat": "install", "time": 0.31},
            {"phase": "beat_start", "beat": "next", "time": 0.32},
            {"phase": "beat_end", "beat": "next", "time": 0.4},
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
        audio_durations={
            "install": retime_cast.AudioSegmentTiming(
                segment_id="install",
                duration=2.0,
                anchor_seconds={"@install@": 0.5},
            )
        },
    )

    output_events = read_output_events(output)
    next_caption_time = time_when_plain_contains(output_events, "# Next")

    assert next_caption_time >= 2.0


def test_retime_unanchored_beat_last_at_least_narration_duration(
    tmp_path: Path,
) -> None:
    cast = tmp_path / "baseline.cast"
    output = tmp_path / "retimed.cast"
    timeline = tmp_path / "baseline.timeline.jsonl"
    write_cast(
        cast,
        [
            (0.1, "# Intro.\r\n\r\n"),
            (0.1, "# Next.\r\n\r\n"),
        ],
    )
    write_timeline(
        timeline,
        [
            {"phase": "beat_start", "beat": "intro", "time": 0.0},
            {"phase": "caption_start", "beat": "intro", "time": 0.1},
            {"phase": "caption_end", "beat": "intro", "time": 0.11},
            {"phase": "beat_end", "beat": "intro", "time": 0.2},
            {"phase": "beat_start", "beat": "next", "time": 0.21},
            {"phase": "caption_start", "beat": "next", "time": 0.22},
            {"phase": "caption_end", "beat": "next", "time": 0.23},
            {"phase": "beat_end", "beat": "next", "time": 0.24},
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
        audio_durations={
            "intro": retime_cast.AudioSegmentTiming(
                segment_id="intro",
                duration=2.0,
                anchor_seconds={},
            )
        },
    )

    output_events = read_output_events(output)
    next_caption_time = time_when_plain_contains(output_events, "# Next")

    assert next_caption_time >= 2.0


def test_retime_uses_manifest_paths_and_rules(tmp_path: Path, monkeypatch: Any) -> None:
    cast = tmp_path / "demo.cast"
    timeline = tmp_path / "demo.timeline.jsonl"
    output = tmp_path / "demo.presentation.cast"
    write_cast(cast, [(0.1, "$ x\r\n")])
    write_cast(output, [(0.1, "$ x\r\n")])
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "demo",
                "action_id": "demo_1",
                "chunk_index": 0,
                "time": 0.05,
            },
            {
                "phase": "command_prompt_end",
                "beat": "demo",
                "action_id": "demo_1",
                "chunk_index": 0,
                "time": 0.15,
            },
        ],
    )
    os.utime(output, None)
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


def test_retime_check_rejects_stale_output(tmp_path: Path, monkeypatch: Any) -> None:
    cast = tmp_path / "demo.cast"
    timeline = tmp_path / "demo.timeline.jsonl"
    output = tmp_path / "demo.presentation.cast"
    write_cast(cast, [(0.1, "$ x\r\n")])
    write_timeline(
        timeline,
        [
            {
                "phase": "command_prompt_start",
                "beat": "demo",
                "action_id": "demo_1",
                "chunk_index": 0,
                "time": 0.05,
            },
            {
                "phase": "command_prompt_end",
                "beat": "demo",
                "action_id": "demo_1",
                "chunk_index": 0,
                "time": 0.15,
            },
        ],
    )
    write_cast(output, [(0.1, "$ x\r\n")])
    os.utime(output, (1000, 1000))
    os.utime(cast, (1010, 1010))
    os.utime(timeline, (1020, 1020))
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
            "retime": {},
            "beats": [{"id": "demo", "actions": [{"run": "true"}]}],
        },
    )

    try:
        retime_cast.run_tool_from_hydra_cfg(object())
    except retime_cast.RetimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("stale retimed cast should fail retime check")

    assert "retimed cast is stale" in message
    assert str(output) in message
    assert "demo.cast" in message
    assert "demo.timeline.jsonl" in message
