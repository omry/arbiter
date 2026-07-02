#!/usr/bin/env python3
"""Frontend CLI for the Arbiter media studio."""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import redirect_stdout
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import hydra
from omegaconf import DictConfig, OmegaConf

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import align_cast
import audio
import record
import retime_cast
import studio_config as studio_config_module
from studio_config import (
    CONFIG_DIR,
    StudioAction,
    StudioConfigError,
    StudioStep,
    container_from_hydra_cfg,
    list_recording_ids,
    load_configured_env_file,
    recording_spec_from_config,
)
from terminal_style import (
    ANSI_CYAN_BOLD,
    ANSI_DIM,
    ANSI_GREEN_BOLD,
    ANSI_RED_BOLD,
    ANSI_YELLOW_BOLD,
    color_enabled,
    color_text,
)


class StudioError(RuntimeError):
    pass


RECORDING_FINGERPRINT_VERSION = 2
FINGERPRINT_PRIVATE_SPEC_KEYS = {"_overrides", "_recording_id"}
CAPTURE_FINGERPRINT_IGNORED_KEYS = {
    "audio",
    "audio_metadata",
    "guide",
    "narration",
    "publish",
    "retimed_cast",
    "surfaces",
}


ToolRunner = Callable[[Any], int]


def text_output_enabled(cfg: DictConfig) -> bool:
    return OmegaConf.select(cfg, "output_format", default="text") != "json"


def status_line(
    status: str,
    message: str,
    *,
    color: str,
    detail: str | None = None,
) -> None:
    enabled = color_enabled()
    label = color_text(f"{status:<5}", color, enabled=enabled)
    line = f"{label} {message}"
    if detail:
        line += color_text(f" ({detail})", ANSI_DIM, enabled=enabled)
    print(line, flush=True)


def step_line(message: str) -> None:
    status_line("step", message, color=ANSI_CYAN_BOLD)


def skip_line(message: str, *, detail: str | None = None) -> None:
    status_line("skip", message, color=ANSI_YELLOW_BOLD, detail=detail)


def pass_line(message: str) -> None:
    status_line("pass", message, color=ANSI_GREEN_BOLD)


def fail_line(message: str) -> None:
    status_line("fail", message, color=ANSI_RED_BOLD)


def info_line(message: str) -> None:
    status_line("info", message, color=ANSI_CYAN_BOLD)


BUILD_STEPS = [
    {
        "action": "record",
        "kind": "compile",
        "description": "record a fast terminal baseline and timeline sidecar",
    },
    {
        "action": "audio_generate",
        "kind": "compile",
        "description": "generate or reuse cached TTS fragments for each beat",
    },
    {
        "action": "audio_publish",
        "kind": "link",
        "description": "concatenate voiceover and write audio timing metadata",
    },
    {
        "action": "retime",
        "kind": "optimize",
        "description": "create the watchable cast using terminal and audio timing",
    },
    {
        "action": "publish_surface",
        "kind": "link",
        "description": "embed the finished recording in the selected publish surface",
    },
    {
        "action": "align_check",
        "kind": "validate",
        "description": "verify visible captions and commands still match the script",
    },
]

PUBLIC_ACTIONS = [action.value for action in StudioAction]

RECORD_ACTIONS = {
    "record": "record",
    "record_check": "check",
    "record_dry_run": "dry_run",
    "dry_run": "dry_run",
    "session": "session",
    "list": "list",
    "runs": "runs",
    "play": "play",
    "inspect": "inspect",
    "output": "output",
}
AUDIO_ACTIONS = {
    "sync_narration": "sync_narration",
    "audio_check": "check",
    "audio_dry_run": "dry_run",
    "audio_generate": "generate",
    "audio_publish": "publish",
    "generate": "generate",
    "publish": "publish",
}
RETIME_ACTIONS = {
    "retime": "retime",
    "retime_check": "check",
}
ALIGN_ACTIONS = {
    "align": "align",
    "align_check": "check",
}


def cfg_with_step(cfg: DictConfig, step: str, **overrides: object) -> DictConfig:
    data = OmegaConf.to_container(cfg, resolve=False, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioError("composed Hydra config must be a mapping")
    data["step"] = step
    for key, value in overrides.items():
        data[key] = str(value) if isinstance(value, Path) else value
    return OmegaConf.create(data)


def run_step(
    label: str,
    runner: ToolRunner,
    cfg: DictConfig,
    step: str,
    *,
    quiet: bool = False,
    config_overrides: dict[str, object] | None = None,
) -> str:
    if text_output_enabled(cfg) and not quiet:
        step_line(label)
    captured = io.StringIO()
    try:
        if quiet:
            with redirect_stdout(captured):
                result = runner(cfg_with_step(cfg, step, **(config_overrides or {})))
        else:
            result = runner(cfg_with_step(cfg, step, **(config_overrides or {})))
    except BaseException:
        output = captured.getvalue()
        if output:
            print(output, end="")
        raise
    output = captured.getvalue()
    if result != 0:
        if output:
            print(output, end="")
        raise StudioError(f"{label} failed with exit code {result}")
    return output


def run_record_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    spec: dict[str, Any] | None = None
    config = container_from_hydra_cfg(cfg)
    output_format = config.get("output_format", "text")
    verbose = bool_config(config, "verbose")
    if action == "record":
        spec = recording_spec_from_config(config, recording_id=None, overrides=())
    run_step(
        label or action.replace("_", " "), record.run_tool_from_hydra_cfg, cfg, action
    )
    if spec is not None:
        fingerprint_path = write_recording_fingerprint(spec)
        if output_format != "json" and verbose:
            pass_line(f"wrote recording fingerprint: {display_path(fingerprint_path)}")


def run_build_record_action(cfg: DictConfig, spec: dict[str, Any]) -> None:
    config = container_from_hydra_cfg(cfg)
    output_format = config.get("output_format", "text")
    verbose = bool_config(config, "verbose")
    if not bool_config(config, "force"):
        reason = recording_skip_reason(spec)
        if reason is None:
            if output_format != "json":
                cast_path = retime_cast.cast_path_from_manifest(spec)
                skip_line(
                    "record baseline cast",
                    detail=f"{display_path(cast_path)} is fresh",
                )
            return
        if output_format != "json" and verbose:
            info_line(f"record baseline cast: {reason}")
    run_step("record baseline cast", record.run_tool_from_hydra_cfg, cfg, "record")


def run_audio_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(label or f"audio {action}", audio.run_tool_from_hydra_cfg, cfg, action)


def status_message_from_output(output: str, status: str) -> str | None:
    prefix = f"{status:<5} "
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def run_build_audio_actions(
    cfg: DictConfig,
    *,
    generate_needed: bool,
    publish_needed: bool,
) -> None:
    if not generate_needed and not publish_needed:
        return
    config = container_from_hydra_cfg(cfg)
    verbose = bool_config(config, "verbose")
    if verbose:
        if generate_needed:
            run_audio_action(cfg, "generate", "generate audio")
        if publish_needed:
            run_audio_action(cfg, "publish", "publish audio")
        return

    if text_output_enabled(cfg):
        step_line("audio")
    generate_output = ""
    publish_output = ""
    if generate_needed:
        generate_output = run_step(
            "audio",
            audio.run_tool_from_hydra_cfg,
            cfg,
            "generate",
            quiet=True,
        )
    if publish_needed:
        publish_output = run_step(
            "audio",
            audio.run_tool_from_hydra_cfg,
            cfg,
            "publish",
            quiet=True,
        )
    if text_output_enabled(cfg):
        message = status_message_from_output(generate_output, "pass")
        if message is None:
            message = status_message_from_output(publish_output, "pass")
        pass_line(message or "audio ready")


def audio_generate_needs_work(cfg: DictConfig) -> bool:
    config = container_from_hydra_cfg(cfg)
    if bool_config(config, "force"):
        return True
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    try:
        settings = audio.audio_settings(spec)
        if not settings.enabled:
            return True
        segments = audio.load_narration_segments(spec)
        recording_id = audio.require_string(spec, "_recording_id", field="recording")
        plan = audio.plan_audio(recording_id, segments, settings)
    except audio.AudioError:
        return True
    if audio.audio_items_requiring_synthesis(plan, settings):
        return True
    if audio.audio_items_requiring_materialization(plan, settings):
        return True
    timestamps = bool_config(config, "timestamps", default=True)
    return bool(
        timestamps and audio.timestamp_items_requiring_generation(plan, force=False)
    )


def audio_publish_needs_work(cfg: DictConfig) -> bool:
    config = container_from_hydra_cfg(cfg)
    if bool_config(config, "force"):
        return True
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    try:
        settings = audio.audio_settings(spec)
        if not settings.enabled:
            return True
        segments = audio.load_narration_segments(spec)
        recording_id = audio.require_string(spec, "_recording_id", field="recording")
        plan = audio.plan_audio(recording_id, segments, settings)
        published_audio = audio.output_audio_path(spec, recording_id, settings)
        published_metadata = audio.output_audio_metadata_path(spec, published_audio)
    except audio.AudioError:
        return True
    return not audio.published_audio_is_fresh(
        plan,
        published_audio,
        published_metadata,
    )


def retime_needs_work(cfg: DictConfig) -> bool:
    config = container_from_hydra_cfg(cfg)
    if bool_config(config, "force"):
        return True
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    try:
        paths = artifact_paths(spec)
        retime_cast.require_fresh_retimed_cast(
            cast_path=paths["cast"],
            timeline_path=paths["timeline"],
            output_path=paths["retimed_cast"],
            audio_metadata_path=paths["audio_metadata"],
        )
    except (StudioError, retime_cast.RetimeError):
        return True
    return False


def staged_retimed_cast_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.build-{os.getpid()}.tmp")


def run_retime_action(
    cfg: DictConfig,
    action: str,
    label: str | None = None,
    *,
    output: Path | None = None,
    quiet: bool = False,
) -> str:
    config_overrides = {"output": output} if output is not None else None
    return run_step(
        label or f"retime {action}",
        retime_cast.run_tool_from_hydra_cfg,
        cfg,
        action,
        quiet=quiet,
        config_overrides=config_overrides,
    )


def run_align_action(
    cfg: DictConfig,
    action: str,
    label: str | None = None,
    *,
    cast: Path | None = None,
) -> None:
    config_overrides = {"cast": cast} if cast is not None else None
    run_step(
        label or f"align {action}",
        align_cast.run_tool_from_hydra_cfg,
        cfg,
        action,
        config_overrides=config_overrides,
    )


def bool_config(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise StudioError(f"{key} must be a boolean")
    return value


def action_help() -> str:
    actions = ", ".join(PUBLIC_ACTIONS)
    return (
        f"user-facing actions: {actions}\n"
        "omit action for the default build; use dry_run=true to preview the build graph"
    )


def enum_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (StudioAction, StudioStep)):
        return value.value
    return None


def validate_action(value: object) -> str:
    if value is None:
        return StudioAction.build.value
    normalized = enum_value(value)
    if normalized is None:
        raise StudioError("action must be a string\n" + action_help())
    if not normalized:
        raise StudioError("action cannot be empty\n" + action_help())
    if normalized not in PUBLIC_ACTIONS:
        raise StudioError(f"unknown action: {normalized}\n" + action_help())
    return normalized


def validate_step(value: object) -> str | None:
    if value is None:
        return None
    normalized = enum_value(value)
    if normalized is None:
        raise StudioError("step must be a string")
    if not normalized:
        raise StudioError("step cannot be empty")
    step_values = [step.value for step in StudioStep]
    if normalized not in step_values:
        raise StudioError(
            "unknown internal step: "
            f"{normalized}\ninternal steps: {', '.join(step_values)}"
        )
    return normalized


def display_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return str(candidate.relative_to(retime_cast.REPO_ROOT))
    except ValueError:
        return str(candidate)


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def recording_id_from_value(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return optional_string(value.get("id"))
    return None


def print_available_recording_scripts(*, selected_required: bool) -> int:
    recording_ids = list_recording_ids()
    if selected_required:
        print("No recording script selected.")
    if recording_ids:
        print("Available recording scripts:")
        for recording_id in recording_ids:
            print(f"  {recording_id}")
        if selected_required:
            print()
            print(f"Run with: media/tools/studio recording={recording_ids[0]}")
    else:
        print("No recording scripts found in media/recording-scripts.")
    return 1 if selected_required else 0


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StudioError(f"{field} must be a mapping")
    return value


def artifact_paths(spec: dict[str, Any]) -> dict[str, Path]:
    recording_id = str(spec["_recording_id"])
    cast_path = retime_cast.cast_path_from_manifest(spec)
    timeline_path = retime_cast.timeline_path_for_cast(cast_path)
    retimed_path = retime_cast.output_path_from_manifest(spec, cast_path)
    settings = audio.audio_settings(spec)
    audio_path = audio.output_audio_path(spec, recording_id, settings)
    audio_metadata_path = audio.output_audio_metadata_path(spec, audio_path)
    narration_path = audio.narration_config_path(recording_id)
    audio_cache = settings.cache_dir / recording_id
    return {
        "cast": cast_path,
        "timeline": timeline_path,
        "retimed_cast": retimed_path,
        "audio": audio_path,
        "audio_metadata": audio_metadata_path,
        "narration_config": narration_path,
        "audio_cache": audio_cache,
    }


def recording_fingerprint_path(cast_path: Path) -> Path:
    return cast_path.with_suffix(".recording.json")


def clean_artifact_paths(spec: dict[str, Any]) -> dict[str, Path]:
    cast_path = retime_cast.cast_path_from_manifest(spec)
    return {
        "baseline_cast": cast_path,
        "timeline": retime_cast.timeline_path_for_cast(cast_path),
        "recording_fingerprint": recording_fingerprint_path(cast_path),
        "retimed_cast": retime_cast.output_path_from_manifest(spec, cast_path),
    }


def normalize_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not (
                str(key).startswith("_")
                and str(key) not in FINGERPRINT_PRIVATE_SPEC_KEYS
            )
        }
    if isinstance(value, list):
        return [normalize_fingerprint_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_fingerprint_value(item) for item in value]
    if isinstance(value, Path):
        return display_path(value)
    return value


def capture_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): capture_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in CAPTURE_FINGERPRINT_IGNORED_KEYS
            and not (
                str(key).startswith("_")
                and str(key) not in FINGERPRINT_PRIVATE_SPEC_KEYS
            )
        }
    if isinstance(value, list):
        return [capture_fingerprint_value(item) for item in value]
    if isinstance(value, tuple):
        return [capture_fingerprint_value(item) for item in value]
    if isinstance(value, Path):
        return display_path(value)
    return value


def collect_run_file_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            if key == "run_file" and isinstance(item, str) and item:
                paths.append(item)
            else:
                paths.extend(collect_run_file_values(item))
        return paths
    if isinstance(value, list):
        paths = []
        for item in value:
            paths.extend(collect_run_file_values(item))
        return paths
    return []


def fingerprint_dependency_paths(spec: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for value in collect_run_file_values(spec):
        paths.append(retime_cast.relative_path(value))
    paths.extend(
        [
            Path(__file__),
            Path(record.__file__),
            Path(studio_config_module.__file__),
        ]
    )
    return sorted(set(paths), key=lambda path: display_path(path) or str(path))


def file_fingerprint_entry(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": display_path(path)}
    if not path.exists():
        entry["missing"] = True
        return entry
    if not path.is_file():
        entry["type"] = "non-file"
        return entry
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entry


def recording_fingerprint_payload(spec: dict[str, Any]) -> dict[str, Any]:
    spec_payload = capture_fingerprint_value(spec)
    dependencies = [
        file_fingerprint_entry(path) for path in fingerprint_dependency_paths(spec)
    ]
    fingerprint_input = {
        "version": RECORDING_FINGERPRINT_VERSION,
        "spec": spec_payload,
        "dependencies": dependencies,
    }
    encoded = json.dumps(
        fingerprint_input, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return {
        "version": RECORDING_FINGERPRINT_VERSION,
        "recording": str(spec.get("_recording_id") or spec.get("id") or ""),
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "spec_sha256": hashlib.sha256(
            json.dumps(
                spec_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest(),
        "dependencies": dependencies,
    }


def dependency_missing(payload: dict[str, Any]) -> str | None:
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        if dependency.get("missing"):
            return str(dependency.get("path") or "unknown dependency")
    return None


def read_recording_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise StudioError(
            f"invalid recording fingerprint JSON: {display_path(path)}"
        ) from exc
    if not isinstance(payload, dict):
        raise StudioError(
            f"recording fingerprint must be a mapping: {display_path(path)}"
        )
    return payload


def write_recording_fingerprint(spec: dict[str, Any]) -> Path:
    cast_path = retime_cast.cast_path_from_manifest(spec)
    fingerprint_path = recording_fingerprint_path(cast_path)
    payload = recording_fingerprint_payload(spec)
    missing = dependency_missing(payload)
    if missing is not None:
        raise StudioError(f"recording fingerprint dependency is missing: {missing}")
    fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fingerprint_path


def recording_skip_reason(spec: dict[str, Any]) -> str | None:
    cast_path = retime_cast.cast_path_from_manifest(spec)
    paths = {
        "cast": cast_path,
        "timeline": retime_cast.timeline_path_for_cast(cast_path),
    }
    for name, path in paths.items():
        if not path.exists():
            return f"{name} is missing"
    fingerprint_path = recording_fingerprint_path(cast_path)
    try:
        stored = read_recording_fingerprint(fingerprint_path)
    except StudioError as exc:
        return str(exc)
    if stored is None:
        return "recording fingerprint is missing"
    current = recording_fingerprint_payload(spec)
    missing = dependency_missing(current)
    if missing is not None:
        return f"recording dependency is missing: {missing}"
    if stored.get("version") != RECORDING_FINGERPRINT_VERSION:
        return "recording fingerprint version changed"
    if stored.get("fingerprint") != current["fingerprint"]:
        return "recording fingerprint changed"
    return None


def publish_config(spec: dict[str, Any]) -> dict[str, Any]:
    publish = spec.get("publish")
    if publish is None:
        return {}
    return as_mapping(publish, field="publish")


def selected_surface_name(config: dict[str, Any], spec: dict[str, Any]) -> str | None:
    surface_override = config.get("surface")
    if surface_override is not None:
        if not isinstance(surface_override, str) or not surface_override:
            raise StudioError("surface must be a non-empty string or null")
        return surface_override
    publish = publish_config(spec)
    default = publish.get("default")
    if default is None:
        return None
    if not isinstance(default, str) or not default:
        raise StudioError("publish.default must be a non-empty string")
    return default


def selected_surface(
    config: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    surface_name = selected_surface_name(config, spec)
    if surface_name is None:
        return None
    publish = publish_config(spec)
    surfaces = as_mapping(publish.get("surfaces"), field="publish.surfaces")
    surface = surfaces.get(surface_name)
    if not isinstance(surface, dict):
        raise StudioError(f"publish surface not found: {surface_name}")
    return surface_name, surface


def build_plan(config: dict[str, Any]) -> dict[str, Any]:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    paths = artifact_paths(spec)
    manifest_path = optional_string(spec.get("_manifest_path"))
    script_path = optional_string(spec.get("script"))
    surface = selected_surface(config, spec)
    surface_info: dict[str, Any] | None = None
    if surface is not None:
        surface_name, surface_config = surface
        surface_info = {
            "name": surface_name,
            "type": optional_string(surface_config.get("type")),
            "file": display_path(optional_string(surface_config.get("file"))),
            "placeholder": optional_string(surface_config.get("placeholder")),
        }

    return {
        "recording": str(spec["_recording_id"]),
        "title": optional_string(spec.get("title")),
        "inputs": {
            "recording_script": display_path(script_path),
            "recording_source": display_path(manifest_path),
        },
        "outputs": {
            "baseline_cast": display_path(paths["cast"]),
            "timeline": display_path(paths["timeline"]),
            "recording_fingerprint": display_path(
                recording_fingerprint_path(paths["cast"])
            ),
            "audio_fragments": display_path(paths["audio_cache"] / "*.mp3"),
            "voiceover": display_path(paths["audio"]),
            "audio_metadata": display_path(paths["audio_metadata"]),
            "retimed_cast": display_path(paths["retimed_cast"]),
        },
        "surface": surface_info,
        "steps": BUILD_STEPS,
    }


def print_build_plan(plan: dict[str, Any]) -> None:
    title = plan.get("title") or plan["recording"]
    print(f"Build dry run: {title}")
    print()
    print("Inputs:")
    for name, value in plan["inputs"].items():
        print(f"  {name}: {value}")
    print()
    print("Outputs:")
    for name, value in plan["outputs"].items():
        print(f"  {name}: {value}")
    if plan.get("surface"):
        surface = plan["surface"]
        print()
        print("Publish surface:")
        print(f"  name: {surface['name']}")
        print(f"  type: {surface['type']}")
        print(f"  file: {surface['file']}")
        if surface.get("placeholder"):
            print(f"  placeholder: {surface['placeholder']}")
    print()
    print("Pipeline:")
    for index, step in enumerate(plan["steps"], 1):
        print(
            f"  {index}. {step['action']} " f"({step['kind']}): {step['description']}"
        )
    print()
    print("No commands were run.")


def run_build_dry_run(config: dict[str, Any]) -> int:
    plan = build_plan(config)
    if config.get("output_format") == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print_build_plan(plan)
    return 0


def clean_recording_outputs(config: dict[str, Any]) -> list[Path]:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    removed: list[Path] = []
    for path in clean_artifact_paths(spec).values():
        if not path.exists():
            continue
        if path.is_dir():
            raise StudioError(f"refusing to remove directory: {display_path(path)}")
        path.unlink()
        removed.append(path)
    return removed


def run_clean(config: dict[str, Any]) -> int:
    removed = clean_recording_outputs(config)
    removed_display = [display_path(path) for path in removed]
    if config.get("output_format") == "json":
        print(
            json.dumps(
                {
                    "removed": removed_display,
                    "retained": [
                        "audio",
                        "audio_metadata",
                        "audio_cache",
                        "recording_runs",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    step_line("clean recording outputs")
    if removed_display:
        for path in removed_display:
            pass_line(f"removed {path}")
    else:
        skip_line("nothing to clean")
    info_line("retained audio outputs, audio metadata, audio cache, and recording runs")
    return 0


def site_url(path: Path) -> str:
    static_root = retime_cast.REPO_ROOT / "website" / "static"
    try:
        return "/" + path.relative_to(static_root).as_posix()
    except ValueError:
        return display_path(path) or str(path)


def first_narration_text(spec: dict[str, Any], segment_id: str | None) -> str | None:
    if not segment_id:
        return None
    narration = spec.get("narration")
    if not isinstance(narration, dict):
        return None
    beats = narration.get("beats")
    if not isinstance(beats, list):
        return None
    for beat in beats:
        if not isinstance(beat, dict) or beat.get("id") != segment_id:
            continue
        text = beat.get("text")
        return text if isinstance(text, str) and text else None
    return None


def player_params(
    spec: dict[str, Any],
    surface: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, str]:
    title = optional_string(spec.get("title")) or str(spec["_recording_id"])
    intro_segment = optional_string(surface.get("intro_segment"))
    intro = optional_string(surface.get("intro")) or first_narration_text(
        spec,
        intro_segment,
    )
    params = {
        "title": title,
        "src": site_url(paths["retimed_cast"]),
        "audio": site_url(paths["audio"]),
        "audioMeta": site_url(paths["audio_metadata"]),
    }
    if intro:
        params["intro"] = intro
    if intro_segment:
        params["introSegment"] = intro_segment
    return params


def render_docusaurus_mdx(spec: dict[str, Any], surface: dict[str, Any]) -> str:
    component = optional_string(surface.get("component")) or "TerminalCast"
    params = player_params(spec, surface, artifact_paths(spec))
    lines = [f"<{component}"]
    for key, value in params.items():
        lines.append(f"  {key}={json.dumps(value)}")
    lines.append("/>")
    return "\n".join(lines)


def ensure_mdx_component_import(
    text: str,
    *,
    component: str,
    import_path: str,
) -> str:
    import_line = f"import {component} from {json.dumps(import_path)};"
    if import_line in text:
        return text
    if f"import {component} " in text:
        return text
    if text.startswith("---\n"):
        end_index = text.find("\n---\n", 4)
        if end_index >= 0:
            insert_index = end_index + len("\n---\n")
            return text[:insert_index] + "\n" + import_line + "\n" + text[insert_index:]
    return import_line + "\n\n" + text


def render_html_iframe(spec: dict[str, Any], surface: dict[str, Any]) -> str:
    params = player_params(spec, surface, artifact_paths(spec))
    iframe_params = {
        "cast": params["src"],
        "title": params["title"],
        "audio": params["audio"],
        "audioMeta": params["audioMeta"],
    }
    for key in ["intro", "introSegment"]:
        if key in params:
            iframe_params[key] = params[key]
    src = "/cast-player.html?" + urlencode(iframe_params)
    title = html.escape(params["title"], quote=True)
    return (
        f'<iframe title="{title}" src="{html.escape(src, quote=True)}" '
        'loading="lazy" allow="autoplay" allowfullscreen></iframe>'
    )


def render_standalone_html(spec: dict[str, Any], surface: dict[str, Any]) -> str:
    title = optional_string(spec.get("title")) or str(spec["_recording_id"])
    iframe = render_html_iframe(spec, surface)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"    <title>{html.escape(title)}</title>\n"
        "    <style>\n"
        "      body { margin: 0; background: #11131a; }\n"
        "      iframe { width: 100vw; height: 100vh; border: 0; display: block; }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        f"    {iframe}\n"
        "  </body>\n"
        "</html>\n"
    )


def replace_placeholder(text: str, placeholder: str, replacement: str) -> str:
    start = f"<!-- studio:{placeholder}:start -->"
    end = f"<!-- studio:{placeholder}:end -->"
    start_index = text.find(start)
    end_index = text.find(end)
    if start_index < 0 or end_index < 0 or end_index < start_index:
        raise StudioError(f"placeholder {placeholder!r} not found")
    return (
        text[: start_index + len(start)]
        + "\n"
        + replacement.rstrip()
        + "\n"
        + text[end_index:]
    )


def publish_surface(
    config: dict[str, Any],
    *,
    validation_retimed_cast: Path | None = None,
) -> Path | None:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    selected = selected_surface(config, spec)
    if selected is None:
        return None
    paths = artifact_paths(spec)
    try:
        retime_cast.require_fresh_retimed_cast(
            cast_path=paths["cast"],
            timeline_path=paths["timeline"],
            output_path=validation_retimed_cast or paths["retimed_cast"],
            audio_metadata_path=paths["audio_metadata"],
        )
    except retime_cast.RetimeError as exc:
        raise StudioError(str(exc)) from exc
    _surface_name, surface = selected
    surface_type = optional_string(surface.get("type"))
    file_name = optional_string(surface.get("file"))
    if not surface_type:
        raise StudioError("publish surface type must be a non-empty string")
    if not file_name:
        raise StudioError("publish surface file must be a non-empty string")
    path = retime_cast.relative_path(file_name)

    if surface_type == "docusaurus_mdx":
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError("docusaurus_mdx surfaces require a placeholder")
        original = path.read_text(encoding="utf-8")
        component = optional_string(surface.get("component")) or "TerminalCast"
        import_path = (
            optional_string(surface.get("component_import"))
            or f"@site/src/components/{component}"
        )
        rendered = render_docusaurus_mdx(spec, surface)
        updated = ensure_mdx_component_import(
            replace_placeholder(original, placeholder, rendered),
            component=component,
            import_path=import_path,
        )
        if updated == original:
            return None
        path.write_text(updated, encoding="utf-8")
        return path
    if surface_type == "plain_html":
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError("plain_html surfaces require a placeholder")
        original = path.read_text(encoding="utf-8")
        rendered = render_html_iframe(spec, surface)
        updated = replace_placeholder(original, placeholder, rendered)
        if updated == original:
            return None
        path.write_text(updated, encoding="utf-8")
        return path
    if surface_type == "standalone_html":
        rendered = render_standalone_html(spec, surface)
        if path.exists() and path.read_text(encoding="utf-8") == rendered:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return path
    raise StudioError(f"unsupported publish surface type: {surface_type}")


def run_publish_surface(
    cfg: DictConfig,
    *,
    validation_retimed_cast: Path | None = None,
) -> None:
    config = container_from_hydra_cfg(cfg)
    path = publish_surface(config, validation_retimed_cast=validation_retimed_cast)
    if path is not None and text_output_enabled(cfg):
        step_line("publish surface")
        pass_line(f"wrote publish surface: {display_path(path)}")


def run_play(cfg: DictConfig, config: dict[str, Any]) -> int:
    cast_override = config.get("cast")
    run_id = config.get("run_id")
    recording_id = recording_id_from_value(config.get("recording"))
    if cast_override is not None or run_id is not None or recording_id is None:
        run_record_action(cfg, "play", "play")
        return 0

    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    cast_path = artifact_paths(spec)["retimed_cast"]
    if not cast_path.exists():
        raise StudioError(
            f"retimed cast not found: {display_path(cast_path)}; "
            "run media/tools/studio action=build first"
        )
    if text_output_enabled(cfg):
        step_line(f"play retimed cast: {display_path(cast_path)}")
    record.check_asciinema()
    return subprocess.run(
        ["asciinema", "play", str(cast_path)],
        cwd=retime_cast.REPO_ROOT,
        check=False,
    ).returncode


def studio_tool_command(recording_id: str, *overrides: str) -> str:
    parts = ["media/tools/studio", f"recording={recording_id}", *overrides]
    return " ".join(shlex.quote(part) for part in parts)


def print_success_followups(cfg: DictConfig) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") == "json":
        return
    recording_value = OmegaConf.select(cfg, "recording")
    if isinstance(recording_value, DictConfig):
        recording_value = OmegaConf.to_container(recording_value, resolve=False)
    recording_id = recording_id_from_value(recording_value)
    if not isinstance(recording_id, str) or not recording_id:
        return
    print()
    print(color_text("next", ANSI_CYAN_BOLD) + " follow-up commands")
    print(
        "  "
        + color_text("play   ", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=play")
    )
    print(
        "  "
        + color_text("inspect", ANSI_GREEN_BOLD)
        + " "
        + studio_tool_command(recording_id, "action=inspect")
    )


def format_elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minute_remainder = divmod(minutes, 60)
    return f"{int(hours)}h {int(minute_remainder)}m {remainder:.1f}s"


def print_build_elapsed(cfg: DictConfig, seconds: float, *, success: bool) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") == "json":
        return
    if success:
        pass_line(f"build completed after {format_elapsed(seconds)}")
    else:
        fail_line(f"build failed after {format_elapsed(seconds)}")


def run_build(cfg: DictConfig) -> int:
    started = time.monotonic()
    success = False
    config = container_from_hydra_cfg(cfg)
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    staged_retimed: Path | None = None
    try:
        run_build_record_action(cfg, spec)
        generate_needed = audio_generate_needs_work(cfg)
        publish_needed = audio_publish_needs_work(cfg)
        run_build_audio_actions(
            cfg,
            generate_needed=generate_needed,
            publish_needed=publish_needed,
        )
        if retime_needs_work(cfg):
            retimed_path = artifact_paths(spec)["retimed_cast"]
            staged_retimed = staged_retimed_cast_path(retimed_path)
            if staged_retimed.exists():
                staged_retimed.unlink()
            verbose = bool_config(config, "verbose")
            if text_output_enabled(cfg) and not verbose:
                step_line("retime cast")
            run_retime_action(
                cfg,
                "retime",
                "retime cast",
                output=staged_retimed,
                quiet=not verbose,
            )
            if not staged_retimed.exists():
                raise StudioError(
                    "retime cast did not write expected output: "
                    f"{display_path(staged_retimed)}"
                )
            run_align_action(cfg, "check", "check alignment", cast=staged_retimed)
            run_publish_surface(cfg, validation_retimed_cast=staged_retimed)
            staged_retimed.replace(retimed_path)
            staged_retimed = None
            if text_output_enabled(cfg) and not verbose:
                pass_line(f"wrote retimed cast: {display_path(retimed_path)}")
        else:
            run_align_action(cfg, "check", "check alignment")
            run_publish_surface(cfg)
        fingerprint_path = write_recording_fingerprint(spec)
        if text_output_enabled(cfg) and bool_config(config, "verbose"):
            pass_line(f"wrote recording fingerprint: {display_path(fingerprint_path)}")
        success = True
    finally:
        if staged_retimed is not None and staged_retimed.exists():
            staged_retimed.unlink()
        print_build_elapsed(cfg, time.monotonic() - started, success=success)
    print_success_followups(cfg)
    return 0


def run_check(cfg: DictConfig) -> int:
    run_record_action(cfg, "check", "check recording")
    run_audio_action(cfg, "check", "check audio")
    run_retime_action(cfg, "check", "check retime")
    run_align_action(cfg, "check", "check alignment")
    return 0


def run_internal_step(cfg: DictConfig, config: dict[str, Any], step: str) -> int:
    if step in RECORD_ACTIONS:
        action = (
            "dry_run"
            if step == "record" and bool_config(config, "dry_run")
            else RECORD_ACTIONS[step]
        )
        run_record_action(cfg, action, step.replace("_", " "))
        return 0
    if step in AUDIO_ACTIONS:
        run_audio_action(cfg, AUDIO_ACTIONS[step], step.replace("_", " "))
        return 0
    if step in RETIME_ACTIONS:
        run_retime_action(cfg, RETIME_ACTIONS[step], step.replace("_", " "))
        return 0
    if step in ALIGN_ACTIONS:
        run_align_action(cfg, ALIGN_ACTIONS[step], step.replace("_", " "))
        return 0
    raise StudioError(f"unknown internal step: {step}")


def run_tool_from_hydra_cfg(cfg: DictConfig) -> int:
    try:
        config = container_from_hydra_cfg(cfg)
    except StudioConfigError as exc:
        raise StudioError(str(exc)) from exc
    action = validate_action(config.get("action", "build"))
    step = validate_step(config.get("step"))

    if step is None and action == "list":
        return print_available_recording_scripts(selected_required=False)

    recording_required = step is not None or action in {"build", "check", "clean"}
    if recording_required and recording_id_from_value(config.get("recording")) is None:
        return print_available_recording_scripts(selected_required=True)

    if step is None and action == "build" and bool_config(config, "dry_run"):
        return run_build_dry_run(config)

    if step is None and action == "clean":
        return run_clean(config)

    try:
        load_configured_env_file(config)
    except StudioConfigError as exc:
        raise StudioError(str(exc)) from exc

    if step is not None:
        return run_internal_step(cfg, config, step)

    if action == "build":
        return run_build(cfg)
    if action == "check":
        return run_check(cfg)
    if action == "play":
        return run_play(cfg, config)

    if action in RECORD_ACTIONS:
        run_record_action(cfg, RECORD_ACTIONS[action], str(action).replace("_", " "))
        return 0

    raise StudioError(f"unknown studio action: {action}")


@hydra.main(version_base=None, config_path=str(CONFIG_DIR), config_name="config")
def main(cfg: DictConfig) -> None:
    use_color = record.host_color_enabled(sys.stderr)
    try:
        raise SystemExit(run_tool_from_hydra_cfg(cfg))
    except record.RecordingInterrupted as exc:
        print(
            color_text(
                f"interrupted: {exc}",
                ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130) from exc
    except KeyboardInterrupt:
        print(
            color_text(
                "interrupted: studio run cancelled by user",
                ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as exc:
        print(
            color_text("error:", ANSI_RED_BOLD, enabled=use_color) + f" {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
