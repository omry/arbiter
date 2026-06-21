from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_tool(name: str) -> Any:
    path = REPO_ROOT / "media" / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


align_cast = load_tool("align_cast")


def write_cast(path: Path, events: list[tuple[float, str]]) -> None:
    lines = [
        json.dumps(
            {
                "version": 3,
                "term": {"cols": 80, "rows": 24},
                "timestamp": 1,
                "title": "test cast",
            }
        )
    ]
    lines.extend(json.dumps([delay, "o", text]) for delay, text in events)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cast_reader_preserves_observed_runtime_gap(tmp_path: Path) -> None:
    cast = tmp_path / "sleep.cast"
    # These are asciinema event delays, not commands executed by the test. The
    # test proves a visible `sleep 10` can carry a 10s observed runtime marker
    # without making the test suite wait for 10 seconds.
    write_cast(
        cast,
        [
            (0.0, "$ sleep 10\r\n"),
            (10.0, "done\r\n"),
        ],
    )

    lines = align_cast.read_cast_lines(cast)

    assert [(line.time, line.text) for line in lines] == [
        (0.0, "$ sleep 10"),
        (10.0, "done"),
    ]


def test_alignment_report_matches_manifest_to_cast_timeline(tmp_path: Path) -> None:
    cast = tmp_path / "aligned.cast"
    write_cast(
        cast,
        [
            (0.0, "# First beat\r\n\r\n"),
            (0.5, "$ echo first\r\n"),
            (1.5, "first\r\n"),
            (2.0, "# Second beat\r\n\r\n"),
            (0.25, "$ sleep 10\r\n"),
            (10.0, "done\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "first",
                "caption": "First beat",
                "actions": [{"run": "echo first"}],
            },
            {
                "id": "second",
                "caption": "Second beat",
                "actions": [{"run": "sleep 10"}],
            },
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is True
    assert "captions: 2/2 matched" in report.text
    assert "commands: 2/2 matched" in report.text
    assert "ok    0.500s  first.1: echo first" in report.text
    assert "ok    4.250s  second.1: sleep 10" in report.text


def test_alignment_report_requires_review_on_command_mismatch(tmp_path: Path) -> None:
    cast = tmp_path / "misaligned.cast"
    write_cast(
        cast,
        [
            (0.0, "# First beat\r\n\r\n"),
            (0.2, "$ echo drifted\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "first",
                "caption": "First beat",
                "actions": [{"display": "echo expected", "run": "echo actual"}],
            }
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is False
    assert "diff   0.200s  first.1: echo expected" in report.text
    assert "observed: echo drifted" in report.text
    assert "misaligned: manual review required" in report.text


def test_alignment_report_requires_review_on_extra_command(tmp_path: Path) -> None:
    cast = tmp_path / "extra-command.cast"
    write_cast(
        cast,
        [
            (0.0, "# First beat\r\n\r\n"),
            (0.2, "$ echo expected\r\n"),
            (0.2, "$ echo unexpected\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "first",
                "caption": "First beat",
                "actions": [{"run": "echo expected"}],
            }
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is False
    assert "extra   0.400s  echo unexpected" in report.text
    assert "misaligned: manual review required" in report.text


def test_alignment_report_requires_review_on_extra_caption(tmp_path: Path) -> None:
    cast = tmp_path / "extra-caption.cast"
    write_cast(
        cast,
        [
            (0.0, "# First beat\r\n\r\n"),
            (0.2, "$ echo expected\r\n"),
            (0.2, "# Unexpected beat\r\n\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "first",
                "caption": "First beat",
                "actions": [{"run": "echo expected"}],
            }
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is False
    assert "extra   0.400s  Unexpected beat" in report.text
    assert "misaligned: manual review required" in report.text
