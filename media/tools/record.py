#!/usr/bin/env python3
"""Record Arbiter media casts from YAML recording manifests."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only outside dev envs.
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDINGS_DIR = REPO_ROOT / "media" / "recordings"


class RecordingError(RuntimeError):
    pass


def load_manifest(recording_id: str) -> dict[str, Any]:
    if yaml is None:
        raise RecordingError(
            "PyYAML is required; install it in the recording environment"
        )
    path = RECORDINGS_DIR / f"{recording_id}.yaml"
    if not path.exists():
        raise RecordingError(f"recording manifest not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RecordingError(f"recording manifest must be a mapping: {path}")
    data["_manifest_path"] = str(path)
    return data


def require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RecordingError(f"manifest field {key!r} must be a non-empty string")
    return value


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RecordingError(f"manifest field {field!r} must be a mapping")
    return value


def as_list(value: object, *, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RecordingError(f"manifest field {field!r} must be a list")
    return value


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def check_asciinema() -> str:
    try:
        result = subprocess.run(
            ["asciinema", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RecordingError(
            "asciinema 3.x is required and was not found on PATH"
        ) from exc
    version = result.stdout.strip()
    match = re.search(r"\b(\d+)\.", version)
    if match is None or int(match.group(1)) < 3:
        raise RecordingError(f"asciinema 3.x is required, found: {version}")
    return version


def check_required_commands(spec: dict[str, Any]) -> None:
    requirements = as_mapping(spec.get("requirements"), field="requirements")
    search_path = os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")]
    )
    for command in as_list(requirements.get("commands"), field="requirements.commands"):
        if not isinstance(command, str) or not command:
            raise RecordingError(
                "requirements.commands values must be non-empty strings"
            )
        if shutil.which(command, path=search_path) is None:
            raise RecordingError(f"required command not found on PATH: {command}")


def require_non_negative_number(
    mapping: dict[str, Any], key: str, default: float
) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value < 0:
        raise RecordingError(f"style.{key} must be a non-negative number")
    return float(value)


def require_positive_number(mapping: dict[str, Any], key: str, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, (int, float)) or value <= 0:
        raise RecordingError(f"style.{key} must be a positive number")
    return float(value)


def require_integer(mapping: dict[str, Any], key: str, default: int) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int):
        raise RecordingError(f"style.{key} must be an integer")
    return value


def validate_manifest(spec: dict[str, Any]) -> None:
    recording_id = require_string(spec, "id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", recording_id):
        raise RecordingError("recording id must be lowercase kebab-case")
    require_string(spec, "title")
    outputs = as_mapping(spec.get("outputs"), field="outputs")
    require_string(outputs, "cast")
    capture = as_mapping(spec.get("capture"), field="capture")
    window_size = capture.get("window_size", "100x28")
    if not isinstance(window_size, str) or not re.fullmatch(r"\d+x\d+", window_size):
        raise RecordingError("capture.window_size must look like COLSxROWS")
    idle_time_limit = capture.get("idle_time_limit")
    if idle_time_limit is not None and (
        not isinstance(idle_time_limit, (int, float)) or idle_time_limit <= 0
    ):
        raise RecordingError("capture.idle_time_limit must be a positive number")
    baseline_compressed = capture.get("baseline_compressed", False)
    if not isinstance(baseline_compressed, bool):
        raise RecordingError("capture.baseline_compressed must be a boolean")
    setup = as_list(spec.get("setup"), field="setup")
    for index, step in enumerate(setup, start=1):
        if not isinstance(step, dict):
            raise RecordingError("each setup step must be a mapping")
        require_string(step, "run")
        name = step.get("name")
        if name is not None and (not isinstance(name, str) or not name):
            raise RecordingError(f"setup.{index}.name must be a non-empty string")
    beats = as_list(spec.get("beats"), field="beats")
    if not beats:
        raise RecordingError("manifest must contain at least one beat")
    for beat in beats:
        if not isinstance(beat, dict):
            raise RecordingError("each beat must be a mapping")
        require_string(beat, "id")
        actions = as_list(beat.get("actions"), field=f"beats.{beat['id']}.actions")
        for action in actions:
            if not isinstance(action, dict):
                raise RecordingError(f"beat {beat['id']} action must be a mapping")
            require_string(action, "run")
        checks = as_list(beat.get("checks"), field=f"beats.{beat['id']}.checks")
        for check in checks:
            if not isinstance(check, dict):
                raise RecordingError(f"beat {beat['id']} check must be a mapping")
            require_string(check, "run")
            name = check.get("name")
            if name is not None and (not isinstance(name, str) or not name):
                raise RecordingError(
                    f"beat {beat['id']} check name must be a non-empty string"
                )


def shell_expect_args(expect: dict[str, Any]) -> list[str]:
    args: list[str] = []
    exit_code = expect.get("exit_code", 0)
    if not isinstance(exit_code, int):
        raise RecordingError("expect.exit_code must be an integer")
    args.extend(["exit", str(exit_code)])
    for field, gate_name in [
        ("output_contains", "contains"),
        ("output_regex", "regex"),
        ("file_exists", "file"),
    ]:
        for value in as_list(expect.get(field), field=f"expect.{field}"):
            if not isinstance(value, str) or not value:
                raise RecordingError(f"expect.{field} values must be non-empty strings")
            args.extend([gate_name, value])
    return args


def timeline_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".timeline.jsonl")


def failure_path_for_cast(cast_path: Path) -> Path:
    return cast_path.with_suffix(".failure.json")


def read_timeline_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(
                f"invalid timeline event in {path}:{line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise RecordingError(
                f"timeline event must be a mapping: {path}:{line_number}"
            )
        events.append(event)
    return events


def read_failure_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecordingError(f"invalid failure report: {path}") from exc
    if not isinstance(report, dict):
        raise RecordingError(f"failure report must be a mapping: {path}")
    return report


def check_intervals_from_timeline(
    events: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    starts: dict[str, float] = {}
    intervals: list[tuple[float, float]] = []
    for event in events:
        phase = event.get("phase")
        check_id = event.get("check_id")
        timestamp = event.get("time")
        if (
            phase not in {"check_start", "check_end"}
            or not isinstance(check_id, str)
            or not isinstance(timestamp, (int, float))
        ):
            continue
        if phase == "check_start":
            starts[check_id] = float(timestamp)
            continue
        start = starts.pop(check_id, None)
        if start is not None and timestamp > start:
            intervals.append((start, float(timestamp)))
    return merge_intervals(intervals)


def unfinished_check_from_timeline(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    starts: dict[str, dict[str, Any]] = {}
    for event in events:
        phase = event.get("phase")
        check_id = event.get("check_id")
        if phase not in {"check_start", "check_end"} or not isinstance(
            check_id, str
        ):
            continue
        if phase == "check_start":
            starts[check_id] = event
        else:
            starts.pop(check_id, None)
    if not starts:
        return None
    return list(starts.values())[-1]


def format_recording_failure(
    *,
    returncode: int,
    command: list[str],
    cast_path: Path,
    timeline_path: Path,
    failure_path: Path,
) -> str:
    lines = [
        f"asciinema recording failed with exit code {returncode}",
        f"cast: {cast_path}",
    ]
    report = read_failure_report(failure_path)
    if report is not None:
        kind = report.get("kind", "step")
        name = report.get("name")
        step_id = report.get("id")
        label = str(kind)
        if isinstance(name, str) and name:
            label = f"{label} {name!r}"
        if isinstance(step_id, str) and step_id:
            label = f"{label} ({step_id})"
        lines.append(f"session failed during {label}")
        message = report.get("message")
        if isinstance(message, str) and message:
            lines.append(f"reason: {message}")
        output = report.get("output")
        if isinstance(output, str) and output:
            lines.append("--- captured output ---")
            lines.append(output.rstrip())
            lines.append("--- end captured output ---")
        return "\n".join(lines)

    events = read_timeline_events(timeline_path)
    unfinished = unfinished_check_from_timeline(events)
    if unfinished is not None:
        check = unfinished.get("check")
        check_id = unfinished.get("check_id")
        beat = unfinished.get("beat")
        if isinstance(check, str) and check:
            lines.append(f"last hidden step started: {check}")
        if isinstance(beat, str) and isinstance(check_id, str):
            lines.append(f"timeline marker: beat={beat} check_id={check_id}")
        lines.append(
            "no failure sidecar was written; the session may have exited before "
            "the recorder could capture hidden-step output"
        )
    else:
        lines.append("no session failure sidecar was written")
    lines.append("command: " + " ".join(shlex.quote(part) for part in command))
    return "\n".join(lines)


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
    return merged


def removed_time_before(
    timestamp: float, intervals: list[tuple[float, float]]
) -> float:
    removed = 0.0
    for start, end in intervals:
        if timestamp <= start:
            break
        removed += max(0.0, min(timestamp, end) - start)
    return removed


def timestamp_in_interval(
    timestamp: float, intervals: list[tuple[float, float]]
) -> bool:
    return any(start < timestamp < end for start, end in intervals)


def strip_cast_intervals(cast_path: Path, intervals: list[tuple[float, float]]) -> None:
    intervals = merge_intervals(intervals)
    if not intervals:
        return

    raw_lines = cast_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise RecordingError(f"cast file is empty: {cast_path}")

    output_lines = [raw_lines[0]]
    absolute_time = 0.0
    previous_adjusted_time = 0.0
    for line_number, line in enumerate(raw_lines[1:], 2):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(
                f"invalid asciinema event in {cast_path}:{line_number}"
            ) from exc
        if (
            not isinstance(event, list)
            or len(event) != 3
            or not isinstance(event[0], (int, float))
        ):
            output_lines.append(line)
            continue
        absolute_time += float(event[0])
        if (
            event[1] == "o"
            and isinstance(event[2], str)
            and event[2]
            and timestamp_in_interval(absolute_time, intervals)
        ):
            raise RecordingError(
                "visible cast output overlaps hidden check interval at "
                f"{absolute_time:.3f}s in {cast_path}"
            )
        adjusted_time = absolute_time - removed_time_before(absolute_time, intervals)
        event[0] = round(max(0.0, adjusted_time - previous_adjusted_time), 6)
        previous_adjusted_time = adjusted_time
        output_lines.append(json.dumps(event, separators=(",", ":")))

    cast_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def normalize_cast_header(cast_path: Path, spec: dict[str, Any]) -> None:
    raw_lines = cast_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise RecordingError(f"cast file is empty: {cast_path}")
    try:
        header = json.loads(raw_lines[0])
    except json.JSONDecodeError as exc:
        raise RecordingError(f"invalid asciinema header in {cast_path}") from exc
    if not isinstance(header, dict):
        raise RecordingError(f"asciinema header must be a mapping: {cast_path}")

    header["command"] = f"media/tools/record.py --session {require_string(spec, 'id')}"
    header.pop("env", None)

    capture = as_mapping(spec.get("capture"), field="capture")
    if capture.get("idle_time_limit") is None:
        header.pop("idle_time_limit", None)
    else:
        header["idle_time_limit"] = capture["idle_time_limit"]

    output_lines = [json.dumps(header, separators=(",", ":"))]
    output_lines.extend(raw_lines[1:])
    cast_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def render_session_script(spec: dict[str, Any]) -> str:
    environment = as_mapping(spec.get("environment"), field="environment")
    working_directory = environment.get("working_directory", ".")
    if not isinstance(working_directory, str):
        raise RecordingError("environment.working_directory must be a string")
    workdir = relative_path(working_directory)

    path_prepend = as_list(
        environment.get("path_prepend"), field="environment.path_prepend"
    )
    path_entries = [str(Path(sys.executable).parent)]
    path_entries.extend(str(relative_path(str(entry))) for entry in path_prepend)
    path_prefix = ":".join(path_entries)
    style = as_mapping(spec.get("style"), field="style")
    color = bool(style.get("color", True))
    typing = bool(style.get("typing", True))
    typing_min_delay = require_positive_number(style, "typing_min_delay", 0.012)
    typing_max_delay = require_positive_number(style, "typing_max_delay", 0.045)
    if typing_min_delay > typing_max_delay:
        raise RecordingError(
            "style.typing_min_delay must be less than or equal to style.typing_max_delay"
        )
    typing_space_delay = require_non_negative_number(style, "typing_space_delay", 0.025)
    typing_punctuation_delay = require_non_negative_number(
        style, "typing_punctuation_delay", 0.05
    )
    typing_newline_delay = require_non_negative_number(
        style, "typing_newline_delay", 0.16
    )
    typing_seed = require_integer(style, "typing_seed", 17)
    capture = as_mapping(spec.get("capture"), field="capture")
    baseline_compressed = bool(capture.get("baseline_compressed", False))
    session_typing = typing and not baseline_compressed

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shell_quote(workdir)}",
    ]
    if path_prefix:
        lines.append(f'export PATH={shell_quote(path_prefix)}:"$PATH"')
    lines.extend(
        [
            f"recording_color={shell_quote(1 if color else 0)}",
            f"recording_python={shell_quote(sys.executable)}",
            f"recording_baseline_compressed={shell_quote(1 if baseline_compressed else 0)}",
            f"recording_typing={shell_quote(1 if session_typing else 0)}",
            f"recording_typing_min_delay={shell_quote(typing_min_delay)}",
            f"recording_typing_max_delay={shell_quote(typing_max_delay)}",
            f"recording_typing_space_delay={shell_quote(typing_space_delay)}",
            f"recording_typing_punctuation_delay={shell_quote(typing_punctuation_delay)}",
            f"recording_typing_newline_delay={shell_quote(typing_newline_delay)}",
            f"recording_typing_seed={shell_quote(typing_seed)}",
            "export recording_typing_min_delay",
            "export recording_typing_max_delay",
            "export recording_typing_space_delay",
            "export recording_typing_punctuation_delay",
            "export recording_typing_newline_delay",
            "export recording_typing_seed",
            'recording_timeline_path="${ARBITER_CINEMA_TIMELINE:-}"',
            'recording_failure_path="${ARBITER_CINEMA_FAILURE:-}"',
            'recording_start_epoch="$("$recording_python" - <<\'PY\'',
            "import time",
            "print(time.time())",
            "PY",
            ')"',
            'if [[ -n "$recording_timeline_path" ]]; then',
            '  : > "$recording_timeline_path"',
            "fi",
            'recording_tmp="$(mktemp -d)"',
            'cleanup_paths=("$recording_tmp")',
            "cleanup_pids=()",
            "cleanup() {",
            "  local pid",
            '  for pid in "${cleanup_pids[@]}"; do',
            '    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then',
            '      kill "$pid" 2>/dev/null || true',
            '      wait "$pid" 2>/dev/null || true',
            "    fi",
            "  done",
            "  local path",
            '  for path in "${cleanup_paths[@]}"; do',
            '    [[ -n "$path" ]] && rm -rf "$path"',
            "  done",
            "}",
            "trap cleanup EXIT",
            "",
            "record_failure() {",
            '  local kind="$1"',
            '  local step_id="$2"',
            '  local step_name="$3"',
            '  local message="$4"',
            '  local output_path="${5:-}"',
            '  [[ -z "$recording_failure_path" ]] && return',
            '  "$recording_python" - "$recording_failure_path" "$kind" "$step_id" "$step_name" "$message" "$output_path" <<\'PY\'',
            "import json",
            "import sys",
            "",
            "path, kind, step_id, step_name, message, output_path = sys.argv[1:]",
            "output = ''",
            "if output_path:",
            "    try:",
            "        with open(output_path, 'r', encoding='utf-8', errors='replace') as handle:",
            "            output = handle.read()",
            "    except OSError as exc:",
            "        output = f'<unable to read captured output: {exc}>'",
            "max_chars = 12000",
            "truncated = len(output) > max_chars",
            "if truncated:",
            "    output = output[-max_chars:]",
            "report = {",
            "    'kind': kind,",
            "    'id': step_id,",
            "    'name': step_name,",
            "    'message': message,",
            "    'output': output,",
            "    'output_truncated': truncated,",
            "}",
            "with open(path, 'w', encoding='utf-8') as handle:",
            "    json.dump(report, handle, indent=2, sort_keys=True)",
            "    handle.write('\\n')",
            "PY",
            "}",
            "",
            "fail_gate() {",
            '  local action_id="$1"',
            '  local message="$2"',
            '  local output_path="${3:-}"',
            '  record_failure action "$action_id" "$action_id" "$message" "$output_path"',
            '  if [[ "$recording_color" == 1 ]]; then',
            "    printf '\\n\\033[31;1mrecording gate failed:\\033[0m %s %s\\n' \"$action_id\" \"$message\" >&2",
            "  else",
            "    printf '\\nrecording gate failed: %s %s\\n' \"$action_id\" \"$message\" >&2",
            "  fi",
            "  exit 1",
            "}",
            "",
            "fail_check() {",
            '  local check_id="$1"',
            '  local check_name="$2"',
            '  local message="$3"',
            '  local output_path="$4"',
            '  record_failure check "$check_id" "$check_name" "$message" "$output_path"',
            '  if [[ "$recording_color" == 1 ]]; then',
            "    printf '\\n\\033[31;1mrecording check failed:\\033[0m %s %s\\n' \"$check_name\" \"$message\" >&2",
            "  else",
            "    printf '\\nrecording check failed: %s %s\\n' \"$check_name\" \"$message\" >&2",
            "  fi",
            '  if [[ -s "$output_path" ]]; then',
            "    printf -- '--- check output ---\\n' >&2",
            '    cat "$output_path" >&2',
            "    printf -- '\\n--- end check output ---\\n' >&2",
            "  fi",
            "  exit 1",
            "}",
            "",
            "timeline_event() {",
            '  local phase="$1"',
            '  local beat_id="$2"',
            '  local check_id="$3"',
            '  local check_name="$4"',
            "  shift 4",
            '  [[ -z "$recording_timeline_path" ]] && return',
            '  "$recording_python" - "$recording_timeline_path" "$recording_start_epoch" "$phase" "$beat_id" "$check_id" "$check_name" "$@" <<\'PY\'',
            "import json",
            "import re",
            "import sys",
            "import time",
            "",
            "path, start, phase, beat_id, check_id, check_name, *pairs = sys.argv[1:]",
            "event = {",
            "    'time': time.time() - float(start),",
            "    'phase': phase,",
            "}",
            "if beat_id:",
            "    event['beat'] = beat_id",
            "if check_id:",
            "    event['check_id'] = check_id",
            "if check_name:",
            "    event['check'] = check_name",
            "if len(pairs) % 2:",
            "    raise SystemExit('timeline key/value arguments must be paired')",
            "for index in range(0, len(pairs), 2):",
            "    key = pairs[index]",
            "    value = pairs[index + 1]",
            "    if value.startswith(('{', '[', '\"')) or re.fullmatch(r'-?\\d+(\\.\\d+)?', value):",
            "        try:",
            "            event[key] = json.loads(value)",
            "        except json.JSONDecodeError:",
            "            event[key] = value",
            "    else:",
            "        event[key] = value",
            "with open(path, 'a', encoding='utf-8') as handle:",
            "    handle.write(json.dumps(event, sort_keys=True) + '\\n')",
            "PY",
            "}",
            "",
            "print_caption() {",
            '  if [[ "$recording_color" == 1 ]]; then',
            "    printf '\\n\\033[36;1m# %s\\033[0m\\n\\n' \"$1\"",
            "  else",
            "    printf '\\n# %s\\n\\n' \"$1\"",
            "  fi",
            "}",
            "",
            "type_text() {",
            '  local text="$1"',
            '  if [[ "$recording_typing" != 1 ]]; then',
            '    printf "%s" "$text"',
            "    return",
            "  fi",
            '  "$recording_python" - "$text" <<\'PY\'',
            "import hashlib",
            "import os",
            "import random",
            "import sys",
            "import time",
            "",
            "text = sys.argv[1]",
            "minimum = float(os.environ['recording_typing_min_delay'])",
            "maximum = float(os.environ['recording_typing_max_delay'])",
            "space = float(os.environ['recording_typing_space_delay'])",
            "punctuation = float(os.environ['recording_typing_punctuation_delay'])",
            "newline = float(os.environ['recording_typing_newline_delay'])",
            "seed = int(os.environ['recording_typing_seed'])",
            "digest = hashlib.sha256(text.encode('utf-8')).digest()",
            "text_seed = int.from_bytes(digest[:8], 'big')",
            "rng = random.Random(seed ^ text_seed)",
            "",
            "for index, char in enumerate(text):",
            "    sys.stdout.write(char)",
            "    sys.stdout.flush()",
            "    if index == len(text) - 1:",
            "        continue",
            "    delay = rng.uniform(minimum, maximum)",
            "    if char == '\\n':",
            "        delay += newline + rng.uniform(0.0, newline / 2)",
            "    elif char.isspace():",
            "        delay += rng.uniform(0.0, space)",
            "    elif char in '|;&':",
            "        delay += punctuation + rng.uniform(0.0, punctuation)",
            "    elif char == '\\\\':",
            "        delay += newline / 2",
            "    elif char in ',.:=/\"\\'{}[]()':",
            "        delay += rng.uniform(0.0, punctuation)",
            "    if char in ' -_/' and rng.random() < 0.08:",
            "        delay += rng.uniform(0.04, 0.12)",
            "    time.sleep(delay)",
            "PY",
            "}",
            "",
            "print_command_line() {",
            '  local line="$1"',
            '  local continuation="$2"',
            '  if [[ "$recording_color" == 1 ]]; then',
            '    if [[ "$continuation" == 1 ]]; then',
            "      printf '  \\033[1m'",
            "    else",
            "      printf '\\033[32;1m$\\033[0m \\033[1m'",
            "    fi",
            '    type_text "$line"',
            "    printf '\\033[0m\\n'",
            "  else",
            '    if [[ "$continuation" == 1 ]]; then',
            "      printf '  '",
            "    else",
            "      printf '$ '",
            "    fi",
            '    type_text "$line"',
            "    printf '\\n'",
            "  fi",
            "}",
            "",
            "print_command() {",
            '  local command="$1"',
            "  local line",
            "  local continuation=0",
            '  while IFS= read -r line || [[ -n "$line" ]]; do',
            '    if [[ -z "$line" ]]; then',
            "      printf '\\n'",
            "      continuation=0",
            "      continue",
            "    fi",
            '    if [[ "$continuation" == 1 || "$line" =~ ^[[:space:]] ]]; then',
            '      print_command_line "$line" 1',
            "    else",
            '      print_command_line "$line" 0',
            "    fi",
            '    if [[ "$line" == *\\\\ ]]; then',
            "      continuation=1",
            "    else",
            "      continuation=0",
            "    fi",
            '  done <<< "$command"',
            "}",
            "",
            "split_commands() {",
            '  local text="$1"',
            '  local target_name="$2"',
            "  local line",
            "  local chunk=''",
            '  local -n target="$target_name"',
            "  target=()",
            '  while IFS= read -r line || [[ -n "$line" ]]; do',
            '    if [[ -z "$line" && -z "$chunk" ]]; then',
            "      continue",
            "    fi",
            '    if [[ -n "$chunk" ]]; then',
            "      chunk+=$'\\n'",
            "    fi",
            '    chunk+="$line"',
            '    if [[ "$line" == *\\\\ ]]; then',
            "      continue",
            "    fi",
            '    target+=("$chunk")',
            "    chunk=''",
            '  done <<< "$text"',
            '  if [[ -n "$chunk" ]]; then',
            '    target+=("$chunk")',
            "  fi",
            "}",
            "",
            "run_visible_command_chunk() {",
            '  local action_id="$1"',
            '  local output_path="$2"',
            '  local command_chunk="$3"',
            '  local chunk_id="$4"',
            '  local pipe_path="$recording_tmp/${action_id}.${chunk_id}.pipe"',
            '  rm -f "$pipe_path"',
            '  mkfifo "$pipe_path"',
            '  tee -a "$output_path" <"$pipe_path" &',
            "  local tee_pid=$!",
            '  eval "$command_chunk" >"$pipe_path" 2>&1',
            "  local status=$?",
            '  wait "$tee_pid" 2>/dev/null || true',
            '  rm -f "$pipe_path"',
            '  return "$status"',
            "}",
            "",
            "free_port() {",
            "  \"$recording_python\" - <<'PY'",
            "import socket",
            "with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:",
            "    sock.bind(('127.0.0.1', 0))",
            "    print(sock.getsockname()[1])",
            "PY",
            "}",
            "",
            "expand_path() {",
            '  local raw="$1"',
            '  eval "printf \'%s\' \\"$raw\\""',
            "}",
            "",
            "run_action() {",
            '  local beat_id="$1"',
            '  local action_id="$2"',
            '  local display_command="$3"',
            '  local command="$4"',
            "  shift 4",
            '  local output_path="$recording_tmp/${action_id}.out"',
            '  : > "$output_path"',
            "  local display_chunks=()",
            "  local command_chunks=()",
            '  split_commands "$display_command" display_chunks',
            '  split_commands "$command" command_chunks',
            '  timeline_event action_start "$beat_id" "" "" action_id "$action_id"',
            "  set +e",
            "  local status=0",
            '  if [[ ${#display_chunks[@]} -gt 0 && ${#display_chunks[@]} -eq ${#command_chunks[@]} ]]; then',
            "    local index",
            '    for index in "${!command_chunks[@]}"; do',
            '      timeline_event command_prompt_start "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${display_chunks[$index]}"',
            '      print_command "${display_chunks[$index]}"',
            '      timeline_event command_prompt_end "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${display_chunks[$index]}"',
            '      timeline_event command_run_start "$beat_id" "" "" action_id "$action_id" chunk_index "$index" command "${command_chunks[$index]}"',
            '      run_visible_command_chunk "$action_id" "$output_path" "${command_chunks[$index]}" "$index"',
            "      status=$?",
            '      timeline_event command_run_end "$beat_id" "" "" action_id "$action_id" chunk_index "$index" status "$status"',
            '      [[ "$status" -eq 0 ]] || break',
            "    done",
            "  else",
            '    timeline_event command_prompt_start "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$display_command"',
            '    print_command "$display_command"',
            '    timeline_event command_prompt_end "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$display_command"',
            '    timeline_event command_run_start "$beat_id" "" "" action_id "$action_id" chunk_index fallback command "$command"',
            '    run_visible_command_chunk "$action_id" "$output_path" "$command" fallback',
            "    status=$?",
            '    timeline_event command_run_end "$beat_id" "" "" action_id "$action_id" chunk_index fallback status "$status"',
            "  fi",
            "  set -e",
            "  local expected_exit=0",
            "  local gate_args=(\"$@\")",
            "  local gate",
            "  local value",
            "  local gate_index",
            '  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do',
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    [[ "$gate" == exit ]] && expected_exit="$value"',
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_gate "$action_id" "exited $status, expected $expected_exit" "$output_path"',
            '  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do',
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    case "$gate" in',
            "      exit)",
            "        ;;",
            "      contains)",
            '        grep -F -- "$value" "$output_path" >/dev/null || fail_gate "$action_id" "missing text: $value" "$output_path"',
            "        ;;",
            "      regex)",
            '        grep -E -- "$value" "$output_path" >/dev/null || fail_gate "$action_id" "missing regex: $value" "$output_path"',
            "        ;;",
            "      file)",
            "        local expanded",
            '        expanded="$(expand_path "$value")"',
            '        [[ -e "$expanded" ]] || fail_gate "$action_id" "missing file: $expanded" "$output_path"',
            "        ;;",
            "      *)",
            '        fail_gate "$action_id" "unknown gate: $gate" "$output_path"',
            "        ;;",
            "    esac",
            "  done",
            '  timeline_event action_end "$beat_id" "" "" action_id "$action_id" status "$status"',
            "  printf '\\n'",
            "}",
            "",
            "run_check() {",
            '  local beat_id="$1"',
            '  local check_id="$2"',
            '  local check_name="$3"',
            '  local command="$4"',
            "  shift 4",
            '  local output_path="$recording_tmp/${check_id}.out"',
            '  timeline_event check_start "$beat_id" "$check_id" "$check_name"',
            "  set +e",
            '  eval "$command" >"$output_path" 2>&1',
            "  local status=$?",
            "  set -e",
            "  local expected_exit=0",
            "  local gate_args=(\"$@\")",
            "  local gate",
            "  local value",
            "  local gate_index",
            '  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do',
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    [[ "$gate" == exit ]] && expected_exit="$value"',
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_check "$check_id" "$check_name" "exited $status, expected $expected_exit" "$output_path" "$stderr_path"',
            '  for ((gate_index = 0; gate_index < ${#gate_args[@]}; gate_index += 2)); do',
            '    gate="${gate_args[$gate_index]}"',
            '    value="${gate_args[$((gate_index + 1))]}"',
            '    case "$gate" in',
            "      exit)",
            "        ;;",
            "      contains)",
            '        grep -F -- "$value" "$output_path" >/dev/null || fail_check "$check_id" "$check_name" "missing text: $value" "$output_path"',
            "        ;;",
            "      regex)",
            '        grep -E -- "$value" "$output_path" >/dev/null || fail_check "$check_id" "$check_name" "missing regex: $value" "$output_path"',
            "        ;;",
            "      file)",
            "        local expanded",
            '        expanded="$(expand_path "$value")"',
            '        [[ -e "$expanded" ]] || fail_check "$check_id" "$check_name" "missing file: $expanded" "$output_path"',
            "        ;;",
            "      *)",
            '        fail_check "$check_id" "$check_name" "unknown gate: $gate" "$output_path"',
            "        ;;",
            "    esac",
            "  done",
            '  [[ "$status" -eq "$expected_exit" ]] || fail_check "$check_id" "$check_name" "exited $status, expected $expected_exit" "$output_path"',
            '  timeline_event check_end "$beat_id" "$check_id" "$check_name"',
            "}",
            "",
            "hold() {",
            '  local beat_id="$1"',
            '  local seconds="$2"',
            '  timeline_event hold_start "$beat_id" "" "" seconds "$seconds"',
            '  if [[ "$recording_baseline_compressed" != 1 ]]; then',
            '    sleep "$seconds"',
            "  fi",
            '  timeline_event hold_end "$beat_id" "" "" seconds "$seconds"',
            "}",
            "",
        ]
    )

    setup = as_list(spec.get("setup"), field="setup")
    for index, step in enumerate(setup, start=1):
        command = require_string(step, "run")
        check_name = step.get("name", f"setup step {index}")
        if not isinstance(check_name, str) or not check_name:
            raise RecordingError(f"setup.{index}.name must be a non-empty string")
        expect = as_mapping(step.get("expect"), field=f"setup.{index}.expect")
        gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
        lines.append(
            "run_check "
            f"{shell_quote('__setup__')} "
            f"{shell_quote(f'setup_check_{index}')} "
            f"{shell_quote(check_name)} "
            f"{shell_quote(command)} " + " ".join(gate_args)
        )
    if setup:
        lines.append("")

    for beat in as_list(spec.get("beats"), field="beats"):
        beat_id = require_string(beat, "id")
        safe_beat_id = re.sub(r"[^A-Za-z0-9_]", "_", beat_id)
        caption = beat.get("caption")
        lines.append(f"timeline_event beat_start {shell_quote(beat_id)} '' ''")
        if isinstance(caption, str) and caption:
            lines.append(
                f"timeline_event caption_start {shell_quote(beat_id)} '' '' "
                f"caption {shell_quote(caption)}"
            )
            lines.append(f"print_caption {shell_quote(caption)}")
            lines.append(
                f"timeline_event caption_end {shell_quote(beat_id)} '' '' "
                f"caption {shell_quote(caption)}"
            )
        actions = as_list(beat.get("actions"), field=f"beats.{beat_id}.actions")
        for index, action in enumerate(actions, start=1):
            command = require_string(action, "run")
            display_command = action.get("display", command)
            if not isinstance(display_command, str) or not display_command:
                raise RecordingError(
                    f"beats.{beat_id}.actions.{index}.display must be a non-empty string"
                )
            expect = as_mapping(
                action.get("expect"), field=f"beats.{beat_id}.actions.{index}.expect"
            )
            gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
            action_id = f"{safe_beat_id}_{index}"
            lines.append(
                "run_action "
                f"{shell_quote(beat_id)} "
                f"{shell_quote(action_id)} "
                f"{shell_quote(display_command)} "
                f"{shell_quote(command)} " + " ".join(gate_args)
            )
        checks = as_list(beat.get("checks"), field=f"beats.{beat_id}.checks")
        for index, check in enumerate(checks, start=1):
            command = require_string(check, "run")
            check_name = check.get("name", f"{beat_id} check {index}")
            if not isinstance(check_name, str) or not check_name:
                raise RecordingError(
                    f"beats.{beat_id}.checks.{index}.name must be a non-empty string"
                )
            expect = as_mapping(
                check.get("expect"), field=f"beats.{beat_id}.checks.{index}.expect"
            )
            gate_args = [shell_quote(value) for value in shell_expect_args(expect)]
            check_id = f"{safe_beat_id}_check_{index}"
            lines.append(
                "run_check "
                f"{shell_quote(beat_id)} "
                f"{shell_quote(check_id)} "
                f"{shell_quote(check_name)} "
                f"{shell_quote(command)} " + " ".join(gate_args)
            )
        viewer_hold = beat.get("viewer_hold")
        if viewer_hold is not None:
            if not isinstance(viewer_hold, (int, float)):
                raise RecordingError(f"beat {beat_id} viewer_hold must be numeric")
            lines.append(f"hold {shell_quote(beat_id)} {shell_quote(viewer_hold)}")
        lines.append(f"timeline_event beat_end {shell_quote(beat_id)} '' ''")
        lines.append("")
    return "\n".join(lines)


def run_session(recording_id: str) -> int:
    spec = load_manifest(recording_id)
    validate_manifest(spec)
    check_required_commands(spec)
    script_text = render_session_script(spec)
    result = subprocess.run(
        ["bash"], input=script_text, cwd=REPO_ROOT, text=True, check=False
    )
    return result.returncode


def record(
    spec: dict[str, Any],
    *,
    dry_run: bool,
    check_only: bool,
    output_override: str | None,
    headed: bool = False,
) -> int:
    validate_manifest(spec)
    check_required_commands(spec)
    asciinema_version = check_asciinema()
    script_text = render_session_script(spec)
    if dry_run:
        print(script_text)
        return 0
    if check_only:
        print(f"ok: {spec['id']} ({asciinema_version})")
        return 0

    outputs = as_mapping(spec.get("outputs"), field="outputs")
    cast_path = relative_path(output_override or require_string(outputs, "cast"))
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path = timeline_path_for_cast(cast_path)
    failure_path = failure_path_for_cast(cast_path)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    if failure_path.exists():
        failure_path.unlink()

    capture = as_mapping(spec.get("capture"), field="capture")
    window_size = str(capture.get("window_size", "100x28"))
    idle_time_limit = capture.get("idle_time_limit")
    headless = bool(capture.get("headless", True)) and not headed

    session_python = Path(sys.executable)
    runner_command = " ".join(
        [
            "env",
            "ARBITER_CINEMA_TIMELINE=" + shlex.quote(str(timeline_path)),
            "ARBITER_CINEMA_FAILURE=" + shlex.quote(str(failure_path)),
            shlex.quote(str(session_python)),
            shlex.quote(str(Path(__file__).resolve())),
            "--session",
            shlex.quote(require_string(spec, "id")),
        ]
    )
    command = [
        "asciinema",
        "record",
        "--overwrite",
        "--return",
        "--window-size",
        window_size,
        "--title",
        require_string(spec, "title"),
    ]
    if idle_time_limit is not None:
        command.extend(["--idle-time-limit", str(idle_time_limit)])
    if headless:
        command.append("--headless")
    command.extend(["--command", runner_command, str(cast_path)])
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        raise RecordingError(
            format_recording_failure(
                returncode=result.returncode,
                command=command,
                cast_path=cast_path,
                timeline_path=timeline_path,
                failure_path=failure_path,
            )
        )
    intervals = check_intervals_from_timeline(read_timeline_events(timeline_path))
    strip_cast_intervals(cast_path, intervals)
    normalize_cast_header(cast_path, spec)
    print(f"wrote {cast_path}")
    return 0


def list_recordings() -> int:
    for path in sorted(RECORDINGS_DIR.glob("*.yaml")):
        print(path.stem)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recording", nargs="?", help="Recording id, for example install-and-bootstrap."
    )
    parser.add_argument(
        "--list", action="store_true", help="List available recording manifests."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the generated session script."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate tools and manifest without recording.",
    )
    parser.add_argument("--output", help="Override the manifest cast output path.")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the terminal session while recording, overriding capture.headless.",
    )
    parser.add_argument("--session", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.list:
            return list_recordings()
        if args.session:
            return run_session(args.session)
        if not args.recording:
            parser.error("recording id is required unless --list is used")
        spec = load_manifest(args.recording)
        return record(
            spec,
            dry_run=args.dry_run,
            check_only=args.check,
            output_override=args.output,
            headed=args.headed,
        )
    except RecordingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"error: command failed with exit code {exc.returncode}: {exc.cmd}",
            file=sys.stderr,
        )
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
