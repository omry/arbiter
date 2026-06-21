#!/usr/bin/env python3
"""Compose Arbiter media studio configuration with Hydra."""

from __future__ import annotations

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


class ToolAction(str, Enum):
    record = "record"
    session = "session"
    check = "check"
    dry_run = "dry_run"
    list = "list"
    retime = "retime"
    generate = "generate"
    align = "align"
    inspect = "inspect"
    output = "output"
    play = "play"


@dataclass
class StudioConfig:
    action: ToolAction = ToolAction.record
    output: str | None = None
    cast: str | None = None
    timeline: str | None = None
    headed: bool = False
    force: bool = False
    timestamps: bool = False
    allow_mismatch: bool = False
    run_id: str | None = None
    profile: dict[str, Any] = field(default_factory=dict)
    studio: dict[str, Any] = field(default_factory=dict)
    package_source: dict[str, Any] = field(default_factory=dict)
    recording: dict[str, Any] = field(default_factory=dict)


def register_studio_schema() -> None:
    ConfigStore.instance().store(name="studio_schema", node=StudioConfig)


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
