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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, text, capture_output))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    tool.build_distributions(
        root=tmp_path,
        outdir=tmp_path / "dist",
        clean=False,
        packages=tool._parse_package_keys("server,imap,smtp,meta:all"),
        verbose=False,
    )

    assert [call[0][-1] for call in calls] == [
        str(tmp_path / "server"),
        str(tmp_path / "plugins" / "imap"),
        str(tmp_path / "plugins" / "smtp"),
        str(tmp_path / "meta" / "arbiter-suite"),
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
        env: dict[str, str] | None = None,
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
        packages=tool._parse_package_keys("server,smtp"),
        verbose=True,
    )

    assert [call[0][-1] for call in calls] == [
        str(tmp_path / "server"),
        str(tmp_path / "plugins" / "smtp"),
    ]
    for _, stdout, stderr in calls:
        assert stdout is None
        assert stderr is None


def test_build_distributions_discovers_new_plugin_package(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    tool = _load_tool()
    (tmp_path / "plugins" / "pop").mkdir(parents=True)
    (tmp_path / "plugins" / "pop" / "pyproject.toml").write_text(
        '[project]\nname = "arbiter-pop"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], object, object]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        env: dict[str, str] | None = None,
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
        packages=tool._parse_package_keys("pop", root=tmp_path),
        verbose=True,
    )

    assert [call[0][-1] for call in calls] == [str(tmp_path / "plugins" / "pop")]


def test_build_distributions_builds_selected_skill_wheel(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    tool = _load_tool()
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, verbose: bool) -> None:
        calls.append(command)

    monkeypatch.setattr(tool, "_run", fake_run)

    tool.build_distributions(
        root=tmp_path,
        outdir=tmp_path / "dist",
        clean=False,
        packages=tool._parse_package_keys("skill"),
        verbose=False,
    )

    assert calls == [
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(tmp_path / "dist"),
            str(tmp_path / "skill"),
        ],
    ]


def test_build_distributions_builds_client_platform_wheels(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    tool = _load_tool()
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "pyproject.toml").write_text(
        '[project]\nname = "arbiter-server"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(
        command: list[str],
        *,
        verbose: bool,
        env: dict[str, str] | None = None,
    ) -> None:
        calls.append((command, env))
        if command[:3] == [sys.executable, "-m", "build"]:
            target = env["ARBITER_CLIENT_TARGET"] if env is not None else ""
            tag = {
                "linux-amd64": "manylinux_2_17_x86_64",
                "linux-arm64": "manylinux_2_17_aarch64",
                "darwin-amd64": "macosx_11_0_x86_64",
                "darwin-arm64": "macosx_11_0_arm64",
                "windows-amd64": "win_amd64",
                "windows-arm64": "win_arm64",
            }[target]
            outdir = Path(command[command.index("--outdir") + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"arbiter_client-1.2.3-py3-none-{tag}.whl").write_text(
                "wheel\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(tool, "_run", fake_run)

    tool.build_distributions(
        root=tmp_path,
        outdir=tmp_path / "dist",
        clean=False,
        packages=tool._parse_package_keys("client"),
        verbose=False,
    )

    assert [Path(call[0][0]).name for call in calls[:1]] == ["build_go_client"]
    client_builds = [
        call for call in calls if call[0][:3] == [sys.executable, "-m", "build"]
    ]
    client_build_envs = [env for _cmd, env in client_builds if env is not None]
    assert len(client_build_envs) == len(client_builds)
    assert [env["ARBITER_CLIENT_TARGET"] for env in client_build_envs] == [
        "linux-amd64",
        "linux-arm64",
        "darwin-amd64",
        "darwin-arm64",
        "windows-amd64",
        "windows-arm64",
    ]
    assert all(env["ARBITER_CLIENT_VERSION"] == "1.2.3" for env in client_build_envs)


def test_parse_package_keys_deduplicates_while_preserving_order() -> None:
    tool = _load_tool()

    assert [
        package.key for package in tool._parse_package_keys("smtp,server,smtp")
    ] == [
        "smtp",
        "server",
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
        env: dict[str, str] | None = None,
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
