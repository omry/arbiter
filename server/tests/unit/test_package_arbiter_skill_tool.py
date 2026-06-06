from __future__ import annotations

import importlib.util
import os
from importlib.machinery import SourceFileLoader
import stat
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
    selector_dir = tmp_path / "packaging" / "arbiter-skill" / "selector"
    target_dir = tmp_path / "packaging" / "arbiter-skill" / "target"
    selector_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    (selector_dir / "agent-skill-selector.yaml").write_text(
        "platform_specific:\n"
        "  wheel: arbiter-skill-{platform}\n"
        "  local_path: ../arbiter-skill-{platform}\n",
        encoding="utf-8",
    )
    (target_dir / "agent-skill-installer.yaml").write_text(
        "installer:\n"
        "  version: 1\n"
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
    binary = tmp_path / "client/go-cli" / "dist" / "linux-arm64" / "arbiter"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return tmp_path


def test_packages_selector_and_platform_target_local_dirs_and_wheels(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    root = make_repo(tmp_path)

    written = tool.package_skill(
        root=root,
        outdir=root / "dist" / "arbiter-skill",
        targets=(tool.Target("linux", "arm64"),),
        formats={"local", "wheel"},
        clean=True,
    )

    selector = root / "dist" / "arbiter-skill" / "local" / "arbiter-skill"
    target = root / "dist" / "arbiter-skill" / "local" / "arbiter-skill-linux-arm64"
    selector_config = selector / "agent-skill-selector.yaml"
    assert selector in written
    assert target in written
    assert selector_config.read_text(encoding="utf-8") == (
        "platform_specific:\n"
        "  wheel: arbiter-skill-{platform}\n"
        "  local_path: ../arbiter-skill-{platform}\n"
    )
    assert (target / "agent-skill-installer.yaml").read_text(encoding="utf-8") == (
        "installer:\n"
        "  version: 1\n"
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
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "arbiter skill\n"
    assert (target / "bin" / "arbiter").read_text(encoding="utf-8") == "binary\n"
    if os.name != "nt":
        assert (target / "bin" / "arbiter").stat().st_mode & stat.S_IXUSR

    selector_wheel = (
        root
        / "dist"
        / "arbiter-skill"
        / "wheels"
        / ("arbiter_skill-1.2.3-py3-none-any.whl")
    )
    target_wheel = (
        root
        / "dist"
        / "arbiter-skill"
        / "wheels"
        / ("arbiter_skill_linux_arm64-1.2.3-py3-none-any.whl")
    )
    assert selector_wheel in written
    assert target_wheel in written

    with zipfile.ZipFile(selector_wheel) as wheel:
        assert "arbiter_skill/_skill/agent-skill-selector.yaml" in wheel.namelist()
        assert not any(name.endswith("/SKILL.md") for name in wheel.namelist())

    with zipfile.ZipFile(target_wheel) as wheel:
        names = wheel.namelist()
        assert "arbiter_skill_linux_arm64/_skill/SKILL.md" in names
        assert "arbiter_skill_linux_arm64/_skill/agent-skill-installer.yaml" in names
        if os.name != "nt":
            info = wheel.getinfo("arbiter_skill_linux_arm64/_skill/bin/arbiter")
            assert (info.external_attr >> 16) & stat.S_IXUSR
