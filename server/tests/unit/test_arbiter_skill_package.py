from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skill"


def _project_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data["project"]
    assert isinstance(project, dict)
    version = project["version"]
    assert isinstance(version, str)
    return version


def test_skill_payload_installs_only_skill_file_before_external_wheels() -> None:
    metadata = (SKILL_ROOT / "agent-skill-installer.yaml").read_text(encoding="utf-8")

    assert "  payload:\n    include:\n      - SKILL.md\n" in metadata
    assert "      editable: ../client\n" in metadata
    assert "          skill_path: bin/arbiter\n" in metadata
    assert "          replace:" not in metadata


def test_skill_package_version_matches_release_line() -> None:
    assert _project_version(SKILL_ROOT / "pyproject.toml") == _project_version(
        REPO_ROOT / "server" / "pyproject.toml"
    )


def test_skill_wheel_contains_only_asi_metadata_payload(tmp_path: Path) -> None:
    source = tmp_path / "skill-src"
    shutil.copytree(
        SKILL_ROOT,
        source,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build", "*.egg-info"),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(source),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    wheel_path = next(tmp_path.glob("arbiter_skill-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        init_text = wheel.read("arbiter_skill/__init__.py").decode("utf-8")
        skill_text = wheel.read("arbiter_skill/skill/SKILL.md").decode("utf-8")
        metadata_text = wheel.read(
            "arbiter_skill/skill/agent-skill-installer.yaml"
        ).decode("utf-8")
        wheel_metadata = wheel.read(
            "arbiter_skill-"
            f"{_project_version(SKILL_ROOT / 'pyproject.toml')}.dist-info/METADATA"
        ).decode("utf-8")

    assert (
        init_text
        == f'__version__ = "{_project_version(SKILL_ROOT / "pyproject.toml")}"\n'
    )
    assert skill_text == (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert metadata_text == (SKILL_ROOT / "agent-skill-installer.yaml").read_text(
        encoding="utf-8"
    )
    assert "arbiter_skill/skill/SKILL.md" in names
    assert "arbiter_skill/skill/agent-skill-installer.yaml" in names
    assert "arbiter_skill/skill/bin/arbiter" not in names
    assert "bin/arbiter" not in names
    assert not any(name.startswith("build/") for name in names)
    assert not any(name.startswith("src/") for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert "Author: Omry Yadan\n" in wheel_metadata
    assert "Author-email: Omry Yadan <omry@yadan.net>\n" in wheel_metadata
    assert "Maintainer: Omry Yadan\n" in wheel_metadata
    assert "Keywords: agent,skill,mcp,access-control,arbiter\n" in wheel_metadata
    assert "Classifier: Programming Language :: Python :: 3.10\n" in wheel_metadata
    assert (
        "Project-URL: Repository, https://github.com/omry/arbiter\n" in wheel_metadata
    )
