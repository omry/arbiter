from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    for package_name in ("agent-arbiter", "agent-arbiter-core"):
        try:
            return version(package_name)
        except PackageNotFoundError:
            continue
    return "unknown"
