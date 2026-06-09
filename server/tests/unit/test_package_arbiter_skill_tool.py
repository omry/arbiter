from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import sys
import zipfile
from pathlib import Path


def _load_tool(repo_root: Path):
    path = repo_root / "tools" / "package_arbiter_skill"
    spec = importlib.util.spec_from_file_location(
        "package_arbiter_skill_tool",
        path,
        loader=SourceFileLoader("package_arbiter_skill_tool", str(path)),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "pyproject.toml").write_text(
        '[project]\nname = "arbiter-server"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_text("arbiter skill\n", encoding="utf-8")
    (tmp_path / "skill" / "agent-skill-installer.yaml").write_text(
        "installer:\n"
        "  version: 1\n"
        "  external_wheels:\n"
        '    - package: "arbiter-client==${package.version}"\n'
        "      editable: ../client\n"
        "      copies:\n"
        "        - wheel_path: arbiter_client/bin/arbiter\n"
        "          skill_path: bin/arbiter\n"
        "          executable: true\n"
        "          replace: true\n"
        "  shared:\n"
        "    instructions:\n"
        "      discoverability:\n"
        "        title: Arbiter\n"
        "        body: Use this skill for Arbiter.\n"
        "  agents:\n"
        "    codex:\n"
        "      instructions: ${installer.shared.instructions.discoverability}\n"
        "    claude:\n"
        "      instructions: ${installer.shared.instructions.discoverability}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_packages_single_skill_local_dir_and_wheel(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    root = make_repo(tmp_path)

    written = tool.package_skill(
        root=root,
        outdir=root / "dist" / "arbiter-skill",
        formats={"local", "wheel"},
        clean=True,
    )

    skill = root / "dist" / "arbiter-skill" / "local" / "arbiter-skill"
    assert skill in written
    assert (skill / "agent-skill-installer.yaml").read_text(encoding="utf-8") == (
        "installer:\n"
        "  version: 1\n"
        "  external_wheels:\n"
        '    - package: "arbiter-client==${package.version}"\n'
        "      editable: ../client\n"
        "      copies:\n"
        "        - wheel_path: arbiter_client/bin/arbiter\n"
        "          skill_path: bin/arbiter\n"
        "          executable: true\n"
        "          replace: true\n"
        "  shared:\n"
        "    instructions:\n"
        "      discoverability:\n"
        "        title: Arbiter\n"
        "        body: Use this skill for Arbiter.\n"
        "  agents:\n"
        "    codex:\n"
        "      instructions: ${installer.shared.instructions.discoverability}\n"
        "    claude:\n"
        "      instructions: ${installer.shared.instructions.discoverability}\n"
    )
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "arbiter skill\n"
    assert not (skill / "bin").exists()

    wheel_path = (
        root
        / "dist"
        / "arbiter-skill"
        / "wheels"
        / ("arbiter_skill-1.2.3-py3-none-any.whl")
    )
    assert wheel_path in written
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        assert "arbiter_skill/_skill/SKILL.md" in names
        assert "arbiter_skill/_skill/agent-skill-installer.yaml" in names
        assert not any(name.endswith("/bin/arbiter") for name in names)
