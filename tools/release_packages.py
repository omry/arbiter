from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from tomllib import loads as load_toml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    from tomli import loads as load_toml


@dataclass(frozen=True)
class Package:
    key: str
    kind: str
    name: str
    path: Path | None
    project_name: str | None = None
    artifacts: tuple[str, ...] = ("sdist", "wheel")

    @property
    def normalized_name(self) -> str:
        return self.name.replace("-", "_")

    @property
    def expected_project_name(self) -> str:
        return self.project_name or self.name


CLIENT_PLATFORM_TAGS = (
    "manylinux_2_17_x86_64",
    "manylinux_2_17_aarch64",
    "macosx_11_0_x86_64",
    "macosx_11_0_arm64",
    "win_amd64",
    "win_arm64",
)
CLIENT_TARGETS = (
    "linux-amd64",
    "linux-arm64",
    "darwin-amd64",
    "darwin-arm64",
    "windows-amd64",
    "windows-arm64",
)


def _project_name(pyproject: Path) -> str:
    data = load_toml(pyproject.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{pyproject} is missing [project]")
    name = project.get("name")
    if not isinstance(name, str):
        raise ValueError(f"{pyproject} is missing project.name")
    return name


def plugin_packages(root: Path) -> tuple[Package, ...]:
    packages: list[Package] = []
    for pyproject in sorted((root / "plugins").glob("*/pyproject.toml")):
        plugin_path = pyproject.parent.relative_to(root)
        key = plugin_path.name
        packages.append(
            Package(
                key=key,
                kind="plugin",
                name=_project_name(pyproject),
                path=plugin_path,
            )
        )
    return tuple(packages)


def skill_packages() -> tuple[Package, ...]:
    return (
        Package(
            key="skill",
            kind="skill",
            name="arbiter-skill",
            path=Path("server"),
            project_name="arbiter-server",
            artifacts=("wheel",),
        ),
    )


def release_packages(root: Path) -> tuple[Package, ...]:
    return (
        Package(
            key="server",
            kind="server",
            name="arbiter-server",
            path=Path("server"),
        ),
        *plugin_packages(root),
        Package(
            key="meta:all",
            kind="meta",
            name="arbiter-suite",
            path=Path("meta/arbiter-suite"),
        ),
        Package(
            key="client",
            kind="client",
            name="arbiter-client",
            path=Path("server"),
            project_name="arbiter-server",
            artifacts=("wheel",),
        ),
        *skill_packages(),
    )


def package_by_key(root: Path) -> dict[str, Package]:
    return {package.key: package for package in release_packages(root)}


def package_key_by_name(root: Path) -> dict[str, str]:
    return {package.name: package.key for package in release_packages(root)}


def package_keys(root: Path) -> tuple[str, ...]:
    return tuple(package_by_key(root))
