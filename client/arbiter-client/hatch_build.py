from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


TARGETS = {
    "linux-amd64": ("arbiter", "manylinux_2_17_x86_64"),
    "linux-arm64": ("arbiter", "manylinux_2_17_aarch64"),
    "darwin-amd64": ("arbiter", "macosx_11_0_x86_64"),
    "darwin-arm64": ("arbiter", "macosx_11_0_arm64"),
    "windows-amd64": ("arbiter.exe", "win_amd64"),
    "windows-arm64": ("arbiter.exe", "win_arm64"),
}


class ArbiterClientBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        target = os.environ.get("ARBITER_CLIENT_TARGET", "").strip()
        if not target:
            raise RuntimeError("ARBITER_CLIENT_TARGET must be set")
        try:
            binary_name, platform_tag = TARGETS[target]
        except KeyError as exc:
            expected = ", ".join(sorted(TARGETS))
            raise RuntimeError(
                f"unsupported ARBITER_CLIENT_TARGET {target!r}; expected one of: {expected}"
            ) from exc

        binary_override = os.environ.get("ARBITER_CLIENT_BINARY")
        if binary_override:
            binary = Path(binary_override)
        else:
            repo_root = Path(self.root).resolve().parents[1]
            binary = repo_root / "client/go-cli" / "dist" / target / binary_name
        if not binary.is_file():
            raise RuntimeError(
                f"missing Arbiter client binary for {target}: {binary}; "
                "run tools/build_go_client first"
            )

        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{platform_tag}"
        build_data["shared_scripts"] = {str(binary): binary_name}


def get_build_hook() -> type[ArbiterClientBuildHook]:
    return ArbiterClientBuildHook
