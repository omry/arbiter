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


def caption(text: str) -> str:
    return f"\x1b[36;1m# {text}\x1b[0m\r\n\r\n"


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
            (0.0, caption("First beat")),
            (0.5, "$ echo first\r\n"),
            (1.5, "first\r\n"),
            (2.0, caption("Second beat")),
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
    assert report.summary_text() == "ok: alignment captions 2/2, commands 2/2"


def test_alignment_report_matches_structured_action_commands(tmp_path: Path) -> None:
    cast = tmp_path / "aligned.cast"
    write_cast(
        cast,
        [
            (0.0, caption("Install")),
            (0.5, "$ curl -fsSL https://example.test/install.sh | sh\r\n"),
            (1.0, "$ tool --version\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "install",
                "caption": "Install",
                "actions": [
                    {
                        "commands": [
                            {
                                "run": "recording_install_tool",
                                "display": (
                                    "curl -fsSL https://example.test/install.sh | sh"
                                ),
                            },
                            {"run": "tool --version"},
                        ]
                    }
                ],
            },
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is True
    assert "commands: 2/2 matched" in report.text
    assert (
        "ok    0.500s  install.1: "
        "curl -fsSL https://example.test/install.sh | sh"
    ) in report.text
    assert "ok    1.500s  install.1: tool --version" in report.text


def test_alignment_tool_defaults_to_retimed_cast_when_configured(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    baseline = tmp_path / "demo.cast"
    retimed = tmp_path / "demo.retimed.cast"
    write_cast(baseline, [(0.0, caption("Install")), (0.5, "$ wrong\r\n")])
    write_cast(retimed, [(0.0, caption("Install")), (0.5, "$ expected\r\n")])
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "outputs": {
            "cast": str(baseline),
            "retimed_cast": str(retimed),
        },
        "beats": [
            {
                "id": "install",
                "caption": "Install",
                "actions": [{"run": "expected"}],
            },
        ],
    }

    monkeypatch.setattr(align_cast, "container_from_hydra_cfg", lambda _cfg: {})
    monkeypatch.setattr(
        align_cast,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: manifest,
    )

    assert align_cast.run_tool_from_hydra_cfg(object()) == 0


def test_alignment_keeps_multiline_command_after_blank_line(tmp_path: Path) -> None:
    cast = tmp_path / "multiline.cast"
    write_cast(
        cast,
        [
            (0.0, caption("Fetch")),
            (0.5, "$ printf 'message_uid=%s\\n' \"$message_uid\" && \\\r\n"),
            (0.1, "\r\n"),
            (0.1, "  arbiter op run imap:get_message | jq .\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "outputs": {"cast": str(cast), "retimed_cast": str(cast)},
        "beats": [
            {
                "id": "fetch",
                "caption": "Fetch",
                "actions": [
                    {
                        "run": "fetch",
                        "display": (
                            "printf 'message_uid=%s\\n' \"$message_uid\" && \\\n"
                            "arbiter op run imap:get_message | jq ."
                        ),
                    }
                ],
            }
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is True
    assert "commands: 2/2 matched" in report.text


def test_alignment_report_requires_review_on_command_mismatch(tmp_path: Path) -> None:
    cast = tmp_path / "misaligned.cast"
    write_cast(
        cast,
        [
            (0.0, caption("First beat")),
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


def test_alignment_check_prints_summary_by_default_on_mismatch(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    cast = tmp_path / "misaligned.cast"
    write_cast(
        cast,
        [
            (0.0, caption("First beat")),
            (0.2, "$ echo drifted\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "outputs": {"cast": str(cast), "retimed_cast": str(cast)},
        "beats": [
            {
                "id": "first",
                "caption": "First beat",
                "actions": [{"display": "echo expected", "run": "echo actual"}],
            }
        ],
    }

    monkeypatch.setattr(align_cast, "container_from_hydra_cfg", lambda _cfg: {})
    monkeypatch.setattr(
        align_cast,
        "load_recording_spec_from_hydra_cfg",
        lambda _cfg: manifest,
    )

    assert align_cast.run_tool_from_hydra_cfg(object()) == 2

    output = capsys.readouterr().out
    assert output == (
        "warn  alignment captions 1/1, commands 0/1\n"
        "info  alignment mismatches:\n"
        "info  command diff first.1: expected echo expected\n"
        "info  command diff first.1: observed echo drifted\n"
        "warn  rerun with verbose=true for full alignment report\n"
    )


def test_alignment_report_requires_review_on_extra_command(tmp_path: Path) -> None:
    cast = tmp_path / "extra-command.cast"
    write_cast(
        cast,
        [
            (0.0, caption("First beat")),
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
            (0.0, caption("First beat")),
            (0.2, "$ echo expected\r\n"),
            (0.2, caption("Unexpected beat")),
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


def test_alignment_report_ignores_visible_config_comments(tmp_path: Path) -> None:
    cast = tmp_path / "config-comments.cast"
    write_cast(
        cast,
        [
            (0.0, caption("Inspect generated config.")),
            (0.2, "$ sed -n '1,8p' conf/arbiter/account/imap/bot.yaml\r\n"),
            (0.1, "# @package arbiter.account.imap.bot\r\n"),
            (0.1, "# Human-facing summary shown by account listing tools.\r\n"),
            (0.1, "summary: Bot account\r\n"),
        ],
    )
    manifest = {
        "_manifest_path": str(tmp_path / "recording.yaml"),
        "beats": [
            {
                "id": "inspect-config",
                "caption": "Inspect generated config.",
                "actions": [
                    {"run": "sed -n '1,8p' conf/arbiter/account/imap/bot.yaml"}
                ],
            }
        ],
    }

    report = align_cast.render_report(manifest, cast)

    assert report.aligned is True
    assert "captions: 1/1 matched" in report.text
    assert "@package arbiter.account.imap.bot" not in report.text
