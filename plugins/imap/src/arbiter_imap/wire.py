"""Validation and quoting for values used in IMAP command arguments."""

from arbiter_server.errors import PluginValidationError


def validate_imap_string(value: str, *, argument: str) -> None:
    # Reject, rather than strip, ASCII controls so input cannot change the
    # command's framing or be normalized into a different argument.
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PluginValidationError(
            "imap", f"IMAP {argument} must not contain control characters"
        )


def quote_imap_string(value: str, *, argument: str) -> str:
    validate_imap_string(value, argument=argument)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
