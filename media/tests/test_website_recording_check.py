from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_RECORDINGS = REPO_ROOT / "website" / "scripts" / "check-recordings.js"


def write_recording_tree(root: Path, *, script_text: str, stored_sha: str | None = None) -> None:
    recording_dir = root / "media" / "recording-scripts"
    casts_dir = root / "website" / "static" / "casts"
    audio_dir = root / "website" / "static" / "audio" / "casts"
    recording_dir.mkdir(parents=True)
    casts_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)

    script_path = recording_dir / "demo.md"
    script_path.write_text(script_text, encoding="utf-8")
    for suffix in [".cast", ".retimed.cast", ".timeline.jsonl"]:
        (casts_dir / f"demo{suffix}").write_text("ok\n", encoding="utf-8")
    (audio_dir / "demo.mp3").write_bytes(b"audio")
    (audio_dir / "demo.json").write_text(
        json.dumps({"audio": "website/static/audio/casts/demo.mp3"}) + "\n",
        encoding="utf-8",
    )

    digest = stored_sha or hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    (casts_dir / "demo.recording.json").write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "path": "media/recording-scripts/demo.md",
                        "sha256": digest,
                    }
                ],
                "recording": "demo",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    return subprocess.run(
        [node, str(CHECK_RECORDINGS)],
        cwd=REPO_ROOT,
        env={"ARBITER_WEBSITE_RECORDING_REPO_ROOT": str(root)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_website_recording_check_accepts_current_fingerprint(tmp_path: Path) -> None:
    write_recording_tree(tmp_path, script_text="# Demo\n")

    result = run_check(tmp_path)

    assert result.returncode == 0, result.stderr


def test_website_recording_check_rejects_changed_dependency(tmp_path: Path) -> None:
    write_recording_tree(tmp_path, script_text="# Demo\n", stored_sha="0" * 64)

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "stale artifact: media/recording-scripts/demo.md changed" in result.stderr


def test_website_recording_check_rejects_missing_artifact(tmp_path: Path) -> None:
    write_recording_tree(tmp_path, script_text="# Demo\n")
    (tmp_path / "website" / "static" / "casts" / "demo.retimed.cast").unlink()

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "missing artifact: website/static/casts/demo.retimed.cast" in result.stderr
