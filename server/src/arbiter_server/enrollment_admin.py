"""Local enrollment administration; pairing secrets never enter CLI arguments."""

from __future__ import annotations

import getpass
import json
from pathlib import Path
import sys
import warnings

from .cli_errors import print_cli_error
from .local_control import enrollment_request


def _list_all(state_dir: Path) -> dict[str, object]:
    arguments: dict[str, object] = {}
    rows: list[object] = []
    while True:
        result = enrollment_request(state_dir, "list", arguments)
        page = result.get("requests")
        if not isinstance(page, list):
            raise ValueError("Invalid enrollment list response.")
        rows.extend(page)
        cursor = result.get("next_cursor")
        if cursor is None:
            return {**result, "requests": rows, "next_cursor": None}
        if not isinstance(cursor, str) or not page:
            raise ValueError("Invalid enrollment list cursor response.")
        arguments = {"cursor": cursor}


def enrollment_cli(
    command: str,
    state_dir: Path,
    *,
    selector: str | None = None,
    grants: list[str] | None = None,
    as_json: bool = False,
) -> int:
    try:
        arguments: dict[str, object] = {}
        if command in {"approve", "reject"}:
            listing = enrollment_request(state_dir, "list", {"selector": selector})
            selected = next(
                (
                    row
                    for row in listing.get("requests", [])
                    if isinstance(row, dict) and row.get("selector") == selector
                ),
                None,
            )
            if selected is None:
                raise ValueError(
                    "Enrollment request is missing or expired; list again."
                )
            arguments = {
                key: selected[key] for key in ("selector", "revision", "request_digest")
            }
            if command == "approve":
                if not sys.stdin.isatty():
                    raise ValueError(
                        "Approval requires an interactive terminal for hidden secret input."
                    )
                # JSON escapes untrusted display labels, including terminal and
                # bidirectional controls. This is a summary, not identity proof.
                print(json.dumps(selected, sort_keys=True), file=sys.stderr)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    secret = getpass.getpass("Pairing secret (input hidden): ")
                arguments.update(psk=secret, grants=grants or [])
        result = (
            _list_all(state_dir)
            if command == "list"
            else enrollment_request(state_dir, command, arguments)
        )
    except (OSError, ValueError, getpass.GetPassWarning, EOFError) as exc:
        print_cli_error(str(exc), area="enrollment")
        return 1
    print(json.dumps(result, sort_keys=True, indent=None if as_json else 2))
    return 0
