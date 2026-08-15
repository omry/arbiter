from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = (
    REPO_ROOT
    / "server"
    / "src"
    / "arbiter_server"
    / "reploy"
    / "arbiter.blueprint.yaml"
)


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="Reploy is currently supported only on Linux.",
)


def _reploy_command() -> str:
    venv_command = Path(sys.executable).with_name("reploy")
    if venv_command.is_file():
        return str(venv_command)
    command = shutil.which("reploy")
    if command is None:
        pytest.skip("reploy is not installed")
    return command


def _run_reploy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_reploy_command(), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def test_reploy_stages_arbiter_from_local_blueprint(tmp_path: Path) -> None:
    staging_dir = tmp_path / "reploy-staging"

    stage = _run_reploy(
        "stage",
        f"file:{BLUEPRINT}",
        "--dir",
        str(staging_dir),
    )

    assert stage.returncode == 0, stage.stderr
    assert "created staging directory for arbiter" in stage.stdout
    assert (staging_dir / "appctl").is_file()
    assert (staging_dir / ".reploy" / "state.json").is_file()
    assert (staging_dir / ".reploy" / "staged-control.json").is_file()

    options = _run_reploy("bundle", "options", "--dir", str(staging_dir))

    assert options.returncode == 0, options.stderr
    assert (
        "application/arbiter-suite\tInstall the full Arbiter suite." in options.stdout
    )
    assert "application/imap\tReceive email through IMAP." in options.stdout
    assert "application/smtp\tSend email through SMTP." in options.stdout

    add_plugins = _run_reploy(
        "bundle",
        "add",
        "--dir",
        str(staging_dir),
        "application/imap,smtp",
    )

    assert add_plugins.returncode == 0, add_plugins.stderr
    assert add_plugins.stdout == "bundle overlay updated\n"

    selected = _run_reploy(
        "bundle",
        "list",
        "--dir",
        str(staging_dir),
    )

    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.splitlines() == [
        "option\tapplication/imap",
        "option\tapplication/smtp",
    ]
