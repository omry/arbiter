from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def minimal_build_spec(tmp_path: Path) -> dict[str, Any]:
    cast = tmp_path / "demo.cast"
    return {
        "id": "demo",
        "_recording_id": "demo",
        "title": "Demo Build",
        "outputs": {
            "cast": str(cast),
            "audio": str(tmp_path / "demo.mp3"),
        },
        "audio": {
            "enabled": True,
            "env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "cache_dir": str(tmp_path / "audio-cache"),
        },
        "beats": [{"id": "one", "actions": [{"run": "true"}]}],
    }


def write_minimal_recording_outputs(spec: dict[str, Any]) -> None:
    cast = Path(spec["outputs"]["cast"])
    cast.parent.mkdir(parents=True, exist_ok=True)
    cast.write_text(
        json.dumps(
            {
                "version": 3,
                "term": {"cols": 80, "rows": 24},
                "timestamp": 1,
                "title": "Demo Build",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    studio.retime_cast.timeline_path_for_cast(cast).write_text("", encoding="utf-8")


def write_audio_generation_outputs(spec: dict[str, Any]) -> None:
    settings = studio.audio.audio_settings(spec)
    transcription = studio.audio.transcription_settings(spec)
    segments = studio.audio.load_narration_segments(spec)
    plan = studio.audio.plan_audio(spec["_recording_id"], segments, settings)
    for item in plan:
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        item.output_path.write_bytes(b"cached audio")
        timestamp_path = studio.audio.timeline_path_for(item)
        timestamp_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp_path.write_text(
            json.dumps(
                studio.audio.timeline_payload(
                    spec["_recording_id"],
                    item,
                    transcription,
                    {"text": item.segment.text, "words": []},
                )
            )
            + "\n",
            encoding="utf-8",
        )


def write_fake_retimed_output(cfg: Any) -> None:
    output = getattr(cfg, "output", None)
    if output is not None:
        Path(str(output)).write_text("retimed cast\n", encoding="utf-8")


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
    spec = minimal_build_spec(tmp_path)

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            if name == "record":
                write_minimal_recording_outputs(spec)
            if name == "retime":
                write_fake_retimed_output(cfg)
            return 0

        return run

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    assert studio.run_tool_from_hydra_cfg(cfg("build")) == 0
    assert calls == [
        ("record", "record"),
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "retime"),
        ("align", "check"),
    ]
    output = capsys.readouterr().out
    assert output.count("step  audio\n") == 1
    assert "pass  audio ready\n" in output
    assert "step  generate audio" not in output
    assert "step  publish audio" not in output
    assert "pass  build completed after " in output
    assert "\n\nnext follow-up commands\n" in output
    assert "play    media/tools/studio recording=demo action=play\n" in output
    assert "watch   media/tools/studio recording=demo action=watch\n" in output
    assert "inspect media/tools/studio recording=demo action=inspect\n" in output
    assert "media/tools/studio recording=demo step=align\n" not in output
    assert studio.recording_skip_reason(spec) is None


def test_build_audio_summary_reports_tts_usage_and_cost(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {
        "beats": [
            {"id": "one", "heading": "One", "text": "Hello."},
            {"id": "two", "heading": "Two", "text": "Goodbye."},
        ]
    }
    calls: list[str] = []

    def run_audio(cfg: Any) -> int:
        calls.append(cfg.step)
        if cfg.step == "generate":
            settings = studio.audio.audio_settings(spec)
            segments = studio.audio.load_narration_segments(spec)
            plan = studio.audio.plan_audio("demo", segments, settings)
            for item in plan:
                item.output_path.parent.mkdir(parents=True, exist_ok=True)
                item.output_path.write_bytes(b"audio")
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        studio.audio,
        "audio_duration_seconds",
        lambda *_args, **_kwargs: 30.0,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run_audio)

    studio.run_build_audio_actions(
        cfg("build"),
        generate_needed=True,
        publish_needed=False,
    )

    assert calls == ["generate"]
    assert capsys.readouterr().out == (
        "step  audio\n"
        "pass  audio updated: 2 generated TTS segments, "
        "2 generated timestamp files, 14 TTS chars, "
        "60.0s transcribed audio, estimated OpenAI cost "
        "TTS $0.000210, transcription $0.006000, total $0.006210\n"
    )


def test_build_audio_summary_reports_unavailable_transcription_cost(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {
        "beats": [
            {"id": "one", "heading": "One", "text": "Hello."},
            {"id": "two", "heading": "Two", "text": "Goodbye."},
        ]
    }

    def run_audio(cfg: Any) -> int:
        if cfg.step == "generate":
            settings = studio.audio.audio_settings(spec)
            segments = studio.audio.load_narration_segments(spec)
            plan = studio.audio.plan_audio("demo", segments, settings)
            for item in plan:
                item.output_path.parent.mkdir(parents=True, exist_ok=True)
                item.output_path.write_bytes(b"audio")
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        studio.audio,
        "audio_duration_seconds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            studio.audio.AudioError("ffprobe failed")
        ),
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run_audio)

    studio.run_build_audio_actions(
        cfg("build"),
        generate_needed=True,
        publish_needed=False,
    )

    assert capsys.readouterr().out == (
        "step  audio\n"
        "pass  audio updated: 2 generated TTS segments, "
        "2 generated timestamp files, 14 TTS chars, "
        "estimated TTS cost $0.000210, transcription cost unavailable\n"
    )


def test_failed_record_preserves_existing_retimed_cast(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    retimed_path = studio.retime_cast.output_path_from_manifest(
        spec, Path(spec["outputs"]["cast"])
    )
    retimed_path.parent.mkdir(parents=True, exist_ok=True)
    retimed_path.write_text("previous retimed cast\n", encoding="utf-8")

    def fail_record(_cfg: Any) -> int:
        return 1

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", fail_record)

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.StudioError as exc:
        assert str(exc) == "record baseline cast failed with exit code 1"
    else:
        raise AssertionError("build should fail when record fails")

    assert retimed_path.read_text(encoding="utf-8") == "previous retimed cast\n"


def test_interrupted_build_record_preserves_recording_fingerprint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)
    fingerprint_path = studio.recording_fingerprint_path(Path(spec["outputs"]["cast"]))
    original_fingerprint = fingerprint_path.read_text(encoding="utf-8")
    spec["beats"][0]["actions"][0]["run"] = "echo changed"
    assert studio.recording_skip_reason(spec) == "recording fingerprint changed"

    def interrupt_record(_cfg: Any) -> int:
        raise studio.record.RecordingInterrupted("recording cancelled by user")

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", interrupt_record)

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.record.RecordingInterrupted:
        pass
    else:
        raise AssertionError("interrupted recording should abort build")

    assert fingerprint_path.read_text(encoding="utf-8") == original_fingerprint
    assert studio.recording_skip_reason(spec) == "recording fingerprint changed"


def test_failed_alignment_does_not_bless_recording_fingerprint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    retimed_path = studio.retime_cast.output_path_from_manifest(
        spec, Path(spec["outputs"]["cast"])
    )
    retimed_path.parent.mkdir(parents=True, exist_ok=True)
    retimed_path.write_text("previous retimed cast\n", encoding="utf-8")

    def run(cfg: Any) -> int:
        if cfg.step == "record":
            write_minimal_recording_outputs(spec)
        if cfg.step == "retime":
            assert cfg.output != str(retimed_path)
            Path(cfg.output).write_text("new retimed cast\n", encoding="utf-8")
        if cfg.step == "check":
            return 2
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(
        studio,
        "publish_surface",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("failed alignment must not publish surface")
        ),
    )

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.StudioError as exc:
        message = str(exc)
    else:
        raise AssertionError("failed alignment should fail build")

    assert "check alignment failed with exit code 2" in message
    fingerprint_path = studio.recording_fingerprint_path(Path(spec["outputs"]["cast"]))
    assert not fingerprint_path.exists()
    assert studio.recording_skip_reason(spec) == "recording fingerprint is missing"
    assert retimed_path.read_text(encoding="utf-8") == "previous retimed cast\n"
    assert not list(retimed_path.parent.glob(f".{retimed_path.name}.build-*.tmp"))


def test_failed_publish_surface_does_not_replace_retimed_cast(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    retimed_path = studio.retime_cast.output_path_from_manifest(
        spec, Path(spec["outputs"]["cast"])
    )
    retimed_path.parent.mkdir(parents=True, exist_ok=True)
    retimed_path.write_text("previous retimed cast\n", encoding="utf-8")

    def run(cfg: Any) -> int:
        if cfg.step == "record":
            write_minimal_recording_outputs(spec)
        if cfg.step == "retime":
            assert cfg.output != str(retimed_path)
            Path(cfg.output).write_text("new retimed cast\n", encoding="utf-8")
        return 0

    def fail_publish_surface(_cfg: Any, **_kwargs: Any) -> None:
        raise studio.StudioError("publish surface failed")

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio, "run_publish_surface", fail_publish_surface)

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.StudioError as exc:
        message = str(exc)
    else:
        raise AssertionError("failed publish surface should fail build")

    assert "publish surface failed" in message
    fingerprint_path = studio.recording_fingerprint_path(Path(spec["outputs"]["cast"]))
    assert not fingerprint_path.exists()
    assert studio.recording_skip_reason(spec) == "recording fingerprint is missing"
    assert retimed_path.read_text(encoding="utf-8") == "previous retimed cast\n"
    assert not list(retimed_path.parent.glob(f".{retimed_path.name}.build-*.tmp"))


def test_play_defaults_to_published_retimed_cast(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    retimed = studio.retime_cast.output_path_from_manifest(
        spec, Path(spec["outputs"]["cast"])
    )
    retimed.write_text("retimed cast\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.record, "check_asciinema", lambda: "asciinema 3.2.0")

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> Any:
        commands.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(studio.subprocess, "run", fake_run)

    assert studio.run_tool_from_hydra_cfg(cfg("play")) == 0

    assert commands == [["asciinema", "play", str(retimed)]]


def test_watch_uses_built_artifacts_without_publish_surface(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    paths = studio.artifact_paths(spec)
    paths["retimed_cast"].write_text("retimed cast\n", encoding="utf-8")
    paths["audio"].write_bytes(b"audio")
    paths["audio_metadata"].write_text('{"segments": []}\n', encoding="utf-8")
    captured: dict[str, Any] = {}

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("watch should not use publish surfaces")

    def fake_run_watch_server(
        cfg: Any,
        url_path: str,
        artifacts: dict[str, Path],
    ) -> int:
        captured["url_path"] = url_path
        captured["artifacts"] = artifacts
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio, "selected_surface", fail_if_called)
    monkeypatch.setattr(studio, "publish_surface", fail_if_called)
    monkeypatch.setattr(studio, "run_watch_server", fake_run_watch_server)

    assert studio.run_tool_from_hydra_cfg(cfg("watch")) == 0

    parsed = urlparse(captured["url_path"])
    params = parse_qs(parsed.query)
    assert parsed.path == "/cast-player.html"
    assert params["title"] == ["Demo Build"]
    assert params["cast"] == ["/__studio_artifacts__/cast.cast"]
    assert params["audio"] == ["/__studio_artifacts__/audio.mp3"]
    assert params["audioMeta"] == ["/__studio_artifacts__/audioMeta.json"]
    assert captured["artifacts"] == {
        "cast": paths["retimed_cast"].resolve(),
        "audio": paths["audio"].resolve(),
        "audioMeta": paths["audio_metadata"].resolve(),
    }


def test_watch_does_not_auto_open_terminal_browser(monkeypatch: Any) -> None:
    monkeypatch.setenv("BROWSER", "links")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert studio.configured_browser_command_name() == "links"
    assert studio.should_auto_open_browser() is False


def test_watch_does_not_auto_open_without_browser_or_display(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(studio.sys, "platform", "linux")

    assert studio.should_auto_open_browser() is False


def test_watch_auto_opens_gui_browser_from_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("BROWSER", "firefox --new-tab")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(studio.sys, "platform", "linux")

    assert studio.configured_browser_command_name() == "firefox"
    assert studio.should_auto_open_browser() is True


def test_watch_opens_wsl_host_browser_with_wslview(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/bin/wslview" if command == "wslview" else None

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return object()

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(studio.shutil, "which", fake_which)
    monkeypatch.setattr(studio.subprocess, "Popen", fake_popen)

    assert studio.open_watch_url("http://127.0.0.1:1234/cast-player.html") is True

    assert commands == [["/usr/bin/wslview", "http://127.0.0.1:1234/cast-player.html"]]


def test_watch_opens_wsl_host_browser_with_powershell_fallback(
    monkeypatch: Any,
) -> None:
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        if command == "powershell.exe":
            return "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        return None

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return object()

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(studio.shutil, "which", fake_which)
    monkeypatch.setattr(studio.subprocess, "Popen", fake_popen)

    url = (
        "http://127.0.0.1:1234/cast-player.html?"
        "title=Install+Arbiter+Server&cast=/casts/demo.cast"
    )
    assert studio.open_watch_url(url) is True

    assert commands == [
        [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Start-Process '{url}'",
        ]
    ]


def test_watch_does_not_fall_back_to_terminal_browser_in_wsl(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("BROWSER", "links")
    monkeypatch.setattr(studio.shutil, "which", lambda _command: None)
    monkeypatch.setattr(studio.webbrowser, "open", lambda url: calls.append(url))

    assert studio.open_watch_url("http://127.0.0.1:1234/cast-player.html") is False
    assert calls == []


def test_build_skips_fresh_recording(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    calls: list[tuple[str, str]] = []
    spec = minimal_build_spec(tmp_path)
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            if name == "retime":
                write_fake_retimed_output(cfg)
            return 0

        return run

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    assert studio.run_tool_from_hydra_cfg(cfg("build")) == 0

    assert calls == [
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "retime"),
        ("align", "check"),
    ]
    output = capsys.readouterr().out
    assert "skip  record baseline cast" in output
    assert output.count("step  audio\n") == 1
    assert "step  generate audio" not in output
    assert "step  publish audio" not in output


def test_build_skips_audio_generate_when_outputs_are_fresh(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    calls: list[tuple[str, str]] = []
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {
        "beats": [
            {
                "id": "overview",
                "heading": "Overview",
                "text": "Intro text from narration.",
            }
        ]
    }
    write_minimal_recording_outputs(spec)
    write_audio_generation_outputs(spec)
    studio.write_recording_fingerprint(spec)

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            if name == "retime":
                write_fake_retimed_output(cfg)
            return 0

        return run

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    assert studio.run_tool_from_hydra_cfg(cfg("build")) == 0

    assert calls == [
        ("audio", "publish"),
        ("retime", "retime"),
        ("align", "check"),
    ]
    output = capsys.readouterr().out
    assert "step  generate audio" not in output
    assert output.count("step  audio\n") == 1


def test_audio_publish_needs_work_when_wait_metadata_changes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {
        "beats": [
            {
                "id": "one",
                "heading": "One",
                "text": "Hello.",
                "waits": [
                    {
                        "target": "bootstrap-server",
                        "marker": "@wait:bootstrap-server@",
                        "text_offset": 6,
                        "gap_seconds": 0.0,
                    }
                ],
            }
        ]
    }
    settings = studio.audio.audio_settings(spec)
    segments = studio.audio.load_narration_segments(spec)
    plan = studio.audio.plan_audio(spec["_recording_id"], segments, settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"audio")
    studio.audio.timeline_path_for(plan[0]).write_text("{}\n", encoding="utf-8")
    published_audio = studio.audio.output_audio_path(
        spec,
        spec["_recording_id"],
        settings,
    )
    published_audio.parent.mkdir(parents=True, exist_ok=True)
    published_audio.write_bytes(b"published")
    metadata = studio.audio.output_audio_metadata_path(spec, published_audio)
    metadata.write_text(
        json.dumps(
            {
                "recording": "demo",
                "audio": str(published_audio),
                "duration": 1.0,
                "segments": [
                    {
                        "id": "one",
                        "heading": "One",
                        "text": "Hello.",
                        "audio": str(plan[0].output_path),
                        "offset": 0.0,
                        "duration": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    assert studio.audio_publish_needs_work(cfg("build"))


def test_audio_generate_needs_work_when_timestamp_sidecar_is_stale(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {"beats": [{"id": "one", "heading": "One", "text": "Hello."}]}
    settings = studio.audio.audio_settings(spec)
    segments = studio.audio.load_narration_segments(spec)
    plan = studio.audio.plan_audio(spec["_recording_id"], segments, settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"audio")
    studio.audio.timeline_path_for(plan[0]).write_text(
        json.dumps(
            {
                "recording": "demo",
                "segment": "one",
                "source_text": "Hello.",
                "timestamp_granularities": ["word"],
                "transcription_model": "old-model",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    assert studio.audio_generate_needs_work(cfg("build"))


def test_build_verbose_keeps_audio_substeps(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    calls: list[tuple[str, str]] = []
    spec = minimal_build_spec(tmp_path)
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            if name == "retime":
                write_fake_retimed_output(cfg)
            return 0

        return run

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    config = OmegaConf.create(
        {"action": "build", "verbose": True, "recording": {"id": "demo"}}
    )
    assert studio.run_tool_from_hydra_cfg(config) == 0

    assert calls == [
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "retime"),
        ("align", "check"),
    ]
    output = capsys.readouterr().out
    assert "step  generate audio" in output
    assert "step  publish audio" in output
    assert "step  audio\n" not in output


def test_build_prints_elapsed_time(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    times = iter([10.0, 72.25])

    def run(cfg: Any) -> int:
        if cfg.step == "record":
            write_minimal_recording_outputs(spec)
        if cfg.step == "retime":
            write_fake_retimed_output(cfg)
        return 0

    monkeypatch.setattr(studio.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", run)

    assert studio.run_tool_from_hydra_cfg(cfg("build")) == 0

    assert "pass  build completed after 1m 2.2s\n" in capsys.readouterr().out


def test_build_prints_elapsed_time_on_failure(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    times = iter([10.0, 72.25])

    def record_run(_cfg: Any) -> int:
        return 7

    monkeypatch.setattr(studio.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", record_run)

    try:
        studio.run_tool_from_hydra_cfg(cfg("build"))
    except studio.StudioError as exc:
        message = str(exc)
    else:
        raise AssertionError("failed record step should fail build")

    assert "record baseline cast failed with exit code 7" in message
    assert "fail  build failed after 1m 2.2s\n" in capsys.readouterr().out


def test_status_lines_use_color_when_forced(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    studio.pass_line("done")

    assert capsys.readouterr().out == "\033[32;1mpass \033[0m done\n"


def test_followup_commands_color_command_labels_when_forced(
    monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    studio.print_success_followups(cfg("build"))

    output = capsys.readouterr().out
    assert "\033[36;1mnext\033[0m follow-up commands\n" in output
    assert (
        "\033[32;1mplay   \033[0m media/tools/studio recording=demo action=play\n"
        in output
    )
    assert (
        "\033[32;1mwatch  \033[0m media/tools/studio recording=demo action=watch\n"
        in output
    )
    assert (
        "\033[32;1minspect\033[0m media/tools/studio recording=demo action=inspect\n"
        in output
    )


def test_build_force_records_even_when_recording_is_fresh(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls: list[tuple[str, str]] = []
    spec = minimal_build_spec(tmp_path)
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            if name == "record":
                write_minimal_recording_outputs(spec)
            if name == "retime":
                write_fake_retimed_output(cfg)
            return 0

        return run

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    config = OmegaConf.create(
        {"action": "build", "force": True, "recording": {"id": "demo"}}
    )
    assert studio.run_tool_from_hydra_cfg(config) == 0

    assert calls == [
        ("record", "record"),
        ("audio", "generate"),
        ("audio", "publish"),
        ("retime", "retime"),
        ("align", "check"),
    ]


def test_recording_fingerprint_tracks_run_file_content(tmp_path: Path) -> None:
    spec = minimal_build_spec(tmp_path)
    run_file = tmp_path / "setup.sh"
    run_file.write_text("echo before\n", encoding="utf-8")
    spec["setup"] = [{"run_file": str(run_file)}]
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    assert studio.recording_skip_reason(spec) is None

    run_file.write_text("echo after\n", encoding="utf-8")

    assert studio.recording_skip_reason(spec) == "recording fingerprint changed"


def test_recording_fingerprint_ignores_narration_changes(tmp_path: Path) -> None:
    spec = minimal_build_spec(tmp_path)
    spec["narration"] = {
        "beats": [
            {
                "id": "one",
                "heading": "One",
                "text": "Original narration.",
            }
        ]
    }
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    spec["narration"]["beats"][0]["text"] = "Updated narration."

    assert studio.recording_skip_reason(spec) is None


def test_recording_fingerprint_tracks_action_changes(tmp_path: Path) -> None:
    spec = minimal_build_spec(tmp_path)
    write_minimal_recording_outputs(spec)
    studio.write_recording_fingerprint(spec)

    spec["beats"][0]["actions"][0]["run"] = "false"

    assert studio.recording_skip_reason(spec) == "recording fingerprint changed"


def test_direct_record_writes_recording_fingerprint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = minimal_build_spec(tmp_path)

    def run(cfg: Any) -> int:
        assert cfg.step == "record"
        write_minimal_recording_outputs(spec)
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)

    assert studio.run_tool_from_hydra_cfg(step_cfg("record")) == 0

    fingerprint_path = studio.recording_fingerprint_path(Path(spec["outputs"]["cast"]))
    assert fingerprint_path.exists()
    assert studio.recording_skip_reason(spec) is None


def test_recording_fingerprint_excludes_retime_tool(tmp_path: Path) -> None:
    spec = minimal_build_spec(tmp_path)

    paths = studio.fingerprint_dependency_paths(spec)

    assert Path(studio.record.__file__) in paths
    assert Path(studio.retime_cast.__file__) not in paths


def test_build_success_followups_are_suppressed_for_json(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)

    def run(cfg: Any) -> int:
        if cfg.step == "record":
            write_minimal_recording_outputs(spec)
        if cfg.step == "retime":
            write_fake_retimed_output(cfg)
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", run)
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", run)

    config = OmegaConf.create(
        {
            "action": "build",
            "output_format": "json",
            "recording": {"id": "demo"},
        }
    )

    assert studio.run_tool_from_hydra_cfg(config) == 0
    assert "build completed" not in capsys.readouterr().out


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
    assert "align_check (validate)" in output
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


def test_clean_removes_recording_outputs_but_keeps_audio(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    paths = studio.artifact_paths(spec)
    fingerprint_path = studio.recording_fingerprint_path(paths["cast"])
    for path in [
        paths["cast"],
        paths["timeline"],
        fingerprint_path,
        paths["retimed_cast"],
        paths["audio"],
        paths["audio_metadata"],
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
    paths["audio_cache"].mkdir(parents=True)
    (paths["audio_cache"] / "cached.mp3").write_bytes(b"cached")

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    assert studio.run_tool_from_hydra_cfg(cfg("clean")) == 0

    assert not paths["cast"].exists()
    assert not paths["timeline"].exists()
    assert not fingerprint_path.exists()
    assert not paths["retimed_cast"].exists()
    assert paths["audio"].exists()
    assert paths["audio_metadata"].exists()
    assert (paths["audio_cache"] / "cached.mp3").exists()
    output = capsys.readouterr().out
    assert "step  clean recording outputs" in output
    assert "retained audio outputs, audio metadata, audio cache" in output


def test_clean_json_reports_removed_and_retained_outputs(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    spec = minimal_build_spec(tmp_path)
    paths = studio.clean_artifact_paths(spec)
    paths["baseline_cast"].parent.mkdir(parents=True, exist_ok=True)
    paths["baseline_cast"].write_text("cast", encoding="utf-8")

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )

    config = OmegaConf.create(
        {"action": "clean", "output_format": "json", "recording": {"id": "demo"}}
    )
    assert studio.run_tool_from_hydra_cfg(config) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] == [studio.display_path(paths["baseline_cast"])]
    assert "audio" in payload["retained"]
    assert "audio_cache" in payload["retained"]


def test_publish_docusaurus_mdx_replaces_holder(
    tmp_path: Path, monkeypatch: Any
) -> None:
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

    monkeypatch.setattr(
        studio.retime_cast,
        "require_fresh_retimed_cast",
        lambda **_kwargs: None,
    )

    assert studio.publish_surface(config) == page

    output = page.read_text(encoding="utf-8")
    assert "before" in output
    assert "after" in output
    assert "old embed" not in output
    assert 'import TerminalCast from "@site/src/components/TerminalCast";' in output
    assert '<TerminalCast\n  title="Demo Build"' in output
    assert 'src="/casts/demo.retimed.cast"' in output
    assert 'audio="/audio/casts/demo.mp3"' in output
    assert 'audioMeta="/audio/casts/demo.json"' in output
    assert 'intro="Intro text from narration."' in output
    assert 'introSegment="overview"' in output


def test_publish_plain_html_replaces_holder(tmp_path: Path, monkeypatch: Any) -> None:
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

    monkeypatch.setattr(
        studio.retime_cast,
        "require_fresh_retimed_cast",
        lambda **_kwargs: None,
    )

    assert studio.publish_surface(config) == page

    output = page.read_text(encoding="utf-8")
    assert "old iframe" not in output
    assert '<script src="/cast-player-embed.js"></script>' in output
    assert '<cast-player-embed title="Demo Build"' in output
    assert 'src="/casts/demo.retimed.cast"' in output
    assert 'audio="/audio/casts/demo.mp3"' in output
    assert 'audio-meta="/audio/casts/demo.json"' in output
    assert 'intro="Intro text from narration."' in output
    assert 'intro-segment="overview"' in output


def test_check_runs_non_artifact_checks(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def runner(name: str) -> Any:
        def run(cfg: Any) -> int:
            calls.append((name, cfg.step))
            return 0

        return run

    monkeypatch.setattr(studio.record, "run_tool_from_hydra_cfg", runner("record"))
    monkeypatch.setattr(studio.audio, "run_tool_from_hydra_cfg", runner("audio"))
    monkeypatch.setattr(studio.retime_cast, "run_tool_from_hydra_cfg", runner("retime"))
    monkeypatch.setattr(studio.align_cast, "run_tool_from_hydra_cfg", runner("align"))

    assert studio.run_tool_from_hydra_cfg(cfg("check")) == 0
    assert calls == [
        ("record", "check"),
        ("audio", "check"),
        ("retime", "check"),
        ("align", "check"),
    ]


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
        "user-facing actions: build, check, clean, play, watch, inspect, output, "
        "runs, list" in message
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
        OmegaConf.create(
            {
                "action": "play",
                "recording": {"id": "demo"},
                "run_id": "20260616-160412",
            }
        ),
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
    spec = minimal_build_spec(tmp_path)

    def audio_run(cfg: Any) -> int:
        calls.append(("audio", cfg.step))
        return 0

    def record_run(cfg: Any) -> int:
        calls.append(("record", cfg.step))
        return 7

    def retime_run(cfg: Any) -> int:
        calls.append(("retime", cfg.step))
        return 0

    monkeypatch.setattr(
        studio,
        "recording_spec_from_config",
        lambda *_args, **_kwargs: spec,
    )
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
