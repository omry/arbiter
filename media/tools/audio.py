#!/usr/bin/env python3
"""Generate cached narration audio from Arbiter media scripts."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only outside dev envs.
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDINGS_DIR = REPO_ROOT / "media" / "recordings"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
SUPPORTED_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
SUPPORTED_TIMESTAMP_GRANULARITIES = {"word", "segment"}
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_TIMESTAMP_GRANULARITIES = ("word", "segment")


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarrationSegment:
    segment_id: str
    heading: str
    text: str


@dataclass(frozen=True)
class AudioSettings:
    enabled: bool
    provider: str
    env: str
    model: str
    voice: str
    format: str
    cache_dir: Path
    instructions: str | None = None


@dataclass(frozen=True)
class TranscriptionSettings:
    model: str
    timestamp_granularities: tuple[str, ...]


@dataclass(frozen=True)
class AudioPlanItem:
    segment: NarrationSegment
    cache_key: str
    output_path: Path


def load_manifest(recording_id: str) -> dict[str, Any]:
    if yaml is None:
        raise AudioError("PyYAML is required to read recording manifests")
    path = RECORDINGS_DIR / f"{recording_id}.yaml"
    if not path.exists():
        raise AudioError(f"recording manifest not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AudioError(f"recording manifest must be a mapping: {path}")
    data["_manifest_path"] = str(path)
    return data


def as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AudioError(f"manifest field {field!r} must be a mapping")
    return value


def require_string(mapping: dict[str, Any], key: str, *, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise AudioError(f"{field}.{key} must be a non-empty string")
    return value


def relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "segment"


def normalize_narration_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].strip()
    return text


def extract_narration_segments(script_text: str) -> list[NarrationSegment]:
    segments: list[NarrationSegment] = []
    heading = "intro"
    heading_counts: dict[str, int] = {}
    lines = script_text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        heading_match = re.fullmatch(r"#{1,6}\s+(.+)", stripped)
        if heading_match:
            heading = heading_match.group(1).strip()
            index += 1
            continue
        if stripped != "Narration:":
            index += 1
            continue

        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        block: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if candidate_stripped.startswith("```"):
                break
            if candidate_stripped.endswith(":") and not block:
                break
            block.append(candidate_stripped)
            index += 1
            if len(block) > 1 and block[0].startswith('"') and block[-1].endswith('"'):
                break
        text = normalize_narration_text(strip_wrapping_quotes(" ".join(block)))
        if text:
            base_id = slugify(heading)
            heading_counts[base_id] = heading_counts.get(base_id, 0) + 1
            suffix = heading_counts[base_id]
            segment_id = base_id if suffix == 1 else f"{base_id}-{suffix}"
            segments.append(
                NarrationSegment(segment_id=segment_id, heading=heading, text=text)
            )
    return segments


def audio_settings(spec: dict[str, Any]) -> AudioSettings:
    audio = as_mapping(spec.get("audio"), field="audio")
    provider = audio.get("provider", "openai")
    if provider != "openai":
        raise AudioError(f"unsupported audio provider: {provider}")
    fmt = require_string(audio, "format", field="audio")
    if fmt not in SUPPORTED_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_FORMATS))
        raise AudioError(f"audio.format must be one of: {supported}")
    instructions = audio.get("instructions")
    if instructions is not None and (
        not isinstance(instructions, str) or not instructions
    ):
        raise AudioError("audio.instructions must be a non-empty string")
    cache_dir = audio.get("cache_dir", "media/cache/audio")
    if not isinstance(cache_dir, str) or not cache_dir:
        raise AudioError("audio.cache_dir must be a non-empty string")
    return AudioSettings(
        enabled=bool(audio.get("enabled", False)),
        provider=provider,
        env=require_string(audio, "env", field="audio"),
        model=require_string(audio, "model", field="audio"),
        voice=require_string(audio, "voice", field="audio"),
        format=fmt,
        instructions=instructions,
        cache_dir=relative_path(cache_dir),
    )


def transcription_settings(spec: dict[str, Any]) -> TranscriptionSettings:
    audio = as_mapping(spec.get("audio"), field="audio")
    transcription = as_mapping(
        audio.get("transcription"), field="audio.transcription"
    )
    model = transcription.get("model", DEFAULT_TRANSCRIPTION_MODEL)
    if not isinstance(model, str) or not model:
        raise AudioError("audio.transcription.model must be a non-empty string")
    granularities = transcription.get(
        "timestamp_granularities", list(DEFAULT_TIMESTAMP_GRANULARITIES)
    )
    if isinstance(granularities, str):
        granularities = [granularities]
    if not isinstance(granularities, list) or not granularities:
        raise AudioError(
            "audio.transcription.timestamp_granularities must be a non-empty list"
        )
    normalized: list[str] = []
    for granularity in granularities:
        if not isinstance(granularity, str):
            raise AudioError(
                "audio.transcription.timestamp_granularities entries must be strings"
            )
        if granularity not in SUPPORTED_TIMESTAMP_GRANULARITIES:
            supported = ", ".join(sorted(SUPPORTED_TIMESTAMP_GRANULARITIES))
            raise AudioError(
                "audio.transcription.timestamp_granularities entries must be "
                f"one of: {supported}"
            )
        if granularity not in normalized:
            normalized.append(granularity)
    return TranscriptionSettings(
        model=model,
        timestamp_granularities=tuple(normalized),
    )


def script_path_from_manifest(spec: dict[str, Any]) -> Path:
    script = spec.get("script")
    if not isinstance(script, str) or not script:
        raise AudioError("manifest field 'script' must be a non-empty string")
    return relative_path(script)


def load_narration_segments(spec: dict[str, Any]) -> list[NarrationSegment]:
    script_path = script_path_from_manifest(spec)
    if not script_path.exists():
        raise AudioError(f"script not found: {script_path}")
    segments = extract_narration_segments(script_path.read_text(encoding="utf-8"))
    if not segments:
        raise AudioError(f"no Narration blocks found in {script_path}")
    return segments


def segment_cache_key(segment: NarrationSegment, settings: AudioSettings) -> str:
    payload = {
        "provider": settings.provider,
        "model": settings.model,
        "voice": settings.voice,
        "format": settings.format,
        "instructions": settings.instructions,
        "text": normalize_narration_text(segment.text),
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


def plan_audio(
    recording_id: str,
    segments: list[NarrationSegment],
    settings: AudioSettings,
) -> list[AudioPlanItem]:
    items: list[AudioPlanItem] = []
    recording_dir = settings.cache_dir / recording_id
    for segment in segments:
        cache_key = segment_cache_key(segment, settings)
        filename = f"{segment.segment_id}-{cache_key}.{settings.format}"
        items.append(
            AudioPlanItem(
                segment=segment,
                cache_key=cache_key,
                output_path=recording_dir / filename,
            )
        )
    return items


def openai_speech_bytes(
    segment: NarrationSegment,
    settings: AudioSettings,
    *,
    environ: dict[str, str] | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> bytes:
    env = os.environ if environ is None else environ
    api_key = env.get(settings.env)
    if not api_key:
        raise AudioError(f"missing OpenAI API key environment variable: {settings.env}")

    payload: dict[str, Any] = {
        "model": settings.model,
        "input": segment.text,
        "voice": settings.voice,
        "response_format": settings.format,
    }
    if settings.instructions:
        payload["instructions"] = settings.instructions
    request = urllib.request.Request(
        OPENAI_SPEECH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.fp is None:
            detail = exc.msg
        else:
            detail = exc.read().decode("utf-8", errors="replace")
        raise AudioError(
            f"OpenAI speech request failed: HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AudioError(f"OpenAI speech request failed: {exc.reason}") from exc


def generate_audio(
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    *,
    force: bool = False,
    synthesize: Callable[
        [NarrationSegment, AudioSettings], bytes
    ] = openai_speech_bytes,
) -> list[Path]:
    written: list[Path] = []
    for item in plan:
        if item.output_path.exists() and not force:
            written.append(item.output_path)
            continue
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        audio_bytes = synthesize(item.segment, settings)
        if not audio_bytes:
            raise AudioError(
                f"audio provider returned no data for {item.segment.segment_id}"
            )
        item.output_path.write_bytes(audio_bytes)
        written.append(item.output_path)
    return written


def multipart_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")


def encode_multipart_form(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
    *,
    boundary: str = "arbiter-media-boundary",
) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            'Content-Disposition: form-data; '
            f'name="{multipart_escape(name)}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, content_type, data in files:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            'Content-Disposition: form-data; '
            f'name="{multipart_escape(name)}"; '
            f'filename="{multipart_escape(filename)}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def audio_content_type(path: Path) -> str:
    if path.suffix == ".mp3":
        return "audio/mpeg"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def timeline_path_for(item: AudioPlanItem) -> Path:
    return item.output_path.with_suffix(".timeline.json")


def openai_transcription_json(
    item: AudioPlanItem,
    settings: AudioSettings,
    transcription: TranscriptionSettings,
    *,
    environ: dict[str, str] | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    api_key = env.get(settings.env)
    if not api_key:
        raise AudioError(f"missing OpenAI API key environment variable: {settings.env}")
    if not item.output_path.exists():
        raise AudioError(f"audio file not found: {item.output_path}")

    fields = [
        ("model", transcription.model),
        ("response_format", "verbose_json"),
    ]
    for granularity in transcription.timestamp_granularities:
        fields.append(("timestamp_granularities[]", granularity))
    body, content_type = encode_multipart_form(
        fields,
        [
            (
                "file",
                item.output_path.name,
                audio_content_type(item.output_path),
                item.output_path.read_bytes(),
            )
        ],
    )
    request = urllib.request.Request(
        OPENAI_TRANSCRIPTIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.fp is None:
            detail = exc.msg
        else:
            detail = exc.read().decode("utf-8", errors="replace")
        raise AudioError(
            f"OpenAI transcription request failed: HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AudioError(
            f"OpenAI transcription request failed: {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise AudioError("OpenAI transcription response was not JSON") from exc
    if not isinstance(data, dict):
        raise AudioError("OpenAI transcription response must be a JSON object")
    return data


def timeline_payload(
    recording_id: str,
    item: AudioPlanItem,
    transcription: TranscriptionSettings,
    response: dict[str, Any],
) -> dict[str, Any]:
    return {
        "recording": recording_id,
        "segment": item.segment.segment_id,
        "heading": item.segment.heading,
        "cache_key": item.cache_key,
        "audio": display_path(item.output_path),
        "transcription_model": transcription.model,
        "timestamp_granularities": list(transcription.timestamp_granularities),
        "source_text": item.segment.text,
        "transcript": response.get("text"),
        "duration": response.get("duration"),
        "language": response.get("language"),
        "words": response.get("words", []),
        "segments": response.get("segments", []),
        "usage": response.get("usage"),
    }


def generate_timestamps(
    recording_id: str,
    plan: list[AudioPlanItem],
    settings: AudioSettings,
    transcription: TranscriptionSettings,
    *,
    force: bool = False,
    transcribe: Callable[
        [AudioPlanItem, AudioSettings, TranscriptionSettings], dict[str, Any]
    ] = openai_transcription_json,
) -> list[Path]:
    written: list[Path] = []
    for item in plan:
        timeline_path = timeline_path_for(item)
        if timeline_path.exists() and not force:
            written.append(timeline_path)
            continue
        response = transcribe(item, settings, transcription)
        payload = timeline_payload(recording_id, item, transcription, response)
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(timeline_path)
    return written


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def print_plan(plan: list[AudioPlanItem]) -> None:
    for item in plan:
        print(
            json.dumps(
                {
                    "segment": item.segment.segment_id,
                    "heading": item.segment.heading,
                    "cache_key": item.cache_key,
                    "output": display_path(item.output_path),
                    "timeline": display_path(timeline_path_for(item)),
                    "text": item.segment.text,
                },
                sort_keys=True,
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recording", help="Recording id, for example install-and-bootstrap."
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate narration plan only."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print planned audio segments."
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate cached audio files."
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Generate transcription timestamp sidecars for generated audio.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec = load_manifest(args.recording)
        settings = audio_settings(spec)
        transcription = transcription_settings(spec)
        segments = load_narration_segments(spec)
        plan = plan_audio(args.recording, segments, settings)
        if args.dry_run:
            print_plan(plan)
            return 0
        if args.check:
            state = "enabled" if settings.enabled else "disabled"
            print(
                f"ok: {args.recording} audio {state}; "
                f"{len(plan)} narration segment(s), provider {settings.provider}, "
                f"transcription {transcription.model}"
            )
            return 0
        if not settings.enabled:
            raise AudioError("audio is disabled in the recording manifest")
        paths = generate_audio(plan, settings, force=args.force)
        for path in paths:
            print(f"audio {display_path(path)}")
        if args.timestamps:
            timeline_paths = generate_timestamps(
                args.recording,
                plan,
                settings,
                transcription,
                force=args.force,
            )
            for path in timeline_paths:
                print(f"timeline {display_path(path)}")
        return 0
    except AudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
