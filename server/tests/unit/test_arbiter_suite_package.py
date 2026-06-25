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
SUITE_ROOT = REPO_ROOT / "meta" / "arbiter-suite"
SERVER_ROOT = REPO_ROOT / "server"
SUITE_PACK_PATH = "arbiter_suite/reploy"
SUITE_PACK_ROOT = SUITE_ROOT / "src" / "arbiter_suite" / "reploy"
SERVER_PACK_ROOT = SERVER_ROOT / "src" / "arbiter_server" / "reploy"
SERVER_PACK_PATH = "arbiter_server/reploy"


def _build_suite_wheel(tmp_path: Path) -> Path:
    source = tmp_path / "arbiter-suite-src"
    shutil.copytree(
        SUITE_ROOT,
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
    return next(tmp_path.glob("arbiter_suite-*.whl"))


def _build_server_wheel(tmp_path: Path) -> Path:
    source = tmp_path / "arbiter-server-src"
    shutil.copytree(
        SERVER_ROOT,
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
    return next(tmp_path.glob("arbiter_server-*.whl"))


def test_suite_wheel_contains_reploy_deployment_pack(tmp_path: Path) -> None:
    wheel_path = _build_suite_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_prefix = SUITE_PACK_PATH + "/"
        packaged_files = {
            name.removeprefix(packaged_prefix)
            for name in wheel.namelist()
            if name.startswith(packaged_prefix) and not name.endswith("/")
        }
        source_files = {
            path.relative_to(SUITE_PACK_ROOT).as_posix()
            for path in SUITE_PACK_ROOT.rglob("*")
            if path.is_file()
        }
        assert packaged_files == source_files
        for relative_path in source_files:
            assert (
                wheel.read(packaged_prefix + relative_path)
                == (SUITE_PACK_ROOT / relative_path).read_bytes()
            )


def test_server_wheel_contains_minimal_reploy_deployment_pack(tmp_path: Path) -> None:
    wheel_path = _build_server_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_prefix = SERVER_PACK_PATH + "/"
        packaged_files = {
            name.removeprefix(packaged_prefix)
            for name in wheel.namelist()
            if name.startswith(packaged_prefix) and not name.endswith("/")
        }
        source_files = {
            path.relative_to(SERVER_PACK_ROOT).as_posix()
            for path in SERVER_PACK_ROOT.rglob("*")
            if path.is_file()
        }
        assert packaged_files == source_files
        for relative_path in source_files:
            assert (
                wheel.read(packaged_prefix + relative_path)
                == (SERVER_PACK_ROOT / relative_path).read_bytes()
            )
