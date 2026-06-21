from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_studio_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "studio.py"
    spec = importlib.util.spec_from_file_location("studio", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["studio"] = module
    spec.loader.exec_module(module)
    return module


studio = load_studio_tool()


def cfg(action: str) -> Any:
    return OmegaConf.create({"action": action, "recording": {"id": "demo"}})


def step_cfg(step: str) -> Any:
    return OmegaConf.create({"step": step, "recording": {"id": "demo"}})


def test_default_without_recording_lists_available_scripts(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setattr(
        studio, "list_recording_ids", lambda: ["install-and-bootstrap", "demo"]
    )

    config = OmegaConf.create({"action": "build", "recording": None})

    assert studio.run_tool_from_hydra_cfg(config) == 1
    output = capsys.readouterr().out
    assert "No recording script selected." in output
    assert "Available recording scripts:" in output
    assert "  install-and-bootstrap\n" in output
    assert "  demo\n" in output
    assert "Run with: media/tools/studio recording=install-and-bootstrap" in output


def test_list_action_prints_available_scripts(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setattr(studio, "list_recording_ids", lambda: ["install-and-bootstrap"])

    config = OmegaConf.create({"action": "list", "recording": None})

    assert studio.run_tool_from_hydra_cfg(config) == 0
    output = capsys.readouterr().out
    assert "No recording script selected." not in output
    assert "  install-and-bootstrap\n" in output


def test_build_runs_full_media_pipeline(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    calls: list[tuple[str, str]] = []

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            return 0

        return run

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))

    assert studio.run_tool_from_hydra_cfg(cfg("build")) == 0
    assert calls == [
        ("record", "record"),
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "retime"),
    ]
    output = capsys.readouterr().out
    assert "Follow-up commands:\n" in output
    assert "media/tools/studio recording=demo action=play\n" in output
    assert "media/tools/studio recording=demo action=inspect\n" in output
    assert "media/tools/studio recording=demo step=align\n" in output


def test_build_success_followups_are_suppressed_for_json(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    def run(_cfg: Any) -> int:
        return 0

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", run)

    config = OmegaConf.create(
        {
            "action": "build",
            "output_format": "json",
            "recording": {"id": "demo"},
        }
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0
    assert capsys.readouterr().out == ""


def test_build_dry_run_explains_pipeline_without_delegating(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def fail_if_called(_cfg: Any) -> int:
        raise AssertionError("build dry run should not delegate to subtools")

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", fail_if_called)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", fail_if_called)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", fail_if_called)

    config = OmegaConf.create(
        {
            "action": "build",
            "dry_run": True,
            "recording": {
                "id": "demo",
                "title": "Demo Build",
                "script": "media/recording-scripts/demo.md",
                "outputs": {
                    "cast": "website/static/casts/demo.cast",
                    "audio": "website/static/audio/casts/demo.mp3",
                },
                "publish": {
                    "default": "docusaurus",
                    "surfaces": {
                        "docusaurus": {
                            "type": "docusaurus_mdx",
                            "file": "website/docs/media/demo.mdx",
                            "placeholder": "demo",
                        }
                    },
                },
            },
            "audio": {
                "env": "OPENAI_API_KEY",
                "model": "gpt-4o-mini-tts",
                "voice": "marin",
                "format": "mp3",
                "cache_dir": "media/cache/audio",
            },
        }
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0

    output = capsys.readouterr().out
    assert "Build dry run: Demo Build" in output
    assert "audio_publish (link)" in output
    assert "retime (optimize)" in output
    assert "publish_surface (link)" in output
    assert "type: docusaurus_mdx" in output
    assert "website/static/casts/demo.retimed.cast" in output
    assert "No commands were run." in output


def test_record_step_dry_run_delegates_to_record_dry_run(monkeypatch: Any) -> None:
    calls: list[str] = []

    def run(cfg: Any) -> int:
        calls.append(cfg.step)
        return 0

    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)

    config = OmegaConf.create(
        {"step": "record", "dry_run": True, "recording": {"id": "demo"}}
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0
    assert calls == ["dry_run"]


def test_publish_docusaurus_mdx_replaces_holder(tmp_path: Path) -> None:
    page = tmp_path / "page.mdx"
    page.write_text(
        "\n".join(
            [
                "before",
                "<!-- studio:demo:start -->",
                "old embed",
                "<!-- studio:demo:end -->",
                "after",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "surface": "docusaurus",
        "recording": {
            "id": "demo",
            "title": "Demo Build",
            "script": "media/recording-scripts/demo.md",
            "outputs": {
                "cast": "website/static/casts/demo.cast",
                "audio": "website/static/audio/casts/demo.mp3",
            },
            "publish": {
                "default": "docusaurus",
                "surfaces": {
                    "docusaurus": {
                        "type": "docusaurus_mdx",
                        "file": str(page),
                        "placeholder": "demo",
                        "component": "TerminalCast",
                        "intro_segment": "overview",
                    }
                },
            },
        },
        "audio": {
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "cache_dir": "media/cache/audio",
        },
        "narration": {
            "beats": [
                {
                    "id": "overview",
                    "heading": "Overview",
                    "text": "Intro text from narration.",
                }
            ]
        },
    }

    assert studio.publish_surface(config) == page

    output = page.read_text(encoding="utf-8")
    assert "before" in output
    assert "after" in output
    assert "old embed" not in output
    assert '<TerminalCast\n  title="Demo Build"' in output
    assert 'src="/casts/demo.retimed.cast"' in output
    assert 'audio="/audio/casts/demo.mp3"' in output
    assert 'audioMeta="/audio/casts/demo.json"' in output
    assert 'intro="Intro text from narration."' in output
    assert 'introSegment="overview"' in output


def test_publish_plain_html_replaces_holder(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(
        "\n".join(
            [
                "<html>",
                "<body>",
                "<!-- studio:demo:start -->",
                "old iframe",
                "<!-- studio:demo:end -->",
                "</body>",
                "</html>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "surface": "plain_html",
        "recording": {
            "id": "demo",
            "title": "Demo Build",
            "script": "media/recording-scripts/demo.md",
            "outputs": {
                "cast": "website/static/casts/demo.cast",
                "audio": "website/static/audio/casts/demo.mp3",
            },
            "publish": {
                "default": "plain_html",
                "surfaces": {
                    "plain_html": {
                        "type": "plain_html",
                        "file": str(page),
                        "placeholder": "demo",
                        "intro_segment": "overview",
                    }
                },
            },
        },
        "audio": {
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "cache_dir": "media/cache/audio",
        },
        "narration": {
            "beats": [
                {
                    "id": "overview",
                    "heading": "Overview",
                    "text": "Intro text from narration.",
                }
            ]
        },
    }

    assert studio.publish_surface(config) == page

    output = page.read_text(encoding="utf-8")
    assert "old iframe" not in output
    assert '<iframe title="Demo Build"' in output
    assert "cast=%2Fcasts%2Fdemo.retimed.cast" in output
    assert "audio=%2Faudio%2Fcasts%2Fdemo.mp3" in output
    assert "audioMeta=%2Faudio%2Fcasts%2Fdemo.json" in output
    assert "intro=Intro+text+from+narration." in output
    assert "introSegment=overview" in output


def test_check_runs_non_artifact_checks(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            return 0

        return run

    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))

    assert studio.run_tool_from_hydra_cfg(cfg("check")) == 0
    assert calls == [("record", "check"), ("audio", "check")]


def test_empty_action_has_short_public_error() -> None:
    try:
        studio.run_tool_from_hydra_cfg(
            OmegaConf.create({"action": "", "recording": {"id": "demo"}})
        )
    except studio.StudioError as exc:
        message = str(exc)
    else:
        raise AssertionError("empty action should fail")

    assert "action cannot be empty" in message
    assert (
        "user-facing actions: build, check, play, inspect, output, runs, list"
        in message
    )
    assert "audio_generate" not in message


def test_individual_actions_delegate_to_owning_tool(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            return 0

        return run

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    configs = [
        step_cfg("record_check"),
        step_cfg("audio_generate"),
        step_cfg("publish"),
        step_cfg("retime_check"),
        step_cfg("align_check"),
        cfg("play"),
        cfg("runs"),
    ]
    for config in configs:
        assert studio.run_tool_from_hydra_cfg(config) == 0

    assert calls == [
        ("record", "check"),
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "check"),
        ("align", "check"),
        ("record", "play"),
        ("record", "runs"),
    ]


def test_failed_step_stops_pipeline(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def audio_run(cfg: Any) -> int:
        calls.append(("audio", cfg.step))
        return 0

    def record_run(cfg: Any) -> int:
        calls.append(("record", cfg.step))
        return 7

    def retime_run(cfg: Any) -> int:
        calls.append(("retime", cfg.step))
        return 0

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", audio_run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", record_run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", retime_run)

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.StudioError as exc:
        assert "record baseline cast failed with exit code 7" in str(exc)
    else:
        raise AssertionError("failed step should stop build")
    assert calls == [("record", "record")]


def test_json_output_format_suppresses_studio_progress_label(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def audio_run(cfg: Any) -> int:
        print('{"ok": true}')
        return 0

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", audio_run)

    config = OmegaConf.create(
        {
            "step": "audio_dry_run",
            "output_format": "json",
            "recording": {"id": "demo"},
        }
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0
    assert capsys.readouterr().out == '{"ok": true}\n'


def test_studio_loads_configured_env_file_before_delegating(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    env_file = tmp_path / "studio.env"
    env_file.write_text("STUDIO_DELEGATE_ENV=loaded\n", encoding="utf-8")
    monkeypatch.delenv("STUDIO_DELEGATE_ENV", raising=False)

    def audio_run(cfg: Any) -> int:
        assert os.environ["STUDIO_DELEGATE_ENV"] == "loaded"
        return 0

    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", audio_run)
    config = OmegaConf.create(
        {
            "step": "audio_check",
            "load_env_file": True,
            "env_file": str(env_file),
            "env_override": False,
            "recording": {"id": "demo"},
        }
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0
