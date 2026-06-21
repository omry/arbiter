#!/usr/bin/env python3
"""Generate a chained narration demo, timestamp it, and split sentence clips."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import audio


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "media" / "cache" / "audio" / "sentence-timing-demo"
DEFAULT_SENTENCES = [
    "First, create a dedicated Python virtual environment for Arbiter.",
    "Next, install the Arbiter suite into that environment.",
    "Then, use the same environment for both server and client commands.",
]


@dataclass(frozen=True)
class SentenceSpan:
    index: int
    text: str
    start: float
    end: float
    raw_start: float
    raw_end: float
    matched_words: int
    source_words: int
    confidence: float
    clip_path: Path


def load_repo_env(path: Path = REPO_ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def sentence_tokens(sentence: str) -> list[str]:
    return [token for token in (normalize_token(part) for part in sentence.split()) if token]


def word_token(word: dict[str, Any]) -> str:
    value = word.get("word", "")
    if not isinstance(value, str):
        return ""
    return normalize_token(value)


def sentence_set_id(
    sentences: list[str],
    *,
    model: str,
    voice: str,
    audio_format: str,
    pause_ms: int,
) -> str:
    payload = {
        "sentences": sentences,
        "model": model,
        "voice": voice,
        "format": audio_format,
        "pause_ms": pause_ms,
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:12]


def align_sentences_to_words(
    sentences: list[str],
    words: list[dict[str, Any]],
    *,
    pre_roll_seconds: float,
    post_roll_seconds: float,
    previous_tail_guard_seconds: float,
    duration: float | None,
    out_dir: Path,
) -> list[SentenceSpan]:
    source: list[tuple[int, str]] = []
    source_counts: list[int] = []
    for sentence_index, sentence in enumerate(sentences):
        tokens = sentence_tokens(sentence)
        source_counts.append(len(tokens))
        source.extend((sentence_index, token) for token in tokens)

    source_only = [token for _, token in source]
    transcript_only = [word_token(word) for word in words]
    matcher = difflib.SequenceMatcher(None, source_only, transcript_only, autojunk=False)
    matches_by_sentence: dict[int, list[int]] = {index: [] for index in range(len(sentences))}
    for tag, source_start, source_end, transcript_start, _transcript_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset, transcript_index in enumerate(range(transcript_start, transcript_start + source_end - source_start)):
            sentence_index = source[source_start + offset][0]
            matches_by_sentence[sentence_index].append(transcript_index)

    spans: list[dict[str, Any]] = []
    for sentence_index, sentence in enumerate(sentences):
        matched = matches_by_sentence[sentence_index]
        if not matched:
            raise audio.AudioError(f"could not align sentence {sentence_index + 1}: {sentence}")
        starts = [float(words[index]["start"]) for index in matched]
        ends = [float(words[index]["end"]) for index in matched]
        raw_start = min(starts)
        raw_end = max(ends)
        spans.append(
            {
                "index": sentence_index + 1,
                "text": sentence,
                "raw_start": raw_start,
                "raw_end": raw_end,
                "start": max(0.0, raw_start - pre_roll_seconds),
                "end": raw_end + post_roll_seconds,
                "matched_words": len(matched),
                "source_words": source_counts[sentence_index],
            }
        )

    for current, following in zip(spans, spans[1:]):
        if current["end"] > following["start"]:
            boundary = (current["raw_end"] + following["raw_start"]) / 2.0
            current["end"] = min(current["end"], boundary)
            following["start"] = max(following["start"], boundary)
        else:
            following["start"] = max(
                current["raw_end"] + previous_tail_guard_seconds,
                following["start"],
            )
    if duration is not None:
        for span in spans:
            span["end"] = min(float(duration), span["end"])

    result: list[SentenceSpan] = []
    for span in spans:
        index = int(span["index"])
        clip_path = out_dir / f"sentence-{index:02d}.wav"
        source_words = int(span["source_words"])
        matched_words = int(span["matched_words"])
        confidence = matched_words / source_words if source_words else 0.0
        result.append(
            SentenceSpan(
                index=index,
                text=str(span["text"]),
                start=float(span["start"]),
                end=float(span["end"]),
                raw_start=float(span["raw_start"]),
                raw_end=float(span["raw_end"]),
                matched_words=matched_words,
                source_words=source_words,
                confidence=confidence,
                clip_path=clip_path,
            )
        )
    return result


def split_audio(audio_path: Path, spans: list[SentenceSpan]) -> None:
    for span in spans:
        duration = span.end - span.start
        if duration <= 0:
            raise audio.AudioError(f"invalid sentence duration for {span.index}")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-vn",
                "-af",
                (
                    f"atrim=start={span.start:.3f}:end={span.end:.3f},"
                    "asetpts=PTS-STARTPTS"
                ),
                "-ac",
                "1",
                "-ar",
                "24000",
                str(span.clip_path),
            ],
            check=True,
        )


def probe_duration(path: Path) -> float | None:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def span_to_json(span: SentenceSpan) -> dict[str, Any]:
    return {
        "index": span.index,
        "text": span.text,
        "start": span.start,
        "end": span.end,
        "raw_start": span.raw_start,
        "raw_end": span.raw_end,
        "matched_words": span.matched_words,
        "source_words": span.source_words,
        "confidence": span.confidence,
        "clip": audio.display_path(span.clip_path),
        "clip_duration": probe_duration(span.clip_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--api-key-env", default="OPENAI_ARBITER_CINEMA_AUDIO_API_KEY")
    parser.add_argument("--tts-model", default="gpt-4o-mini-tts")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--format", default="mp3")
    parser.add_argument("--transcription-model", default="whisper-1")
    parser.add_argument(
        "--sentence",
        action="append",
        dest="sentences",
        help="Sentence to include in the demo. Repeat for multiple sentences.",
    )
    parser.add_argument("--pause-ms", type=int, default=350)
    parser.add_argument("--pre-roll-seconds", type=float, default=0.55)
    parser.add_argument("--post-roll-seconds", type=float, default=0.08)
    parser.add_argument("--previous-tail-guard-seconds", type=float, default=0.12)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        load_repo_env()
        out_dir = audio.relative_path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        sentences = args.sentences or DEFAULT_SENTENCES
        sample_id = sentence_set_id(
            sentences,
            model=args.tts_model,
            voice=args.voice,
            audio_format=args.format,
            pause_ms=args.pause_ms,
        )
        sample_dir = out_dir / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        full_audio_path = sample_dir / f"chained-sentences.{args.format}"
        transcript_path = sample_dir / "transcription.json"
        timeline_path = sample_dir / "sentence-timeline.json"
        text = "\n\n".join(sentences)
        instructions = (
            "Speak as one natural tutorial voice. Pause briefly and clearly "
            f"for about {args.pause_ms} milliseconds after each sentence."
        )
        settings = audio.AudioSettings(
            enabled=True,
            provider="openai",
            env=args.api_key_env,
            model=args.tts_model,
            voice=args.voice,
            format=args.format,
            instructions=instructions,
            cache_dir=out_dir,
        )
        segment = audio.NarrationSegment(
            segment_id="chained-sentences",
            heading="Sentence Timing Demo",
            text=text,
        )
        item = audio.AudioPlanItem(
            segment=segment,
            cache_key="sentence-demo",
            output_path=full_audio_path,
        )
        if args.force or not full_audio_path.exists():
            full_audio_path.write_bytes(audio.openai_speech_bytes(segment, settings))

        transcription_settings = audio.TranscriptionSettings(
            model=args.transcription_model,
            timestamp_granularities=("word", "segment"),
        )
        if args.force or not transcript_path.exists():
            transcript = audio.openai_transcription_json(
                item,
                settings,
                transcription_settings,
            )
            transcript_path.write_text(
                json.dumps(transcript, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

        words = transcript.get("words", [])
        if not isinstance(words, list) or not words:
            raise audio.AudioError("transcription response did not include word timestamps")
        duration = transcript.get("duration")
        duration_value = float(duration) if isinstance(duration, (int, float)) else probe_duration(full_audio_path)
        spans = align_sentences_to_words(
            sentences,
            words,
            pre_roll_seconds=args.pre_roll_seconds,
            post_roll_seconds=args.post_roll_seconds,
            previous_tail_guard_seconds=args.previous_tail_guard_seconds,
            duration=duration_value,
            out_dir=sample_dir,
        )
        split_audio(full_audio_path, spans)
        timeline = {
            "audio": audio.display_path(full_audio_path),
            "transcript": transcript.get("text"),
            "duration": duration_value,
            "sentences": [span_to_json(span) for span in spans],
        }
        timeline_path.write_text(
            json.dumps(timeline, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        print(f"audio {audio.display_path(full_audio_path)}")
        print(f"transcript {audio.display_path(transcript_path)}")
        print(f"timeline {audio.display_path(timeline_path)}")
        for span in spans:
            print(
                f"sentence {span.index}: {span.start:.3f}-{span.end:.3f}s "
                f"confidence={span.confidence:.2f} {audio.display_path(span.clip_path)}"
            )
        return 0
    except (audio.AudioError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
