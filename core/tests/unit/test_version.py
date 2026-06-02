from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from arbiter_core import version as version_module


def test_distribution_version_falls_back_to_source_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = tmp_path / "plugin" / "pyproject.toml"
    package_file = tmp_path / "plugin" / "src" / "example_plugin" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    pyproject.write_text(
        """[project]
name = "arbiter-example"
version = "1.2.3"
""",
        encoding="utf-8",
    )
    package_file.write_text("", encoding="utf-8")

    def missing_distribution(package_name: str) -> str:
        raise PackageNotFoundError(package_name)

    monkeypatch.setattr(version_module, "version", missing_distribution)

    assert (
        version_module.distribution_version(
            "arbiter-example",
            package_file=package_file,
        )
        == "1.2.3"
    )


def test_distribution_version_prefers_source_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = tmp_path / "core" / "pyproject.toml"
    package_file = tmp_path / "core" / "src" / "arbiter_core" / "version.py"
    package_file.parent.mkdir(parents=True)
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    pyproject.write_text(
        """[project]
name = "arbiter-core"
version = "1.2.3"
""",
        encoding="utf-8",
    )
    package_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(version_module, "version", lambda package_name: "9.9.9")

    assert (
        version_module.distribution_version(
            "arbiter-core",
            package_file=package_file,
        )
        == "1.2.3"
    )


def test_distribution_version_does_not_escape_venv_for_source_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_pyproject = tmp_path / "core" / "pyproject.toml"
    package_file = (
        tmp_path
        / ".venv"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "arbiter_core"
        / "version.py"
    )
    package_file.parent.mkdir(parents=True)
    checkout_pyproject.parent.mkdir(parents=True)
    checkout_pyproject.write_text(
        """[project]
name = "arbiter-core"
version = "9.9.9"
""",
        encoding="utf-8",
    )
    package_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(version_module, "version", lambda package_name: "1.2.3")

    assert (
        version_module.distribution_version(
            "arbiter-core",
            package_file=package_file,
        )
        == "1.2.3"
    )


def test_arbiter_core_version_reads_core_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_distribution_version(
        distribution_name: str,
        *,
        package_file: str | Path | None = None,
    ) -> str:
        assert distribution_name == "arbiter-core"
        return "1.2.3"

    monkeypatch.setattr(
        version_module, "distribution_version", fake_distribution_version
    )

    assert version_module.arbiter_core_version() == "1.2.3"


def test_source_info_returns_unknown_outside_vcs(tmp_path: Path) -> None:
    package_file = tmp_path / "package.py"
    package_file.write_text("", encoding="utf-8")

    assert version_module.source_info(package_file) == version_module.SourceInfo(
        commit=None,
        dirty=None,
    )
