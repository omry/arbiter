"""Bounded public-issuer commands over the existing authenticated local control."""

import base64
import json
from pathlib import Path
import sqlite3
import sys

from . import authority as a
from .cli_errors import print_cli_error
from .local_control import _exchange
from .owner_issuers import control


def issuer_cli(
    command: str,
    state_dir: Path,
    *,
    certificate_file: Path | None = None,
    issuer_sha256: str | None = None,
    cursor: str | None = None,
    as_json: bool = False,
) -> int:
    try:
        arguments: dict[str, object] = {}
        if command == "register":
            if certificate_file is None:
                raise ValueError("select --certificate-file with public owner DER")
            with certificate_file.open("rb") as handle:
                der = handle.read(4097)
            if not 1 <= len(der) <= 4096:
                raise ValueError("owner DER must contain 1–4096 bytes")
            arguments["certificate_der"] = base64.b64encode(der).decode()
        elif command in {"export", "remove"}:
            arguments["issuer_sha256"] = issuer_sha256
        elif cursor is not None:
            arguments["cursor"] = cursor
        root = a._root(state_dir, create=False)
        try:
            with a._locked(root):
                result = control(root, command, arguments)
        except a.AuthorityBusy:
            result = _exchange(
                root,
                {
                    "root": str(root),
                    "action": "owner-issuer",
                    "command": command,
                    "arguments": arguments,
                },
            )
        if command == "export":
            encoded = result.get("certificate_der")
            if not isinstance(encoded, str) or len(encoded) > 5464:
                raise ValueError("invalid public issuer export")
            sys.stdout.buffer.write(base64.b64decode(encoded, validate=True))
        else:
            print(json.dumps(result, sort_keys=True, indent=None if as_json else 2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print_cli_error(str(exc), area="owner-issuer")
        return 1
