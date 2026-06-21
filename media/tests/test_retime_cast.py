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
    assert prompt_times == [0.25, 0.35, 0.45, 0.55]
    ok_time = next(time for time, payload in events if payload == "ok\r\n")
    assert ok_time >= 0.95
    assert events[-1][0] >= 3.0
    assert "".join(payload for _time, payload in events) == (
        "# Show a command.\r\n\r\n$ hi\r\nok\r\n"
    )


def test_retime_uses_manifest_paths_and_rules(tmp_path: Path, monkeypatch: Any) -> None:
    manifest = tmp_path / "demo.yaml"
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
    manifest.write_text(
        json.dumps(
            {
                "id": "demo",
                "title": "Demo",
                "outputs": {
                    "cast": str(cast),
                    "retimed_cast": str(output),
                },
                "retime": {"post_enter_pause": 0.2},
                "beats": [{"id": "demo", "actions": [{"run": "true"}]}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(retime_cast, "RECORDINGS_DIR", tmp_path)

    result = retime_cast.main(["demo", "--check"])

    assert result == 0
    assert retime_cast.timeline_path_for_cast(cast) == timeline
