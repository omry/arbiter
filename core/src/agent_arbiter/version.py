from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import re


__version__ = "0.8.0"
_VERSION_LINE_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.|$)")


def compatibility_line(value: str) -> str:
    match = _VERSION_LINE_PATTERN.match(value)
    if not match:
        raise ValueError(f"version must start with MAJOR.MINOR: {value}")
    return f"{match.group('major')}.{match.group('minor')}"


def core_version() -> str:
    return __version__


def core_api_version() -> str:
    return compatibility_line(core_version())


def package_version() -> str:
    for package_name in ("agent-arbiter", "agent-arbiter-core"):
        try:
            return version(package_name)
        except PackageNotFoundError:
            continue
    return __version__
