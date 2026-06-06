from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import sys


def _load_tool(repo_root: Path):
    path = repo_root / "tools" / "build_go_client"
    spec = importlib.util.spec_from_file_location(
        "build_go_client_tool",
        path,
        loader=SourceFileLoader("build_go_client_tool", str(path)),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_targets_cross_compile_matrix(tmp_path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []

    client_dir = tmp_path / "client/go-cli"
    (client_dir / "cmd" / "arbiter").mkdir(parents=True)
    (client_dir / "internal" / "cli").mkdir(parents=True)
    (client_dir / "go.mod").write_text(
        "module github.com/omry/arbiter/client/go-cli\n",
        encoding="utf-8",
    )

    def fake_run(command, *, cwd, env=None, verbose=False):
        calls.append((list(command), cwd, env))
        output_index = command.index("-o") + 1 if "-o" in command else None
        if output_index is not None:
            Path(command[output_index]).write_text("binary", encoding="utf-8")

    monkeypatch.setattr(tool, "run", fake_run)

    built = tool.build_targets(
        root=tmp_path,
        outdir=tmp_path / "client/go-cli" / "dist",
        targets=(tool.Target("linux", "arm64"), tool.Target("windows", "amd64")),
        clean=False,
        generate=True,
        strip=True,
        verbose=False,
    )

    assert calls[0][0] == ["go", "generate", "./internal/cli"]
    assert calls[0][1] == client_dir
    linux_env = calls[1][2]
    assert linux_env is not None
    assert linux_env["CGO_ENABLED"] == "0"
    assert linux_env["GOOS"] == "linux"
    assert linux_env["GOARCH"] == "arm64"
    assert "-ldflags" in calls[1][0]
    assert "-s -w" in calls[1][0]
    windows_env = calls[2][2]
    assert windows_env is not None
    assert windows_env["GOOS"] == "windows"
    assert windows_env["GOARCH"] == "amd64"
    assert built == [
        tmp_path / "client/go-cli" / "dist" / "linux-arm64" / "arbiter",
        tmp_path / "client/go-cli" / "dist" / "windows-amd64" / "arbiter.exe",
    ]


def test_main_reports_subprocess_failure(tmp_path, monkeypatch, capsys) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    (tmp_path / "client/go-cli").mkdir(parents=True)
    (tmp_path / "client/go-cli" / "go.mod").write_text(
        "module github.com/omry/arbiter/client/go-cli\n",
        encoding="utf-8",
    )

    def fail_run(command, *, cwd, env=None, verbose=False):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(tool, "run", fail_run)

    result = tool.main(
        [
            "--root",
            str(tmp_path),
            "--target",
            "linux-amd64",
            "--skip-generate",
        ]
    )

    assert result == 1
    assert "build_go_client:" in capsys.readouterr().err


def test_main_links_current_platform_binary_into_skill_bin(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    client_dir = tmp_path / "client/go-cli"
    (client_dir / "cmd" / "arbiter").mkdir(parents=True)
    (client_dir / "internal" / "cli").mkdir(parents=True)
    (client_dir / "go.mod").write_text(
        "module github.com/omry/arbiter/client/go-cli\n",
        encoding="utf-8",
    )
    skill_bin = tmp_path / "skill" / "bin"
    skill_bin.mkdir(parents=True)
    (skill_bin / "arbiter").write_text("old", encoding="utf-8")

    def fake_run(command, *, cwd, env=None, verbose=False):
        output_index = command.index("-o") + 1 if "-o" in command else None
        if output_index is not None:
            output = Path(command[output_index])
            output.write_text("new", encoding="utf-8")

    monkeypatch.setattr(tool, "run", fake_run)
    monkeypatch.setattr(
        tool,
        "current_target",
        lambda: tool.Target("linux", "amd64"),
    )

    result = tool.main(
        [
            "--root",
            str(tmp_path),
            "--target",
            "linux-amd64",
            "--skip-generate",
        ]
    )

    binary = tmp_path / "client/go-cli" / "dist" / "linux-amd64" / "arbiter"
    link = tmp_path / "skill" / "bin" / "arbiter"
    assert result == 0
    assert link.read_text(encoding="utf-8") == "new"
    assert link.stat().st_ino == binary.stat().st_ino
    assert not (skill_bin / ".arbiter.tmp-link").exists()

    result = tool.main(
        [
            "--root",
            str(tmp_path),
            "--target",
            "linux-amd64",
            "--skip-generate",
        ]
    )

    assert result == 0
    assert link.stat().st_ino == binary.stat().st_ino
    assert not (skill_bin / ".arbiter.tmp-link").exists()


def test_main_does_not_link_custom_output_dir(tmp_path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)
    client_dir = tmp_path / "client/go-cli"
    (client_dir / "cmd" / "arbiter").mkdir(parents=True)
    (client_dir / "internal" / "cli").mkdir(parents=True)
    (client_dir / "go.mod").write_text(
        "module github.com/omry/arbiter/client/go-cli\n",
        encoding="utf-8",
    )
    skill_bin = tmp_path / "skill" / "bin"
    skill_bin.mkdir(parents=True)
    (skill_bin / "arbiter").write_text("old", encoding="utf-8")

    def fake_run(command, *, cwd, env=None, verbose=False):
        output_index = command.index("-o") + 1 if "-o" in command else None
        if output_index is not None:
            Path(command[output_index]).write_text("new", encoding="utf-8")

    monkeypatch.setattr(tool, "run", fake_run)
    monkeypatch.setattr(
        tool,
        "current_target",
        lambda: tool.Target("linux", "amd64"),
    )

    result = tool.main(
        [
            "--root",
            str(tmp_path),
            "--outdir",
            str(tmp_path / ".ci" / "go-client-smoke"),
            "--target",
            "linux-amd64",
            "--skip-generate",
        ]
    )

    assert result == 0
    assert (skill_bin / "arbiter").read_text(encoding="utf-8") == "old"


def test_display_path_handles_paths_outside_repo(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool = _load_tool(repo_root)

    assert tool.display_path(tmp_path / "artifact", repo_root) == str(
        tmp_path / "artifact"
    )
