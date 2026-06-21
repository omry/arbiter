#!/usr/bin/env python3
"""Frontend CLI for the Arbiter media studio."""

from __future__ import annotations

import html
import json
import shlex
import sys
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


class StudioError(RuntimeError):
    pass


ToolRunner = Callable[[Any], int]


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


def cfg_with_step(cfg: DictConfig, step: str) -> DictConfig:
    data = OmegaConf.to_container(cfg, resolve=False, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioError("composed Hydra config must be a mapping")
    data["step"] = step
    return OmegaConf.create(data)


def run_step(label: str, runner: ToolRunner, cfg: DictConfig, step: str) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") != "json":
        print(f"::: studio: {label}")
    result = runner(cfg_with_step(cfg, step))
    if result != 0:
        raise StudioError(f"{label} failed with exit code {result}")


def run_record_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(
        label or action.replace("_", " "), record.run_tool_from_hydra_cfg, cfg, action
    )


def run_audio_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(label or f"audio {action}", audio.run_tool_from_hydra_cfg, cfg, action)


def run_retime_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(
        label or f"retime {action}", retime_cast.run_tool_from_hydra_cfg, cfg, action
    )


def run_align_action(cfg: DictConfig, action: str, label: str | None = None) -> None:
    run_step(
        label or f"align {action}", align_cast.run_tool_from_hydra_cfg, cfg, action
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


def publish_surface(config: dict[str, Any]) -> Path | None:
    spec = recording_spec_from_config(config, recording_id=None, overrides=())
    selected = selected_surface(config, spec)
    if selected is None:
        return None
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
        rendered = render_docusaurus_mdx(spec, surface)
        path.write_text(
            replace_placeholder(original, placeholder, rendered),
            encoding="utf-8",
        )
        return path
    if surface_type == "plain_html":
        placeholder = optional_string(surface.get("placeholder"))
        if not placeholder:
            raise StudioError("plain_html surfaces require a placeholder")
        original = path.read_text(encoding="utf-8")
        rendered = render_html_iframe(spec, surface)
        path.write_text(
            replace_placeholder(original, placeholder, rendered),
            encoding="utf-8",
        )
        return path
    if surface_type == "standalone_html":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_standalone_html(spec, surface), encoding="utf-8")
        return path
    raise StudioError(f"unsupported publish surface type: {surface_type}")


def run_publish_surface(cfg: DictConfig) -> None:
    if OmegaConf.select(cfg, "output_format", default="text") != "json":
        print("::: studio: publish surface")
    config = container_from_hydra_cfg(cfg)
    path = publish_surface(config)
    if path is not None and config.get("output_format") != "json":
        print(f"wrote {display_path(path)}")


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
    print("Follow-up commands:")
    print("  " + studio_tool_command(recording_id, "action=play"))
    print("  " + studio_tool_command(recording_id, "action=inspect"))
    print("  " + studio_tool_command(recording_id, "step=align"))


def run_build(cfg: DictConfig) -> int:
    run_record_action(cfg, "record", "record baseline cast")
    run_audio_action(cfg, "generate", "generate audio")
    run_audio_action(cfg, "publish", "publish audio")
    run_retime_action(cfg, "retime", "retime cast")
    run_publish_surface(cfg)
    print_success_followups(cfg)
    return 0


def run_check(cfg: DictConfig) -> int:
    run_record_action(cfg, "check", "check recording")
    run_audio_action(cfg, "check", "check audio")
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

    recording_required = step is not None or action in {"build", "check"}
    if recording_required and recording_id_from_value(config.get("recording")) is None:
        return print_available_recording_scripts(selected_required=True)

    if step is None and action == "build" and bool_config(config, "dry_run"):
        return run_build_dry_run(config)

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
            record.color_text(
                f"interrupted: {exc}",
                record.ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130) from exc
    except KeyboardInterrupt:
        print(
            record.color_text(
                "interrupted: studio run cancelled by user",
                record.ANSI_YELLOW_BOLD,
                enabled=use_color,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as exc:
        print(
            record.color_text("error:", record.ANSI_RED_BOLD, enabled=use_color)
            + f" {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
