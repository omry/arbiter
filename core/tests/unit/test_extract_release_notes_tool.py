from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable, cast

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "extract_release_notes"


def _load_tool() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("extract_release_notes", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load extract_release_notes module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _extract_release_notes() -> Callable[..., str]:
    return cast(Callable[..., str], getattr(_load_tool(), "extract_release_notes"))


def _main() -> Callable[..., int]:
    return cast(Callable[..., int], getattr(_load_tool(), "main"))


def test_extract_release_notes_returns_requested_section() -> None:
    notes = """# Release Notes

<!-- towncrier release notes start -->

# Agent Arbiter 0.9.0 (2026-06-01)

## Features

- Publish the initial packages.

# Agent Arbiter 0.8.0 (2026-05-01)

## Bugfixes

- Older note.
"""

    assert (
        _extract_release_notes()(notes, "0.9.0")
        == """# Agent Arbiter 0.9.0 (2026-06-01)

## Features

- Publish the initial packages.
"""
    )


def test_extract_release_notes_rejects_missing_version() -> None:
    with pytest.raises(ValueError, match="does not contain release notes"):
        _extract_release_notes()("# Release Notes\n", "0.9.0")


def test_extract_release_notes_uses_package_key_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes_path = tmp_path / "imap" / "NEWS.md"
    notes_path.parent.mkdir()
    notes_path.write_text(
        """# Release Notes

<!-- towncrier release notes start -->

# Agent Arbiter IMAP 0.9.0 (2026-06-01)

## Features

- Publish the IMAP plugin.
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert _main()(["0.9.0", "--package-key", "imap"]) == 0

    assert "Publish the IMAP plugin." in capsys.readouterr().out
