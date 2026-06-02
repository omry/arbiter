from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


_VERSION_LINE_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.|$)")


@dataclass(frozen=True)
class SourceInfo:
    commit: str | None
    dirty: bool | None


def compatibility_line(value: str) -> str:
    match = _VERSION_LINE_PATTERN.match(value)
    if not match:
        raise ValueError(f"version must start with MAJOR.MINOR: {value}")
    return f"{match.group('major')}.{match.group('minor')}"


def _pyproject_version(distribution_name: str, package_file: str | Path) -> str | None:
    start = Path(package_file).resolve()
    current = start if start.is_dir() else start.parent
    for src_dir in (
        parent for parent in (current, *current.parents) if parent.name == "src"
    ):
        package_root = src_dir.parent
        if not current.is_relative_to(src_dir):
            continue
        pyproject = package_root / "pyproject.toml"
        if not pyproject.exists():
            return None
        text = pyproject.read_text(encoding="utf-8")
        project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
        if project is None:
            return None
        project_text = project.group(1)
        name = re.search(r'^name\s*=\s*"([^"]+)"\s*$', project_text, re.MULTILINE)
        if name is None or name.group(1) != distribution_name:
            return None
        project_version = re.search(
            r'^version\s*=\s*"([^"]+)"\s*$',
            project_text,
            re.MULTILINE,
        )
        if project_version is not None:
            return project_version.group(1)
        return None
    return None


def distribution_version(
    distribution_name: str,
    *,
    package_file: str | Path | None = None,
) -> str:
    if package_file is not None:
        source_version = _pyproject_version(distribution_name, package_file)
        if source_version is not None:
            return source_version

    try:
        return version(distribution_name)
    except PackageNotFoundError:
        pass

    return "unknown"


def arbiter_core_version() -> str:
    return distribution_version("arbiter-core", package_file=__file__)


def core_api_version() -> str:
    return compatibility_line(arbiter_core_version())


def _find_vcs_root(start: str | Path) -> Path | None:
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    for src_dir in (
        parent for parent in (current, *current.parents) if parent.name == "src"
    ):
        if not current.is_relative_to(src_dir):
            continue
        package_root = src_dir.parent
        for parent in (package_root, *package_root.parents):
            if (parent / ".sl").exists() or (parent / ".git").exists():
                return parent
        return None
    return None


def _run_vcs_command(root: Path, args: list[str]) -> str | None:
    env = os.environ.copy()
    if args[0] == "sl":
        env["CHGDISABLE"] = "1"
    try:
        result = subprocess.run(
            args,
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def source_info(package_file: str | Path = __file__) -> SourceInfo:
    root = _find_vcs_root(package_file)
    if root is None:
        return SourceInfo(commit=None, dirty=None)

    if (root / ".sl").exists():
        commit = _run_vcs_command(root, ["sl", "log", "-r", ".", "-T", "{node|short}"])
        status = _run_vcs_command(root, ["sl", "status"])
    else:
        commit = _run_vcs_command(root, ["git", "rev-parse", "--short", "HEAD"])
        status = _run_vcs_command(root, ["git", "status", "--porcelain"])

    return SourceInfo(
        commit=commit or None,
        dirty=None if status is None else bool(status),
    )
