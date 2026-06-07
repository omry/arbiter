from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import zipfile
from types import ModuleType
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT = REPO_ROOT / "client"


def _load_hatch_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "arbiter_client_hatch_build",
        PROJECT / "hatch_build.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load arbiter-client hatch_build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hatchling_builds_platform_tagged_script_wheel(tmp_path: Path) -> None:
    binary = tmp_path / "arbiter"
    binary.write_bytes(b"native binary\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    outdir = tmp_path / "dist"
    env = os.environ.copy()
    env.update(
        {
            "ARBITER_CLIENT_VERSION": "1.2.3",
            "ARBITER_CLIENT_TARGET": "linux-amd64",
            "ARBITER_CLIENT_BINARY": str(binary),
        }
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(outdir),
            str(PROJECT),
        ],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    wheel = outdir / "arbiter_client-1.2.3-py3-none-manylinux_2_17_x86_64.whl"
    assert wheel.is_file()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert names == [
            "arbiter_client-1.2.3.data/scripts/arbiter",
            "arbiter_client-1.2.3.dist-info/METADATA",
            "arbiter_client-1.2.3.dist-info/WHEEL",
            "arbiter_client-1.2.3.dist-info/RECORD",
        ]
        assert not any(name.startswith("arbiter_client/") for name in names)
        assert (
            archive.read("arbiter_client-1.2.3.data/scripts/arbiter")
            == b"native binary\n"
        )
        script_info = archive.getinfo("arbiter_client-1.2.3.data/scripts/arbiter")
        if os.name != "nt":
            assert (script_info.external_attr >> 16) & stat.S_IXUSR
        wheel_metadata = archive.read("arbiter_client-1.2.3.dist-info/WHEEL").decode()
        assert "Root-Is-Purelib: false\n" in wheel_metadata
        assert "Tag: py3-none-manylinux_2_17_x86_64\n" in wheel_metadata


def test_hatchling_builds_windows_exe_script_wheel(tmp_path: Path) -> None:
    binary = tmp_path / "arbiter.exe"
    binary.write_bytes(b"windows binary\n")
    outdir = tmp_path / "dist"
    env = os.environ.copy()
    env.update(
        {
            "ARBITER_CLIENT_VERSION": "1.2.3",
            "ARBITER_CLIENT_TARGET": "windows-amd64",
            "ARBITER_CLIENT_BINARY": str(binary),
        }
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(outdir),
            str(PROJECT),
        ],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    wheel = outdir / "arbiter_client-1.2.3-py3-none-win_amd64.whl"
    assert wheel.is_file()
    with zipfile.ZipFile(wheel) as archive:
        assert "arbiter_client-1.2.3.data/scripts/arbiter.exe" in archive.namelist()
        assert (
            archive.read("arbiter_client-1.2.3.data/scripts/arbiter.exe")
            == b"windows binary\n"
        )


def test_editable_linux_build_uses_live_launcher(tmp_path: Path) -> None:
    hatch_build = _load_hatch_build()
    binary = tmp_path / "binary with spaces" / "arbiter"
    binary.parent.mkdir()
    binary.write_text("#!/usr/bin/env sh\n", encoding="utf-8")

    source, name = hatch_build._script_for_build(
        version="editable",
        build_dir=tmp_path / "build",
        target="linux-amd64",
        binary=binary,
        binary_name="arbiter",
    )

    assert name == "arbiter"
    assert source != binary
    assert source.read_text(encoding="utf-8") == (
        "#!/usr/bin/env sh\n"
        f"exec '{binary}' \"$@\"\n"
    )
    assert source.stat().st_mode & stat.S_IXUSR


def test_editable_windows_build_uses_live_cmd_launcher(tmp_path: Path) -> None:
    hatch_build = _load_hatch_build()
    binary = tmp_path / "bin" / "arbiter.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"windows binary\n")

    source, name = hatch_build._script_for_build(
        version="editable",
        build_dir=tmp_path / "build",
        target="windows-amd64",
        binary=binary,
        binary_name="arbiter.exe",
    )

    assert name == "arbiter.cmd"
    assert source != binary
    assert source.read_bytes() == f'@echo off\r\n"{binary}" %*\r\n'.encode()


def test_standard_build_uses_native_binary(tmp_path: Path) -> None:
    hatch_build = _load_hatch_build()
    binary = tmp_path / "arbiter"

    source, name = hatch_build._script_for_build(
        version="standard",
        build_dir=tmp_path / "build",
        target="linux-amd64",
        binary=binary,
        binary_name="arbiter",
    )

    assert source == binary
    assert name == "arbiter"


def test_client_version_prefers_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hatch_build = _load_hatch_build()

    monkeypatch.setenv("ARBITER_CLIENT_VERSION", "1.2.3")

    assert hatch_build.client_version() == "1.2.3"


def test_client_version_defaults_to_server_project_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hatch_build = _load_hatch_build()
    server_pyproject = REPO_ROOT / "server" / "pyproject.toml"
    expected_version = next(
        line.split('"')[1]
        for line in server_pyproject.read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )

    monkeypatch.delenv("ARBITER_CLIENT_VERSION", raising=False)

    assert hatch_build.client_version() == expected_version


def test_current_target_detects_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    hatch_build = _load_hatch_build()

    monkeypatch.setattr(hatch_build.sys, "platform", "darwin")
    monkeypatch.setattr(hatch_build.platform, "machine", lambda: "arm64")

    assert hatch_build.current_target() == "darwin-arm64"


def test_current_target_reports_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hatch_build = _load_hatch_build()

    monkeypatch.setattr(hatch_build.sys, "platform", "linux")
    monkeypatch.setattr(hatch_build.platform, "machine", lambda: "sparc")

    with pytest.raises(RuntimeError, match="set ARBITER_CLIENT_TARGET explicitly"):
        hatch_build.current_target()
