from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_audio_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "audio.py"
    spec = importlib.util.spec_from_file_location("audio", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio"] = module
    spec.loader.exec_module(module)
    return module


audio = load_audio_tool()


def test_extract_narration_segments_from_movie_script() -> None:
    script = """# Demo

### First Beat

Narration:

"Start with a dedicated Python virtual environment.
Use it for Arbiter."

Action:

```bash
true
```

### Second Beat

Narration:

"Then stage it."
"""

    segments = audio.extract_narration_segments(script)

    assert segments == [
        audio.NarrationSegment(
            segment_id="first-beat",
            heading="First Beat",
            text="Start with a dedicated Python virtual environment. Use it for Arbiter.",
        ),
        audio.NarrationSegment(
            segment_id="second-beat",
            heading="Second Beat",
            text="Then stage it.",
        ),
    ]


def test_audio_settings_and_plan_use_manifest_values(tmp_path: Path) -> None:
    spec = {
        "audio": {
            "enabled": True,
            "provider": "openai",
            "env": "OPENAI_TEST_KEY",
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "format": "mp3",
            "instructions": "Speak clearly.",
            "cache_dir": str(tmp_path / "cache"),
        }
    }
    segment = audio.NarrationSegment(
        segment_id="intro",
        heading="Intro",
        text="Hello world.",
    )

    settings = audio.audio_settings(spec)
    plan = audio.plan_audio("demo", [segment], settings)

    assert settings.enabled is True
    assert settings.instructions == "Speak clearly."
    assert len(plan) == 1
    assert plan[0].output_path.parent == tmp_path / "cache" / "demo"
    assert plan[0].output_path.name.startswith("intro-")
    assert plan[0].output_path.suffix == ".mp3"


def test_audio_cache_key_changes_with_text_and_voice(tmp_path: Path) -> None:
    base = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions=None,
        cache_dir=tmp_path,
    )
    other_voice = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="cedar",
        format="mp3",
        instructions=None,
        cache_dir=tmp_path,
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")
    changed_text = audio.NarrationSegment("intro", "Intro", "Hello again.")

    assert audio.segment_cache_key(segment, base) == audio.segment_cache_key(
        segment, base
    )
    assert audio.segment_cache_key(segment, base) != audio.segment_cache_key(
        changed_text, base
    )
    assert audio.segment_cache_key(segment, base) != audio.segment_cache_key(
        segment, other_voice
    )


def test_generate_audio_reuses_existing_cache(tmp_path: Path) -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions=None,
        cache_dir=tmp_path,
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")
    plan = audio.plan_audio("demo", [segment], settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"cached")

    def fail_synthesize(
        segment: audio.NarrationSegment, settings: audio.AudioSettings
    ) -> bytes:
        raise AssertionError("cache hit should not call synthesize")

    paths = audio.generate_audio(plan, settings, synthesize=fail_synthesize)

    assert paths == [plan[0].output_path]
    assert plan[0].output_path.read_bytes() == b"cached"


def test_generate_audio_writes_provider_bytes(tmp_path: Path) -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions=None,
        cache_dir=tmp_path,
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")
    plan = audio.plan_audio("demo", [segment], settings)

    paths = audio.generate_audio(
        plan,
        settings,
        synthesize=lambda segment, settings: b"audio-bytes",
    )

    assert paths == [plan[0].output_path]
    assert plan[0].output_path.read_bytes() == b"audio-bytes"


def test_install_bootstrap_manifest_has_audio_plan() -> None:
    spec = audio.load_manifest("install-and-bootstrap")

    settings = audio.audio_settings(spec)
    segments = audio.load_narration_segments(spec)
    plan = audio.plan_audio("install-and-bootstrap", segments, settings)

    assert settings.enabled is True
    assert settings.env == "OPENAI_ARBITER_CINEMA_AUDIO_API_KEY"
    assert len(plan) >= 1
    assert all(item.output_path.suffix == ".mp3" for item in plan)


def test_openai_speech_request_uses_expected_payload() -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions="Speak clearly.",
        cache_dir=Path("cache"),
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")
    seen: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"audio"

    def fake_urlopen(request: Any, *, timeout: int) -> FakeResponse:
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    result = audio.openai_speech_bytes(
        segment,
        settings,
        environ={"OPENAI_TEST_KEY": "sk-test"},
        urlopen=fake_urlopen,
    )

    assert result == b"audio"
    assert seen["url"] == audio.OPENAI_SPEECH_URL
    assert seen["timeout"] == 120
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["body"] == {
        "model": "gpt-4o-mini-tts",
        "input": "Hello world.",
        "voice": "marin",
        "response_format": "mp3",
        "instructions": "Speak clearly.",
    }


def test_openai_speech_requires_api_key() -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions=None,
        cache_dir=Path("cache"),
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")

    try:
        audio.openai_speech_bytes(segment, settings, environ={})
    except audio.AudioError as exc:
        assert "OPENAI_TEST_KEY" in str(exc)
    else:
        raise AssertionError("missing API key should fail")


def test_openai_speech_reports_http_errors() -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions=None,
        cache_dir=Path("cache"),
    )
    segment = audio.NarrationSegment("intro", "Intro", "Hello world.")

    def fake_urlopen(request: Any, *, timeout: int) -> Any:
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=400,
            msg="bad request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad"}'),
        )

    try:
        audio.openai_speech_bytes(
            segment,
            settings,
            environ={"OPENAI_TEST_KEY": "sk-test"},
            urlopen=fake_urlopen,
        )
    except audio.AudioError as exc:
        assert "HTTP 400" in str(exc)
        assert "bad" in str(exc)
    else:
        raise AssertionError("HTTP error should fail")
