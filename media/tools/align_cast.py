#!/usr/bin/env python3
"""Align an asciinema cast with a recording manifest.

This is a proof-of-concept analyzer. It uses visible captions and prompted
command lines, so it can tell whether a cast still resembles the manifest, but
it is not a durable sync format.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only outside dev envs.
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDINGS_DIR = REPO_ROOT / "media" / "recordings"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class AlignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class CastLine:
    time: float
    text: str


@dataclass(frozen=True)
class ObservedCommand:
    time: float
    beat_caption: str | None
    text: str


@dataclass(frozen=True)
class ExpectedCommand:
    beat_id: str
    beat_caption: str | None
    action_index: int
    text: str


def load_manifest(recording_id: str) -> dict[str, Any]:
    if yaml is None:
        raise AlignmentError("PyYAML is required to read recording manifests")
    path = RECORDINGS_DIR / f"{recording_id}.yaml"
    if not path.exists():
        raise AlignmentError(f"recording manifest not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AlignmentError(f"recording manifest must be a mapping: {path}")
    data["_manifest_path"] = str(path)
    return data


def as_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


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
        raise AlignmentError("manifest outputs.cast must be a non-empty string")
    return relative_path(cast)


def clean_terminal_text(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def read_cast_lines(path: Path) -> list[CastLine]:
    if not path.exists():
        raise AlignmentError(f"cast file not found: {path}")

    lines: list[CastLine] = []
    current: list[str] = []
    current_time = 0.0
    absolute_time = 0.0

    with path.open(encoding="utf-8") as handle:
        header = handle.readline()
        if not header:
            raise AlignmentError(f"cast file is empty: {path}")
        try:
            json.loads(header)
        except json.JSONDecodeError as exc:
            raise AlignmentError(f"invalid asciinema header in {path}") from exc

        for raw in handle:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AlignmentError(f"invalid asciinema event in {path}") from exc
            if (
                not isinstance(event, list)
                or len(event) != 3
                or not isinstance(event[0], (int, float))
            ):
                continue
            delay, event_type, payload = event
            absolute_time += float(delay)
            if event_type != "o" or not isinstance(payload, str):
                continue
            for char in clean_terminal_text(payload):
                if not current:
                    current_time = absolute_time
                if char == "\n":
                    lines.append(CastLine(current_time, "".join(current)))
                    current = []
                else:
                    current.append(char)
        if current:
            lines.append(CastLine(current_time, "".join(current)))
    return lines


def terminal_command_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        result.append(line.strip())
    return result


def expected_captions(spec: dict[str, Any]) -> list[tuple[str, str]]:
    captions: list[tuple[str, str]] = []
    for beat in as_list(spec.get("beats")):
        if not isinstance(beat, dict):
            continue
        beat_id = beat.get("id")
        caption = beat.get("caption")
        if isinstance(beat_id, str) and isinstance(caption, str) and caption:
            captions.append((beat_id, caption))
    return captions


def expected_commands(spec: dict[str, Any]) -> list[ExpectedCommand]:
    commands: list[ExpectedCommand] = []
    for beat in as_list(spec.get("beats")):
        if not isinstance(beat, dict):
            continue
        beat_id = beat.get("id")
        caption = beat.get("caption")
        if not isinstance(beat_id, str):
            continue
        beat_caption = caption if isinstance(caption, str) else None
        for index, action in enumerate(as_list(beat.get("actions")), start=1):
            if not isinstance(action, dict):
                continue
            display = action.get("display", action.get("run"))
            if not isinstance(display, str):
                continue
            for command in terminal_command_lines(display):
                commands.append(
                    ExpectedCommand(
                        beat_id=beat_id,
                        beat_caption=beat_caption,
                        action_index=index,
                        text=command,
                    )
                )
    return commands


def observed_captions(lines: list[CastLine]) -> list[tuple[float, str]]:
    captions: list[tuple[float, str]] = []
    for line in lines:
        text = line.text.strip()
        if text.startswith("# "):
            captions.append((line.time, text[2:]))
    return captions


def observed_commands(lines: list[CastLine]) -> list[ObservedCommand]:
    commands: list[ObservedCommand] = []
    current_caption: str | None = None
    previous_command: ObservedCommand | None = None
    for line in lines:
        text = line.text.rstrip()
        stripped = text.strip()
        if stripped.startswith("# "):
            current_caption = stripped[2:]
            previous_command = None
            continue
        if text.startswith("$ "):
            previous_command = ObservedCommand(
                time=line.time,
                beat_caption=current_caption,
                text=text[2:].strip(),
            )
            commands.append(previous_command)
            continue
        if previous_command is not None and text.startswith("  ") and stripped:
            previous_command = ObservedCommand(
                time=line.time,
                beat_caption=current_caption,
                text=stripped,
            )
            commands.append(previous_command)
            continue
        previous_command = None
    return commands


@dataclass(frozen=True)
class AlignmentReport:
    text: str
    aligned: bool


def render_report(spec: dict[str, Any], cast_path: Path) -> AlignmentReport:
    lines = read_cast_lines(cast_path)
    expected_caps = expected_captions(spec)
    observed_caps = observed_captions(lines)
    expected_cmds = expected_commands(spec)
    observed_cmds = observed_commands(lines)

    report: list[str] = [
        f"manifest: {display_path(Path(spec['_manifest_path']))}",
        f"cast: {display_path(cast_path)}",
        "",
        "Captions",
    ]

    matched_captions = 0
    for index, (beat_id, caption) in enumerate(expected_caps):
        observed = observed_caps[index] if index < len(observed_caps) else None
        if observed is not None and observed[1] == caption:
            matched_captions += 1
            report.append(f"  ok  {observed[0]:7.3f}s  {beat_id}: {caption}")
        elif observed is None:
            report.append(f"  miss {'':7}   {beat_id}: {caption}")
        else:
            report.append(f"  diff {observed[0]:7.3f}s  {beat_id}: {caption}")
            report.append(f"       observed: {observed[1]}")
    for observed in observed_caps[len(expected_caps) :]:
        report.append(f"  extra {observed[0]:7.3f}s  {observed[1]}")

    report.extend(["", "Commands"])
    matched_commands = 0
    for index, expected in enumerate(expected_cmds):
        observed = observed_cmds[index] if index < len(observed_cmds) else None
        if observed is None:
            report.append(
                f"  miss          {expected.beat_id}.{expected.action_index}: {expected.text}"
            )
            continue
        if observed.text == expected.text:
            matched_commands += 1
            report.append(
                f"  ok  {observed.time:7.3f}s  "
                f"{expected.beat_id}.{expected.action_index}: {expected.text}"
            )
        else:
            report.append(
                f"  diff {observed.time:7.3f}s  "
                f"{expected.beat_id}.{expected.action_index}: {expected.text}"
            )
            report.append(f"       observed: {observed.text}")
    for observed in observed_cmds[len(expected_cmds) :]:
        report.append(f"  extra {observed.time:7.3f}s  {observed.text}")

    report.extend(
        [
            "",
            "Summary",
            f"  captions: {matched_captions}/{len(expected_caps)} matched",
            f"  commands: {matched_commands}/{len(expected_cmds)} matched",
        ]
    )
    aligned = (
        matched_captions == len(expected_caps)
        and len(observed_caps) == len(expected_caps)
        and matched_commands == len(expected_cmds)
        and len(observed_cmds) == len(expected_cmds)
    )
    if aligned:
        report.extend(["", "Review", "  aligned: no manual review required"])
    else:
        report.extend(
            [
                "",
                "Review",
                "  misaligned: manual review required",
                "  Check whether the recording should be regenerated, the manifest",
                "  should be updated, or the movie script no longer matches the",
                "  versioned workflow.",
            ]
        )
    report.extend(
        [
            "",
            "Limitations",
            "  This POC aligns visible text only. It can drift if captions, prompts,",
            "  terminal wrapping, ANSI output, or command text changes. A production",
            "  retiming pipeline should emit a sidecar timeline with beat/action/phase",
            "  boundaries during capture.",
        ]
    )
    return AlignmentReport(text="\n".join(report), aligned=aligned)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recording", help="Recording id, for example install-and-bootstrap."
    )
    parser.add_argument("--cast", help="Override the cast path from the manifest.")
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="Print the report but exit 0 even when manual review is required.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec = load_manifest(args.recording)
        cast_path = (
            relative_path(args.cast) if args.cast else cast_path_from_manifest(spec)
        )
        report = render_report(spec, cast_path)
        print(report.text)
        if report.aligned or args.allow_mismatch:
            return 0
        return 2
    except AlignmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
