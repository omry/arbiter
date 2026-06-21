#!/usr/bin/env python3
"""Generate a presentation-timed asciinema cast from a fast baseline cast."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import hydra
from omegaconf import DictConfig

from studio_config import (
    CONFIG_DIR,
    StudioConfigError,
    container_from_hydra_cfg,
    load_recording_spec,
    load_recording_spec_from_hydra_cfg,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class RetimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CastEvent:
    index: int
    absolute_time: float
    event_type: str
    payload: Any


@dataclass(frozen=True)
class ScheduledEvent:
    absolute_time: float
    order: float
    event_type: str
    payload: Any


@dataclass(frozen=True)
class TimelineInterval:
    start: float
    end: float
    start_event: dict[str, Any]
    end_event: dict[str, Any]


@dataclass(frozen=True)
class TimingRules:
    typing_char_delay: float = 0.035
    typing_space_delay: float = 0.02
    typing_punctuation_delay: float = 0.05
    typing_newline_delay: float = 0.0
    post_enter_pause: float = 0.35
    post_command_pause: float = 0.85


def load_manifest(
    recording_id: str, overrides: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    try:
        return load_recording_spec(recording_id, overrides)
    except StudioConfigError as exc:
        raise RetimeError(str(exc)) from exc


def as_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def cast_path_from_manifest(spec: dict[str, Any]) -> Path:
    outputs = as_mapping(spec.get("outputs"))
    cast = outputs.get("cast")
    if not isinstance(cast, str) or not cast:
        raise RetimeError("recording config outputs.cast must be a non-empty string")
    return relative_path(cast)


def timeline_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".timeline.jsonl")


def output_path_from_manifest(spec: dict[str, Any], cast_path: Path) -> Path:
    outputs = as_mapping(spec.get("outputs"))
    retimed = outputs.get("retimed_cast")
    if isinstance(retimed, str) and retimed:
        return relative_path(retimed)
    return cast_path.with_name(f"{cast_path.stem}.retimed{cast_path.suffix}")


def require_number(mapping: dict[str, Any], key: str, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value < 0:
        raise RetimeError(f"retime.{key} must be a non-negative number")
    return float(value)


def timing_rules_from_manifest(spec: dict[str, Any]) -> TimingRules:
    retime = as_mapping(spec.get("retime"))
    return TimingRules(
        typing_char_delay=require_number(
            retime, "typing_char_delay", TimingRules.typing_char_delay
        ),
        typing_space_delay=require_number(
            retime, "typing_space_delay", TimingRules.typing_space_delay
        ),
        typing_punctuation_delay=require_number(
            retime, "typing_punctuation_delay", TimingRules.typing_punctuation_delay
        ),
        typing_newline_delay=require_number(
            retime, "typing_newline_delay", TimingRules.typing_newline_delay
        ),
        post_enter_pause=require_number(
            retime, "post_enter_pause", TimingRules.post_enter_pause
        ),
        post_command_pause=require_number(
            retime, "post_command_pause", TimingRules.post_command_pause
        ),
    )


def read_cast(path: Path) -> tuple[dict[str, Any], list[CastEvent]]:
    if not path.exists():
        raise RetimeError(f"cast file not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RetimeError(f"cast file is empty: {path}")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RetimeError(f"invalid asciinema header in {path}") from exc
    if not isinstance(header, dict):
        raise RetimeError(f"asciinema header must be a mapping: {path}")

    absolute_time = 0.0
    events: list[CastEvent] = []
    for index, line in enumerate(lines[1:]):
        try:
            raw_event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetimeError(f"invalid asciinema event in {path}:{index + 2}") from exc
        if (
            not isinstance(raw_event, list)
            or len(raw_event) != 3
            or not isinstance(raw_event[0], (int, float))
            or not isinstance(raw_event[1], str)
        ):
            raise RetimeError(f"invalid asciinema event in {path}:{index + 2}")
        absolute_time += float(raw_event[0])
        events.append(
            CastEvent(
                index=index,
                absolute_time=absolute_time,
                event_type=raw_event[1],
                payload=raw_event[2],
            )
        )
    return header, events


def write_cast(path: Path, header: dict[str, Any], events: list[ScheduledEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(events, key=lambda event: (event.absolute_time, event.order))
    lines = [json.dumps(header, separators=(",", ":"))]
    previous = 0.0
    for event in ordered:
        absolute = max(previous, event.absolute_time)
        delay = round(absolute - previous, 6)
        previous = absolute
        lines.append(json.dumps([delay, event.event_type, event.payload], separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_timeline(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RetimeError(f"timeline file not found: {path}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetimeError(f"invalid timeline event in {path}:{line_number}") from exc
        if not isinstance(event, dict):
            raise RetimeError(f"timeline event must be a mapping: {path}:{line_number}")
        if not isinstance(event.get("time"), (int, float)):
            raise RetimeError(f"timeline event missing numeric time: {path}:{line_number}")
        events.append(event)
    return sorted(events, key=lambda event: float(event["time"]))


def interval_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("beat", "")),
        str(event.get("action_id", "")),
        str(event.get("chunk_index", "")),
    )


def pair_intervals(
    timeline: list[dict[str, Any]],
    *,
    start_phase: str,
    end_phase: str,
) -> list[TimelineInterval]:
    starts: dict[tuple[str, str, str], dict[str, Any]] = {}
    intervals: list[TimelineInterval] = []
    for event in timeline:
        phase = event.get("phase")
        if phase == start_phase:
            starts[interval_key(event)] = event
        elif phase == end_phase:
            key = interval_key(event)
            start_event = starts.pop(key, None)
            if start_event is None:
                continue
            start = float(start_event["time"])
            end = float(event["time"])
            if end >= start:
                intervals.append(TimelineInterval(start, end, start_event, event))
    return intervals


def pair_hold_intervals(timeline: list[dict[str, Any]]) -> list[TimelineInterval]:
    starts: dict[str, dict[str, Any]] = {}
    intervals: list[TimelineInterval] = []
    for event in timeline:
        phase = event.get("phase")
        beat = str(event.get("beat", ""))
        if phase == "hold_start":
            starts[beat] = event
        elif phase == "hold_end":
            start_event = starts.pop(beat, None)
            if start_event is None:
                continue
            start = float(start_event["time"])
            end = float(event["time"])
            if end >= start:
                intervals.append(TimelineInterval(start, end, start_event, event))
    return intervals


def token_delay(token: str, rules: TimingRules) -> float:
    if not token or ANSI_RE.fullmatch(token):
        return 0.0
    if token in {"\r", "\n"}:
        return rules.typing_newline_delay
    if token.isspace():
        return rules.typing_space_delay
    if token in "|;&,.:=/\"'{}[]()-_":
        return rules.typing_punctuation_delay
    return rules.typing_char_delay


def tokenize_terminal_payload(payload: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    for match in ANSI_RE.finditer(payload):
        tokens.extend(payload[position : match.start()])
        tokens.append(match.group(0))
        position = match.end()
    tokens.extend(payload[position:])
    return [token for token in tokens if token]


def prompt_events_for_interval(
    events: list[CastEvent], interval: TimelineInterval
) -> list[CastEvent]:
    return [
        event
        for event in events
        if event.event_type == "o"
        and isinstance(event.payload, str)
        and interval.start <= event.absolute_time <= interval.end
    ]


def shifted_time(
    timestamp: float,
    insertions: list[tuple[float, float]],
    *,
    inclusive: bool,
) -> float:
    shift = 0.0
    for anchor, amount in insertions:
        if anchor < timestamp or (inclusive and anchor <= timestamp):
            shift += amount
    return timestamp + shift


def shift_before(timestamp: float, insertions: list[tuple[float, float]]) -> float:
    return sum(amount for anchor, amount in insertions if anchor < timestamp)


def retime_events(
    events: list[CastEvent],
    timeline: list[dict[str, Any]],
    rules: TimingRules,
) -> list[ScheduledEvent]:
    prompt_intervals = pair_intervals(
        timeline,
        start_phase="command_prompt_start",
        end_phase="command_prompt_end",
    )
    run_intervals = pair_intervals(
        timeline,
        start_phase="command_run_start",
        end_phase="command_run_end",
    )
    hold_intervals = pair_hold_intervals(timeline)

    insertions: list[tuple[float, float]] = []
    removed_event_indexes: set[int] = set()
    replacements: list[tuple[ScheduledEvent, float]] = []

    for interval in prompt_intervals:
        prompt_events = prompt_events_for_interval(events, interval)
        if not prompt_events:
            continue
        original_duration = max(0.0, interval.end - interval.start)
        local_time = interval.start
        replacement_order = prompt_events[0].index - 0.25
        for event in prompt_events:
            removed_event_indexes.add(event.index)
            for token in tokenize_terminal_payload(event.payload):
                replacements.append(
                    (
                        ScheduledEvent(
                            absolute_time=local_time,
                            order=replacement_order,
                            event_type=event.event_type,
                            payload=token,
                        ),
                        interval.start,
                    )
                )
                replacement_order += 0.0001
                local_time += token_delay(token, rules)
        typing_duration = max(0.0, local_time - interval.start)
        extra_typing_time = max(0.0, typing_duration - original_duration)
        insertion = extra_typing_time + rules.post_enter_pause
        if insertion > 0:
            insertions.append((interval.end, insertion))

    for interval in run_intervals:
        if rules.post_command_pause > 0:
            insertions.append((interval.end, rules.post_command_pause))

    for interval in hold_intervals:
        desired = interval.end_event.get("seconds", interval.start_event.get("seconds", 0.0))
        if not isinstance(desired, (int, float)):
            continue
        observed = max(0.0, interval.end - interval.start)
        insertion = max(0.0, float(desired) - observed)
        if insertion > 0:
            insertions.append((interval.end, insertion))

    insertions.sort()
    scheduled: list[ScheduledEvent] = []
    for event in events:
        if event.index in removed_event_indexes:
            continue
        scheduled.append(
            ScheduledEvent(
                absolute_time=shifted_time(
                    event.absolute_time, insertions, inclusive=True
                ),
                order=float(event.index),
                event_type=event.event_type,
                payload=event.payload,
            )
        )

    for event, prompt_start in replacements:
        prior_shift = shift_before(prompt_start, insertions)
        scheduled.append(
            ScheduledEvent(
                absolute_time=event.absolute_time + prior_shift,
                order=event.order,
                event_type=event.event_type,
                payload=event.payload,
            )
        )
    if scheduled and insertions:
        final_event_time = max(event.absolute_time for event in scheduled)
        final_inserted_time = max(
            shifted_time(anchor, insertions, inclusive=True)
            for anchor, _amount in insertions
        )
        if final_inserted_time > final_event_time:
            scheduled.append(
                ScheduledEvent(
                    absolute_time=final_inserted_time,
                    order=1_000_000_000.0,
                    event_type="o",
                    payload="",
                )
            )
    return scheduled


def retime_cast(
    *,
    cast_path: Path,
    timeline_path: Path,
    output_path: Path,
    rules: TimingRules,
) -> None:
    header, events = read_cast(cast_path)
    timeline = read_timeline(timeline_path)
    if not events:
        raise RetimeError(f"cast contains no events: {cast_path}")
    scheduled = retime_events(events, timeline, rules)
    write_cast(output_path, header, scheduled)


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
        spec = load_recording_spec_from_hydra_cfg(cfg)
        action = config.get("action", "retime")
        if action not in {"retime", "check"}:
            raise RetimeError("action must be 'retime' or 'check'")
        cast_override = config.get("cast")
        timeline_override = config.get("timeline")
        output_override = config.get("output")
        for name, value in [
            ("cast", cast_override),
            ("timeline", timeline_override),
            ("output", output_override),
        ]:
            if value is not None and not isinstance(value, str):
                raise RetimeError(f"{name} must be a string or null")
        cast_path = (
            relative_path(cast_override)
            if cast_override
            else cast_path_from_manifest(spec)
        )
        timeline_path = (
            relative_path(timeline_override)
            if timeline_override
            else timeline_path_for_cast(cast_path)
        )
        output_path = (
            relative_path(output_override)
            if output_override
            else output_path_from_manifest(spec, cast_path)
        )
        rules = timing_rules_from_manifest(spec)
        header, events = read_cast(cast_path)
        timeline = read_timeline(timeline_path)
        if action == "check":
            if not events:
                raise RetimeError(f"cast contains no events: {cast_path}")
            if not timeline:
                raise RetimeError(f"timeline contains no events: {timeline_path}")
            print(
                "ok: "
                f"{spec['_recording_id']} retime "
                f"cast={display_path(cast_path)} "
                f"timeline={display_path(timeline_path)} "
                f"output={display_path(output_path)}"
            )
            return 0
        scheduled = retime_events(events, timeline, rules)
        write_cast(output_path, header, scheduled)
        print(f"wrote {display_path(output_path)}")
        return 0
    except StudioConfigError as exc:
        raise RetimeError(str(exc)) from exc


@hydra.main(version_base=None, config_path=str(CONFIG_DIR), config_name="config")
def main(cfg: DictConfig) -> None:
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except RetimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
