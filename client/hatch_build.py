from __future__ import annotations

import os
import platform
from pathlib import Path
import re
import shlex
import sys
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


def _repo_root(project_root: str) -> Path:
    path = Path(project_root).resolve()
    if path.is_file():
        path = path.parent
    return path.parent


def client_version() -> str:
    version = os.environ.get("ARBITER_CLIENT_VERSION", "").strip()
    if version:
        return version

    server_pyproject = _repo_root(__file__) / "server" / "pyproject.toml"
    match = re.search(
        r'^version = "([^"]+)"$',
        server_pyproject.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(
            f"could not read Arbiter client version from {server_pyproject}"
        )
    return match.group(1)


def current_target() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    arch_by_machine = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    os_by_platform = {
        "darwin": "darwin",
        "linux": "linux",
        "win32": "windows",
        "cygwin": "windows",
        "msys": "windows",
    }
    try:
        target_os = os_by_platform[system]
        target_arch = arch_by_machine[machine]
    except KeyError as exc:
        raise RuntimeError(
            f"could not infer ARBITER_CLIENT_TARGET from platform "
            f"{system!r}/{machine!r}; set ARBITER_CLIENT_TARGET explicitly"
        ) from exc
    return f"{target_os}-{target_arch}"


def _editable_launcher(
    *,
    build_dir: Path,
    target: str,
    binary: Path,
    binary_name: str,
) -> tuple[Path, str]:
    launcher_dir = build_dir / "arbiter-client-editable" / target
    launcher_dir.mkdir(parents=True, exist_ok=True)

    if binary_name.endswith(".exe"):
        launcher = launcher_dir / "arbiter.cmd"
        quoted_binary = str(binary).replace('"', '""')
        binary_arg = f'"{quoted_binary}"'
        launcher.write_text(f"@echo off\r\n{binary_arg} %*\r\n", encoding="utf-8")
        return launcher, launcher.name

    launcher = launcher_dir / binary_name
    launcher.write_text(
        f'#!/usr/bin/env sh\nexec {shlex.quote(str(binary))} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher, binary_name


def _script_for_build(
    *,
    version: str,
    build_dir: Path,
    target: str,
    binary: Path,
    binary_name: str,
) -> tuple[Path, str]:
    if version == "editable":
        return _editable_launcher(
            build_dir=build_dir,
            target=target,
            binary=binary,
            binary_name=binary_name,
        )
    return binary, binary_name


class ArbiterClientBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        target = os.environ.get("ARBITER_CLIENT_TARGET", "").strip()
        if not target:
            target = current_target()
        try:
            binary_name, platform_tag = TARGETS[target]
        except KeyError as exc:
            expected = ", ".join(sorted(TARGETS))
            raise RuntimeError(
                f"unsupported ARBITER_CLIENT_TARGET {target!r}; expected one of: {expected}"
            ) from exc

        binary_override = os.environ.get("ARBITER_CLIENT_BINARY")
        if binary_override:
            binary = Path(binary_override).resolve()
        else:
            repo_root = _repo_root(self.root)
            binary = repo_root / "client/go-cli" / "dist" / target / binary_name
        if not binary.is_file():
            raise RuntimeError(
                f"missing Arbiter client binary for {target}: {binary}; "
                f"run tools/build_go_client --target {target} first"
            )

        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{platform_tag}"
        script_source, script_name = _script_for_build(
            version=version,
            build_dir=Path(self.directory),
            target=target,
            binary=binary,
            binary_name=binary_name,
        )
        build_data["shared_scripts"] = {str(script_source): script_name}
        build_data["force_include"] = {str(script_source): "arbiter_client/bin/arbiter"}


def get_build_hook() -> type[ArbiterClientBuildHook]:
    return ArbiterClientBuildHook
