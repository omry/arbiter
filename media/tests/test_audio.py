from __future__ import annotations

import importlib.util
import io
import json
import subprocess
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

```studio-directive
scene: Demo
```

### First Beat

```studio-directive
beat:
  id: first
  heading: First Beat
  narration: >-
    Start with a dedicated Python virtual environment.
    Use it for Arbiter.
```

Action:

```bash
true
```

### Second Beat

```studio-directive
beat:
  id: second
  heading: Second Beat
  narration: Then stage it.
```
"""

    segments = audio.extract_narration_segments(script)

    assert segments == [
        audio.NarrationSegment(
            segment_id="first",
            heading="First Beat",
            text="Start with a dedicated Python virtual environment. Use it for Arbiter.",
        ),
        audio.NarrationSegment(
            segment_id="second",
            heading="Second Beat",
            text="Then stage it.",
        ),
    ]


def test_extract_narration_allows_quoted_first_line_to_end_with_colon() -> None:
    script = """# Demo

### Review Plan

```studio-directive
beat:
  id: review-plan
  heading: Review Plan
  narration: >-
    Finally, review what promotion would do:
    copy files, set ownership, and prepare the service.
```

Action:

```bash
true
```
"""

    segments = audio.extract_narration_segments(script)

    assert segments == [
        audio.NarrationSegment(
            segment_id="review-plan",
            heading="Review Plan",
            text=(
                "Finally, review what promotion would do: copy files, "
                "set ownership, and prepare the service."
            ),
        )
    ]


def test_extract_narration_accepts_one_large_directive_block() -> None:
    script = """# Demo

```studio-directive
scene:
  title: Demo Scene
beats:
  - id: first
    heading: First
    narration: Do the first thing.
  - id: second
    heading: Second
    narration: Do the second thing.
```
"""

    script_narration = audio.extract_script_narration(script)

    assert script_narration.scene_title == "Demo Scene"
    assert script_narration.segments == [
        audio.NarrationSegment(
            segment_id="first",
            heading="First",
            text="Do the first thing.",
        ),
        audio.NarrationSegment(
            segment_id="second",
            heading="Second",
            text="Do the second thing.",
        ),
    ]


def test_extract_narration_accepts_yaml_studio_directive_fence() -> None:
    script = """# Demo

```yaml studio-directive
scene: Demo Scene
```

```yaml studio-directive
beat:
  id: overview
  heading: Overview
  narration: >-
    This uses the syntax-highlighted directive fence.
```
"""

    script_narration = audio.extract_script_narration(script)

    assert script_narration.scene_title == "Demo Scene"
    assert script_narration.segments == [
        audio.NarrationSegment(
            segment_id="overview",
            heading="Overview",
            text="This uses the syntax-highlighted directive fence.",
        )
    ]


def test_extract_narration_rejects_loose_machine_fields() -> None:
    script = """# Demo

### Missing Beat

Narration:

"No stable id."
"""

    try:
        audio.extract_narration_segments(script)
    except audio.AudioError as exc:
        assert "studio-directive" in str(exc)
    else:
        raise AssertionError("loose narration directive should fail")


def test_extract_narration_requires_beat_id_in_directive() -> None:
    script = """# Demo

```studio-directive
beat:
  heading: Missing Beat
  narration: No stable id.
```
"""

    try:
        audio.extract_narration_segments(script)
    except audio.AudioError as exc:
        assert "beat.id" in str(exc)
    else:
        raise AssertionError("narration without beat id should fail")


def test_sync_narration_config_writes_generated_yaml(tmp_path: Path) -> None:
    script = tmp_path / "demo.md"
    script.write_text(
        """# Demo

```studio-directive
scene: Demo Scene
```

### Overview

```studio-directive
beat:
  id: overview
  heading: Overview
  narration: Welcome.
```

### First

```studio-directive
beat:
  id: first
  heading: First
  narration: Do the first thing.
```
""",
        encoding="utf-8",
    )
    output = tmp_path / "demo.yaml"
    spec = {
        "_recording_id": "demo",
        "script": str(script),
        "beats": [{"id": "first"}],
    }

    path = audio.sync_narration_config(spec, output_path=output)
    text = path.read_text(encoding="utf-8")

    assert path == output
    assert 'title: "Demo Scene"' in text
    assert 'id: "overview"' in text
    assert 'id: "first"' in text
    assert "Do the first thing." in text


def test_sync_narration_config_rejects_unknown_beat_id(tmp_path: Path) -> None:
    script = tmp_path / "demo.md"
    script.write_text(
        """# Demo

### Unknown

```studio-directive
beat:
  id: missing
  heading: Unknown
  narration: This beat is not in the recording config.
```
""",
        encoding="utf-8",
    )
    spec = {
        "_recording_id": "demo",
        "script": str(script),
        "beats": [{"id": "known"}],
    }

    try:
        audio.sync_narration_config(spec, output_path=tmp_path / "demo.yaml")
    except audio.AudioError as exc:
        assert "unknown beat id(s): missing" in str(exc)
    else:
        raise AssertionError("unknown beat id should fail sync")


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
            "billing": {"tts_usd_per_1m_characters": 20.0},
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
    assert settings.tts_usd_per_1m_characters == 20.0
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


def test_generate_audio_reuses_matching_cache_key_when_segment_id_changes(
    tmp_path: Path,
) -> None:
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
    old_segment = audio.NarrationSegment("old-id", "Old", "Same words.")
    new_segment = audio.NarrationSegment("new-id", "New", "Same words.")
    old_plan = audio.plan_audio("demo", [old_segment], settings)
    new_plan = audio.plan_audio("demo", [new_segment], settings)
    old_plan[0].output_path.parent.mkdir(parents=True)
    old_plan[0].output_path.write_bytes(b"cached-by-key")

    def fail_synthesize(
        segment: audio.NarrationSegment, settings: audio.AudioSettings
    ) -> bytes:
        raise AssertionError("cache key hit should not call synthesize")

    paths = audio.generate_audio(new_plan, settings, synthesize=fail_synthesize)

    assert paths == [new_plan[0].output_path]
    assert new_plan[0].output_path.read_bytes() == b"cached-by-key"


def test_openai_tts_billing_counts_only_generated_segments(tmp_path: Path) -> None:
    settings = audio.AudioSettings(
        enabled=True,
        provider="openai",
        env="OPENAI_TEST_KEY",
        model="gpt-4o-mini-tts",
        voice="marin",
        format="mp3",
        instructions="Speak clearly.",
        cache_dir=tmp_path,
    )
    segments = [
        audio.NarrationSegment("cached", "Cached", "Already generated."),
        audio.NarrationSegment("renamed", "Renamed", "Same cached words."),
        audio.NarrationSegment("missing", "Missing", "Fresh narration."),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"cached")
    old_plan = audio.plan_audio(
        "demo",
        [audio.NarrationSegment("old", "Old", "Same cached words.")],
        settings,
    )
    old_plan[0].output_path.write_bytes(b"reusable")

    items = audio.audio_items_requiring_synthesis(plan, settings)
    billing = audio.estimate_openai_tts_billing(items, settings)

    expected_characters = len("Fresh narration.") + len("Speak clearly.")
    assert [item.segment.segment_id for item in items] == ["missing"]
    assert billing.generated_segments == 1
    assert billing.billable_characters == expected_characters
    assert billing.estimated_cost_usd == (
        expected_characters * settings.tts_usd_per_1m_characters / 1_000_000
    )


def test_openai_tts_billing_force_counts_all_segments(tmp_path: Path) -> None:
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
    segments = [
        audio.NarrationSegment("first", "First", "One."),
        audio.NarrationSegment("second", "Second", "Two."),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"cached")

    items = audio.audio_items_requiring_synthesis(plan, settings, force=True)
    billing = audio.estimate_openai_tts_billing(items, settings)

    assert [item.segment.segment_id for item in items] == ["first", "second"]
    assert billing.billable_characters == len("One.") + len("Two.")


def test_openai_tts_billing_summary_prints_total_cost(capsys: Any) -> None:
    audio.print_openai_tts_billing_summary(
        audio.AudioBillingSummary(
            generated_segments=1,
            billable_characters=100,
            estimated_cost_usd=0.0015,
        )
    )

    assert capsys.readouterr().out == (
        "OpenAI TTS estimated cost this run: $0.001500\n"
    )


def test_audio_dry_run_prints_human_summary_by_default(
    tmp_path: Path,
    capsys: Any,
) -> None:
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
    segments = [
        audio.NarrationSegment("cached", "Cached", "Already generated."),
        audio.NarrationSegment("renamed", "Renamed", "Same cached words."),
        audio.NarrationSegment(
            "missing",
            "Missing",
            "This narration segment is intentionally long enough that the dry "
            "run should shorten it instead of printing the whole paragraph.",
        ),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    plan[0].output_path.parent.mkdir(parents=True)
    plan[0].output_path.write_bytes(b"cached")
    old_plan = audio.plan_audio(
        "demo",
        [audio.NarrationSegment("old", "Old", "Same cached words.")],
        settings,
    )
    old_plan[0].output_path.write_bytes(b"reusable")

    audio.print_plan(
        plan,
        settings,
        tmp_path / "published.mp3",
        tmp_path / "published.json",
    )

    out = capsys.readouterr().out
    assert "Audio dry run" in out
    assert "segments: 3" in out
    assert "1 cached, 1 reusable, 1 missing" in out
    assert "cached  " in out
    assert "renamed - Renamed" in out
    assert "reuses:" in out
    assert "This narration segment is intentionally long enough" in out
    assert "whole paragraph." not in out
    assert '{"' not in out


def test_audio_dry_run_json_output_remains_available(
    tmp_path: Path,
    capsys: Any,
) -> None:
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

    audio.print_plan(
        plan,
        settings,
        tmp_path / "published.mp3",
        tmp_path / "published.json",
        output_format="json",
    )

    lines = capsys.readouterr().out.splitlines()
    segment_payload = json.loads(lines[0])
    publish_payload = json.loads(lines[1])
    assert segment_payload["segment"] == "intro"
    assert segment_payload["status"] == "missing"
    assert segment_payload["text"] == "Hello world."
    assert publish_payload == {
        "published_audio": str(tmp_path / "published.mp3"),
        "published_audio_metadata": str(tmp_path / "published.json"),
    }


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
    assert segments[0].segment_id == "overview"
    assert any(segment.segment_id == "install-server" for segment in segments)
    assert len(plan) >= 1
    assert all(item.output_path.suffix == ".mp3" for item in plan)
    assert (
        audio.output_audio_path(spec, "install-and-bootstrap", settings)
        == REPO_ROOT / "website/static/audio/casts/install-and-bootstrap.mp3"
    )
    assert (
        audio.output_audio_metadata_path(
            spec,
            audio.output_audio_path(spec, "install-and-bootstrap", settings),
        )
        == REPO_ROOT / "website/static/audio/casts/install-and-bootstrap.json"
    )


def test_publish_audio_combines_cached_segments(tmp_path: Path) -> None:
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
    segments = [
        audio.NarrationSegment("intro", "Intro", "Hello."),
        audio.NarrationSegment("next", "Next", "Goodbye."),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    for item in plan:
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        item.output_path.write_bytes(b"audio")
    output = tmp_path / "published.mp3"
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], *, capture_output: bool, text: bool) -> Any:
        seen["command"] = command
        seen["capture_output"] = capture_output
        seen["text"] = text
        output.write_bytes(b"published")
        return subprocess.CompletedProcess(command, 0, "", "")

    path = audio.publish_audio(plan, output, ffmpeg="ffmpeg-test", run=fake_run)

    assert path == output
    assert output.read_bytes() == b"published"
    assert seen["command"][0] == "ffmpeg-test"
    assert seen["command"][-1] == str(output)
    assert seen["capture_output"] is True
    assert seen["text"] is True


def test_publish_audio_metadata_writes_segment_durations(tmp_path: Path) -> None:
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
    segments = [
        audio.NarrationSegment("overview", "Overview", "Hello."),
        audio.NarrationSegment("next", "Next", "Goodbye."),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    for item in plan:
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        item.output_path.write_bytes(b"audio")
    output = tmp_path / "published.mp3"
    output.write_bytes(b"published")
    metadata = tmp_path / "published.json"

    durations = {
        plan[0].output_path: "2.125\n",
        plan[1].output_path: "3.500\n",
        output: "5.625\n",
    }

    def fake_run(command: list[str], *, capture_output: bool, text: bool) -> Any:
        assert command[0] == "ffprobe-test"
        path = Path(command[-1])
        return subprocess.CompletedProcess(command, 0, durations[path], "")

    path = audio.publish_audio_metadata(
        "demo",
        plan,
        output,
        metadata,
        ffprobe="ffprobe-test",
        run=fake_run,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == metadata
    assert payload["recording"] == "demo"
    assert payload["audio"] == str(output)
    assert payload["duration"] == 5.625
    assert payload["segments"] == [
        {
            "id": "overview",
            "heading": "Overview",
            "text": "Hello.",
            "audio": str(plan[0].output_path),
            "offset": 0.0,
            "duration": 2.125,
        },
        {
            "id": "next",
            "heading": "Next",
            "text": "Goodbye.",
            "audio": str(plan[1].output_path),
            "offset": 2.125,
            "duration": 3.5,
        },
    ]
    ordered_dir = plan[0].output_path.parent / "ordered"
    ordered_names = sorted(path.name for path in ordered_dir.iterdir())
    assert ordered_names == [
        f"00m00s-00m02s-{plan[0].output_path.name}",
        f"00m02s-00m06s-{plan[1].output_path.name}",
    ]
    first_ordered = ordered_dir / ordered_names[0]
    if first_ordered.is_symlink():
        assert first_ordered.readlink() == Path("..") / plan[0].output_path.name
    else:
        assert first_ordered.read_bytes() == b"audio"


def test_validate_published_audio_metadata_accepts_current_plan(tmp_path: Path) -> None:
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
    plan = audio.plan_audio(
        "demo",
        [audio.NarrationSegment("overview", "Overview", "Hello.")],
        settings,
    )
    metadata = tmp_path / "published.json"
    metadata.write_text(
        json.dumps(
            {
                "recording": "demo",
                "audio": str(tmp_path / "published.mp3"),
                "duration": 1.0,
                "segments": [
                    {
                        "id": "overview",
                        "heading": "Overview",
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

    audio.validate_published_audio_metadata(plan, metadata)


def test_validate_published_audio_metadata_rejects_stale_text(tmp_path: Path) -> None:
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
    plan = audio.plan_audio(
        "demo",
        [audio.NarrationSegment("overview", "Overview", "New words.")],
        settings,
    )
    metadata = tmp_path / "published.json"
    metadata.write_text(
        json.dumps(
            {
                "recording": "demo",
                "audio": str(tmp_path / "published.mp3"),
                "duration": 1.0,
                "segments": [
                    {
                        "id": "overview",
                        "heading": "Overview",
                        "text": "Old words.",
                        "audio": str(tmp_path / "old.mp3"),
                        "offset": 0.0,
                        "duration": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        audio.validate_published_audio_metadata(plan, metadata)
    except audio.AudioError as exc:
        message = str(exc)
        assert "published audio metadata is stale" in message
        assert "segment 'overview' field 'text' is stale" in message
        assert "run audio_generate and audio_publish" in message
    else:
        raise AssertionError("stale audio metadata should fail validation")


def test_audio_metadata_includes_guide_for_matching_beat_segment(
    tmp_path: Path,
) -> None:
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
    segments = [
        audio.NarrationSegment("overview", "Overview", "Intro."),
        audio.NarrationSegment("init-staging", "Init Staging", "Start."),
    ]
    plan = audio.plan_audio("demo", segments, settings)
    for item in plan:
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        item.output_path.write_bytes(b"audio")
    output = tmp_path / "published.mp3"
    output.write_bytes(b"published")
    durations = {
        plan[0].output_path: "1.000\n",
        plan[1].output_path: "2.000\n",
        output: "3.000\n",
    }

    def fake_run(command: list[str], *, capture_output: bool, text: bool) -> Any:
        path = Path(command[-1])
        return subprocess.CompletedProcess(command, 0, durations[path], "")

    guides = audio.guide_by_segment_id_from_spec(
        {
            "beats": [
                {
                    "id": "init-staging",
                    "guide": {
                        "try_command": "test -f compose.yaml",
                        "success_hint": "The compose file should exist.",
                    },
                }
            ]
        },
        plan,
    )
    payload = audio.audio_metadata_payload(
        "demo",
        plan,
        output,
        guide_by_segment_id=guides,
        ffprobe="ffprobe-test",
        run=fake_run,
    )

    assert "guide" not in payload["segments"][0]
    assert payload["segments"][1]["guide"] == {
        "try_command": "test -f compose.yaml",
        "success_hint": "The compose file should exist.",
    }


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
