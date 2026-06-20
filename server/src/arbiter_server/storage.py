from __future__ import annotations

import os
from pathlib import Path
import sys


class PluginStorage:
    def __init__(self, *, plugin_name: str, root: Path) -> None:
        self._plugin_name = plugin_name
        self._root = plugin_data_dir(root, plugin_name)

    @property
    def plugin_name(self) -> str:
        return self._plugin_name

    def path(self, *parts: str) -> Path:
        relative = Path(*parts)
        if relative.is_absolute():
            raise ValueError("plugin storage paths must be relative")
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("plugin storage paths must not traverse directories")
        return self._root / relative

    def ensure_dir(self, *parts: str) -> Path:
        directory = self.path(*parts)
        ensure_private_dir(directory)
        return directory


def default_plugin_data_root() -> Path:
    return default_server_data_root() / "plugins"


def default_server_data_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Arbiter" / "server"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Arbiter" / "server"
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "arbiter" / "server"
    return Path.home() / ".local" / "state" / "arbiter" / "server"


def plugin_data_dir(root: Path, plugin_name: str) -> Path:
    if "/" in plugin_name or "\\" in plugin_name or plugin_name in {"", ".", ".."}:
        raise ValueError(f"invalid plugin name for data directory: {plugin_name}")
    return root / plugin_name


def ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
