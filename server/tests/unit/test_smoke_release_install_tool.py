from __future__ import annotations

import importlib.machinery
import importlib.util
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "smoke_release_install"


def _load_tool() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("smoke_release_install", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load smoke_release_install module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_parse_publish_specs_requires_exact_specs() -> None:
    tool = _load_tool()

    assert [
        spec.text for spec in tool.parse_publish_specs(" arbiter-skill==1.2.3 ")
    ] == ["arbiter-skill==1.2.3"]
    with pytest.raises(ValueError, match="NAME==VERSION"):
        tool.parse_publish_specs("arbiter-skill")
    with pytest.raises(ValueError, match="at least one"):
        tool.parse_publish_specs(" ")


def test_skill_smoke_installs_with_asi_directory_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_tool()
    dist_dir = tmp_path / "dist"
    target_dir = tmp_path / "target"
    dist_dir.mkdir()
    wheel = dist_dir / "arbiter_skill-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel\n")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, env))
        if command[:3] == [str(tmp_path / "python"), "-m", "agent_skill_installer"]:
            skill_dir = target_dir / ".codex" / "skills" / "arbiter"
            bin_dir = skill_dir / "bin"
            bin_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Arbiter\n", encoding="utf-8")
            arbiter = bin_dir / "arbiter"
            arbiter.write_text("#!/usr/bin/env sh\n", encoding="utf-8")
            arbiter.chmod(arbiter.stat().st_mode | stat.S_IXUSR)
            (target_dir / "AGENTS.md").write_text("hook\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(tool, "_run", fake_run)
    monkeypatch.setenv("PIP_FIND_LINKS", "/extra/wheels")

    tool._smoke_arbiter_skill(
        dist_dir=dist_dir,
        spec=tool.PublishSpec("arbiter-skill", "1.2.3"),
        target_dir=target_dir,
        asi_python=tmp_path / "python",
        no_index=False,
    )

    asi_command, asi_env = calls[0]
    assert asi_command == [
        str(tmp_path / "python"),
        "-m",
        "agent_skill_installer",
        "--no-ui",
        "install",
        "--wheel-file",
        str(wheel),
        "--agent",
        "codex",
        "--scope",
        "dir",
        "--target-dir",
        str(target_dir),
        "--force",
    ]
    assert asi_env is not None
    assert asi_env["PIP_FIND_LINKS"] == f"{dist_dir} /extra/wheels"
    assert calls[-1][0] == [
        str(target_dir / ".codex/skills/arbiter/bin/arbiter"),
        "--version",
    ]


def test_skill_smoke_requires_asi_installed_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_tool()
    dist_dir = tmp_path / "dist"
    target_dir = tmp_path / "target"
    dist_dir.mkdir()
    (dist_dir / "arbiter_skill-1.2.3-py3-none-any.whl").write_bytes(b"wheel\n")

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        skill_dir = target_dir / ".codex" / "skills" / "arbiter"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Arbiter\n", encoding="utf-8")
        (target_dir / "AGENTS.md").write_text("hook\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(tool, "_run", fake_run)

    with pytest.raises(FileNotFoundError, match=r"bin[/\\]arbiter"):
        tool._smoke_arbiter_skill(
            dist_dir=dist_dir,
            spec=tool.PublishSpec("arbiter-skill", "1.2.3"),
            target_dir=target_dir,
            asi_python=Path(sys.executable),
            no_index=False,
        )


def test_release_smoke_routes_package_specific_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_tool()
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        tool,
        "_create_venv",
        lambda venv_dir, *, clean: calls.append(("venv", clean)) or tmp_path / "python",
    )
    monkeypatch.setattr(
        tool,
        "_install_publish_specs",
        lambda **kwargs: calls.append(("install", kwargs["specs"])),
    )
    monkeypatch.setattr(
        tool,
        "_smoke_arbiter_client",
        lambda **kwargs: calls.append(("client", kwargs["venv_dir"])),
    )
    monkeypatch.setattr(
        tool,
        "_smoke_arbiter_skill",
        lambda **kwargs: calls.append(("skill", kwargs["spec"])),
    )
    monkeypatch.setattr(
        tool,
        "_smoke_server",
        lambda **kwargs: calls.append(("server", kwargs["venv_dir"])),
    )

    tool.smoke_release_install(
        dist_dir=tmp_path / "dist",
        venv_dir=tmp_path / "venv",
        asi_target_dir=tmp_path / "asi",
        asi_python=tmp_path / "asi-python",
        specs=(
            tool.PublishSpec("arbiter-client", "1.2.3"),
            tool.PublishSpec("arbiter-skill", "1.2.3"),
            tool.PublishSpec("arbiter-server", "1.2.3"),
        ),
        clean=True,
        no_index=False,
    )

    assert [name for name, _value in calls] == [
        "venv",
        "install",
        "client",
        "skill",
        "server",
    ]
