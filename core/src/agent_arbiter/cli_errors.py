from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import TextIO


def format_cli_error(
    message: str,
    *,
    area: str | None = None,
    details: Iterable[str] = (),
) -> str:
    area_text = f" {area}" if area else ""
    message_lines = message.splitlines() or [""]
    lines = [f"Agent Arbiter{area_text} error: {message_lines[0]}"]
    lines.extend(f"  {line}" for line in message_lines[1:])
    lines.extend(f"  {detail}" for detail in details)
    return "\n".join(lines)


def print_cli_error(
    message: str,
    *,
    area: str | None = None,
    details: Iterable[str] = (),
    file: TextIO | None = None,
) -> None:
    if file is None:
        file = sys.stderr
    print(format_cli_error(message, area=area, details=details), file=file)
