from __future__ import annotations

import stat
from pathlib import Path


def _other_read_write_bits(mode: int) -> int:
    return mode & (stat.S_IROTH | stat.S_IWOTH)


def _group_or_other_write_bits(mode: int) -> int:
    return mode & (stat.S_IWGRP | stat.S_IWOTH)


def _group_or_other_read_write_bits(mode: int) -> int:
    return mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)


def _directory_group_or_other_write_execute_bits(mode: int) -> int:
    group_bits = mode & stat.S_IWGRP if mode & stat.S_IXGRP else 0
    other_bits = mode & stat.S_IWOTH if mode & stat.S_IXOTH else 0
    return group_bits | other_bits


def ensure_runtime_config_permissions(
    *,
    config_dir: Path,
    env_file: Path | None,
) -> None:
    for directory in sorted(
        {config_dir, *(path.parent for path in config_dir.rglob("*.yaml"))}
    ):
        if not directory.is_dir():
            continue
        if _directory_group_or_other_write_execute_bits(directory.stat().st_mode):
            raise ValueError(
                "unsafe config directory permissions: "
                f"{directory} must not be writable by group or others; "
                f"run `chmod go-w {directory}`"
            )

    for config_file in sorted(config_dir.rglob("*.yaml")):
        if not config_file.is_file():
            continue
        if _other_read_write_bits(
            config_file.stat().st_mode
        ) or _group_or_other_write_bits(config_file.stat().st_mode):
            raise ValueError(
                "unsafe config file permissions: "
                f"{config_file} must not be writable by group or others, "
                "or readable by others; "
                f"run `chmod g-w,o-rw {config_file}`"
            )

    if env_file is None or not env_file.exists():
        return
    if env_file.parent.exists() and _directory_group_or_other_write_execute_bits(
        env_file.parent.stat().st_mode
    ):
        raise ValueError(
            "unsafe app env directory permissions: "
            f"{env_file.parent} must not be writable by group or others; "
            f"run `chmod go-w {env_file.parent}`"
        )
    if _group_or_other_read_write_bits(env_file.stat().st_mode):
        raise ValueError(
            "unsafe app env file permissions: "
            f"{env_file} must not be readable or writable by group or others; "
            f"run `chmod 600 {env_file}`"
        )
