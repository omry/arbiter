from __future__ import annotations

import os
from pathlib import Path


def ensure_runtime_config_permissions(
    *,
    config_dir: Path,
    env_file: Path | None,
) -> None:
    if os.name == "nt":
        from .windows import ensure_runtime_config_permissions as ensure_windows

        ensure_windows(config_dir=config_dir, env_file=env_file)
        return

    from .posix import ensure_runtime_config_permissions as ensure_posix

    ensure_posix(config_dir=config_dir, env_file=env_file)
