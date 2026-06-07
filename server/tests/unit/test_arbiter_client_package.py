from __future__ import annotations

import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT = REPO_ROOT / "client" / "arbiter-client"


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
