from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_sentence_demo_tool() -> Any:
    tools_dir = REPO_ROOT / "media" / "tools"
    path = tools_dir / "sentence_timing_demo.py"
    sys.path.insert(0, str(tools_dir))
    try:
        spec = importlib.util.spec_from_file_location("sentence_timing_demo", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["sentence_timing_demo"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(tools_dir))


demo = load_sentence_demo_tool()


def test_sentence_set_id_changes_when_sentences_change() -> None:
    base = demo.sentence_set_id(
        ["One sentence.", "Another sentence."],
        model="gpt-4o-mini-tts",
        voice="marin",
        audio_format="mp3",
        pause_ms=350,
    )
    changed = demo.sentence_set_id(
        ["One sentence.", "Different sentence."],
        model="gpt-4o-mini-tts",
        voice="marin",
        audio_format="mp3",
        pause_ms=350,
    )

    assert len(base) == 12
    assert base != changed


def test_align_sentences_to_words_returns_sentence_spans(tmp_path: Path) -> None:
    sentences = [
        "First, create a virtual environment.",
        "Next, install Arbiter.",
    ]
    words = [
        {"word": "First", "start": 0.0, "end": 0.3},
        {"word": "create", "start": 0.4, "end": 0.7},
        {"word": "a", "start": 0.8, "end": 0.9},
        {"word": "virtual", "start": 1.0, "end": 1.3},
        {"word": "environment", "start": 1.4, "end": 1.9},
        {"word": "Next", "start": 2.4, "end": 2.7},
        {"word": "install", "start": 2.8, "end": 3.2},
        {"word": "Arbiter", "start": 3.3, "end": 3.7},
    ]

    spans = demo.align_sentences_to_words(
        sentences,
        words,
        pre_roll_seconds=0.05,
        post_roll_seconds=0.05,
        previous_tail_guard_seconds=0.0,
        duration=4.0,
        out_dir=tmp_path,
    )

    assert len(spans) == 2
    assert spans[0].start == 0.0
    assert spans[0].end == 1.95
    assert spans[0].confidence == 1.0
    assert spans[0].clip_path == tmp_path / "sentence-01.wav"
    assert spans[1].start == 2.35
    assert spans[1].end == 3.75
    assert spans[1].confidence == 1.0
    assert spans[1].clip_path == tmp_path / "sentence-02.wav"


def test_align_sentences_to_words_reports_partial_confidence(tmp_path: Path) -> None:
    sentences = [
        "First, create a virtual environment.",
        "Next, install Arbiter.",
    ]
    words = [
        {"word": "First", "start": 0.0, "end": 0.3},
        {"word": "create", "start": 0.4, "end": 0.7},
        {"word": "environment", "start": 1.4, "end": 1.9},
        {"word": "Next", "start": 2.4, "end": 2.7},
        {"word": "install", "start": 2.8, "end": 3.2},
        {"word": "Arbiter", "start": 3.3, "end": 3.7},
    ]

    spans = demo.align_sentences_to_words(
        sentences,
        words,
        pre_roll_seconds=0.0,
        post_roll_seconds=0.0,
        previous_tail_guard_seconds=0.0,
        duration=None,
        out_dir=tmp_path,
    )

    assert spans[0].matched_words == 3
    assert spans[0].source_words == 5
    assert spans[0].confidence == 0.6
    assert spans[1].confidence == 1.0
