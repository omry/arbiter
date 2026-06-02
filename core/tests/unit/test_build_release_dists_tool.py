from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "build_release_dists"


def _load_tool() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("build_release_dists", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load build_release_dists module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_build_distributions_builds_all_packages_in_order(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    tool = _load_tool()
    calls: list[tuple[list[str], bool, bool]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, text, capture_output))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    tool.build_distributions(
        root=tmp_path,
        outdir=tmp_path / "dist",
        clean=False,
        packages=tool.PACKAGES,
        verbose=False,
    )

    assert [call[0][-1] for call in calls] == [
        str(tmp_path / "core"),
        str(tmp_path / "imap"),
        str(tmp_path / "smtp"),
        str(tmp_path),
    ]
    for command, text, capture_output in calls:
        assert command[:5] == [sys.executable, "-m", "build", "--sdist", "--wheel"]
        assert command[5:7] == ["--outdir", str(tmp_path / "dist")]
        assert text is True
        assert capture_output is True


def test_build_distributions_selects_packages_and_supports_verbose(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    tool = _load_tool()
    calls: list[tuple[list[str], object, object]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        stdout: object | None = None,
        stderr: object | None = None,
    ) -> None:
        assert check is True
        calls.append((command, stdout, stderr))

    monkeypatch.setattr(subprocess, "run", fake_run)

    tool.build_distributions(
        root=tmp_path,
        outdir=tmp_path / "dist",
        clean=False,
        packages=tool._parse_package_keys("core,smtp"),
        verbose=True,
    )

    assert [call[0][-1] for call in calls] == [
        str(tmp_path / "core"),
        str(tmp_path / "smtp"),
    ]
    for _, stdout, stderr in calls:
        assert stdout is None
        assert stderr is None


def test_parse_package_keys_deduplicates_while_preserving_order() -> None:
    tool = _load_tool()

    assert [package.key for package in tool._parse_package_keys("smtp,core,smtp")] == [
        "smtp",
        "core",
    ]


def test_main_returns_nonzero_when_build_fails(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    tool = _load_tool()

    def fake_run(
        command: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, "build stdout\n", "build stderr\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert tool.main(["--root", str(tmp_path), "--outdir", str(tmp_path / "dist")]) == 1
    captured = capsys.readouterr()
    assert "build stdout" in captured.out
    assert "build stderr" in captured.err


def test_main_rejects_unknown_package_key(tmp_path: Path) -> None:
    tool = _load_tool()

    assert (
        tool.main(
            [
                "--root",
                str(tmp_path),
                "--outdir",
                str(tmp_path / "dist"),
                "--packages",
                "mail",
            ]
        )
        == 1
    )
