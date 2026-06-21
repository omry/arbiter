#!/usr/bin/env python3
"""Compose Arbiter media studio configuration with Hydra."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from hydra import compose, initialize_config_dir
from hydra.core.config_store import ConfigStore
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "media" / "conf"
RECORDING_CONFIG_DIR = CONFIG_DIR / "recording"


class StudioConfigError(RuntimeError):
    pass


def normalize_studio_token(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def studio_run_dir(
    action: object,
    step: object,
    dry_run: object,
    recording_id: object,
    timestamp: object,
) -> str:
    action_text = normalize_studio_token(action) or "build"
    step_text = normalize_studio_token(step)
    recording_text = normalize_studio_token(recording_id) or "recording"
    timestamp_text = normalize_studio_token(timestamp)
    dry_run_enabled = str(dry_run).lower() == "true"
    is_recording_run = not dry_run_enabled and (
        step_text in {"record", "session"}
        or (not step_text and action_text in {"build", "record"})
    )
    if is_recording_run:
        return f"media/runs/{recording_text}/{timestamp_text}"
    job_kind = step_text or action_text
    return f"media/studio-runs/{job_kind}/{recording_text}/{timestamp_text}"


def register_resolvers() -> None:
    if not OmegaConf.has_resolver("studio_run_dir"):
        OmegaConf.register_new_resolver("studio_run_dir", studio_run_dir)


class StudioAction(str, Enum):
    build = "build"
    check = "check"
    play = "play"
    inspect = "inspect"
    output = "output"
    runs = "runs"
    list = "list"


class StudioStep(str, Enum):
    record = "record"
    record_check = "record_check"
    record_dry_run = "record_dry_run"
    session = "session"
    dry_run = "dry_run"
    sync_narration = "sync_narration"
    retime = "retime"
    retime_check = "retime_check"
    generate = "generate"
    publish = "publish"
    audio_check = "audio_check"
    audio_dry_run = "audio_dry_run"
    audio_generate = "audio_generate"
    audio_publish = "audio_publish"
    align = "align"
    align_check = "align_check"


@dataclass
class StudioConfig:
    action: StudioAction = StudioAction.build
    step: StudioStep | None = None
    output_format: str = "text"
    load_env_file: bool = True
    env_file: str | None = ".env"
    env_override: bool = False
    output: str | None = None
    cast: str | None = None
    timeline: str | None = None
    surface: str | None = None
    dry_run: bool = False
    headed: bool = False
    force: bool = False
    timestamps: bool = False
    allow_mismatch: bool = False
    run_id: str | None = None
    runs_since: str | None = None
    runs_limit: int | None = 10
    profile: dict[str, Any] = field(default_factory=dict)
    studio: dict[str, Any] = field(default_factory=dict)
    package_source: dict[str, Any] = field(default_factory=dict)
    narration: dict[str, Any] = field(default_factory=dict)
    publish: dict[str, Any] = field(default_factory=dict)
    recording: dict[str, Any] = field(default_factory=dict)


def register_studio_schema() -> None:
    ConfigStore.instance().store(name="studio_schema", node=StudioConfig)


register_resolvers()
register_studio_schema()


def list_recording_ids() -> list[str]:
    if not RECORDING_CONFIG_DIR.exists():
        return []
    return sorted(path.stem for path in RECORDING_CONFIG_DIR.glob("*.yaml"))


def normalize_hydra_override(override: str) -> str:
    if override.count("=") <= 1:
        return override
    key, value = override.split("=", 1)
    if value.startswith(("'", '"')):
        return override
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def compose_studio_config(
    recording_id: str | None,
    overrides: Sequence[str] = (),
) -> dict[str, Any]:
    if not CONFIG_DIR.exists():
        raise StudioConfigError(f"media config directory not found: {CONFIG_DIR}")

    hydra_overrides = [normalize_hydra_override(str(override)) for override in overrides]
    if recording_id is not None:
        hydra_overrides.insert(0, f"recording={recording_id}")
    try:
        with initialize_config_dir(
            version_base=None,
            config_dir=str(CONFIG_DIR),
        ):
            cfg = compose(config_name="config", overrides=hydra_overrides)
            data = OmegaConf.to_container(cfg, resolve=True, enum_to_str=True)
    except Exception as exc:
        details = f"recording {recording_id!r}" if recording_id else "default recording"
        raise StudioConfigError(f"failed to compose media config for {details}") from exc
    if not isinstance(data, dict):
        raise StudioConfigError("composed media config must be a mapping")
    return data


def container_from_hydra_cfg(cfg: DictConfig) -> dict[str, Any]:
    data = OmegaConf.to_container(cfg, resolve=True, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioConfigError("composed Hydra config must be a mapping")
    return data


def resolve_config_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def dotenv_entry(line: str, *, path: Path, line_number: int) -> tuple[str, str] | None:
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError as exc:
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: {exc}"
        ) from exc
    if not tokens:
        return None
    if tokens[0] == "export":
        tokens = tokens[1:]
    if len(tokens) != 1 or "=" not in tokens[0]:
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: expected KEY=VALUE"
        )
    key, value = tokens[0].split("=", 1)
    if not key.isidentifier():
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: invalid key {key!r}"
        )
    return key, value


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        entry = dotenv_entry(line, path=path, line_number=line_number)
        if entry is None:
            continue
        key, value = entry
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def load_configured_env_file(config: dict[str, Any]) -> dict[str, str]:
    enabled = config.get("load_env_file", True)
    if not isinstance(enabled, bool):
        raise StudioConfigError("load_env_file must be a boolean")
    if not enabled:
        return {}

    env_file = config.get("env_file", ".env")
    if env_file is None:
        return {}
    if not isinstance(env_file, str) or not env_file:
        raise StudioConfigError("env_file must be a non-empty string or null")

    override = config.get("env_override", False)
    if not isinstance(override, bool):
        raise StudioConfigError("env_override must be a boolean")

    return load_env_file(resolve_config_path(env_file), override=override)


def merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_mapping(existing, value)
        else:
            merged[key] = value
    return merged


def recording_spec_from_config(
    config: dict[str, Any],
    *,
    recording_id: str | None,
    overrides: Sequence[str],
    hydra_output_dir: str | None = None,
) -> dict[str, Any]:
    recording = config.get("recording")
    if not isinstance(recording, dict):
        raise StudioConfigError("composed media config must contain recording mapping")

    spec = dict(recording)
    for key in [
        "profile",
        "studio",
        "package_source",
        "requirements",
        "capture",
        "style",
        "outputs",
        "retime",
        "environment",
        "audio",
        "narration",
        "publish",
    ]:
        value = config.get(key)
        if not isinstance(value, dict):
            continue
        current = spec.get(key)
        if isinstance(current, dict):
            spec[key] = merge_mapping(value, current)
        else:
            spec[key] = value

    resolved_recording_id = spec.get("id")
    if not isinstance(resolved_recording_id, str) or not resolved_recording_id:
        resolved_recording_id = recording_id
    if not isinstance(resolved_recording_id, str) or not resolved_recording_id:
        raise StudioConfigError("recording.id must be a non-empty string")

    manifest_path = RECORDING_CONFIG_DIR / f"{resolved_recording_id}.yaml"
    spec["_manifest_path"] = str(manifest_path)
    spec["_config_dir"] = str(CONFIG_DIR)
    spec["_recording_id"] = resolved_recording_id
    spec["_overrides"] = list(overrides)
    spec["_studio_config"] = config
    if hydra_output_dir is not None:
        studio = config.get("studio", {})
        keep_output_dir = False
        if isinstance(studio, dict):
            keep_output_dir = bool(studio.get("keep_output_dir", False))
        spec["_hydra_output_dir"] = hydra_output_dir
        spec["_keep_hydra_output_dir"] = keep_output_dir
    return spec


def load_recording_spec(
    recording_id: str | None,
    overrides: Sequence[str] = (),
) -> dict[str, Any]:
    config = compose_studio_config(recording_id, overrides)
    return recording_spec_from_config(
        config,
        recording_id=recording_id,
        overrides=overrides,
    )


def load_recording_spec_from_hydra_cfg(cfg: DictConfig) -> dict[str, Any]:
    hydra_cfg = HydraConfig.get()
    return recording_spec_from_config(
        container_from_hydra_cfg(cfg),
        recording_id=None,
        overrides=list(hydra_cfg.overrides.task),
        hydra_output_dir=str(hydra_cfg.runtime.output_dir),
    )
