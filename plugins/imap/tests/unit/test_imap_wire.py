from __future__ import annotations

import imaplib
from typing import Any

import pytest

from arbiter_imap import IMAPRuntime, IMAPServicePlugin
from arbiter_imap.client import IMAPClient
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
)
from arbiter_server.errors import PluginValidationError
from arbiter_server.services import RuntimeRegistry, ServicePluginContext


class WireIMAP(imaplib.IMAP4):
    """Use stdlib command serialization, capturing bytes instead of a socket."""

    def __init__(self) -> None:
        self.state = "AUTH"
        self.literal = None
        # imaplib uses bytes here; the supported type stubs declare str.
        setattr(self, "tagpre", b"A")
        self.tagnum = 0
        self.tagged_commands = {}
        self.untagged_responses = {}
        self.is_readonly = False
        self._encoding = "ascii"
        self.utf8_enabled = False
        self.debug = 0
        self.wire: list[bytes] = []

    def send(self, data: Any) -> None:
        self.wire.append(bytes(data))

    def _log(self, line: str) -> None:
        pass

    def _get_tagged_response(
        self, tag: bytes, *, expect_bye: bool = False
    ) -> tuple[str, list[bytes]]:
        return "OK", []

    def _get_response(self) -> None:
        return None  # Accept an APPEND literal continuation.

    def shutdown(self) -> None:
        pass


def wire_client(monkeypatch: pytest.MonkeyPatch) -> tuple[IMAPClient, WireIMAP]:
    server = WireIMAP()
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    return client, server


INJECTION = "safe\r\nX1 DELETE INBOX\r\nX2 NOOP"
CONTROLS = ["\r", "\n", "\r\n", "\x00", "\t", "\x1f", "\x7f"]


@pytest.mark.parametrize("query", [INJECTION, *[f"a{c}b" for c in CONTROLS]])
def test_search_injection_never_reaches_imap_wire(monkeypatch, query):
    client, server = wire_client(monkeypatch)

    with pytest.raises(PluginValidationError) as caught:
        client.search_messages(folder="INBOX", query=query, limit=1)

    assert server.wire == []
    assert caught.value.to_dict()["error"] == {
        "code": "validation_error",
        "message": "Invalid plugin operation arguments.",
        "plugin": "imap",
        "domain_code": "invalid_arguments",
        "retry": {"transient": "no", "replay": "safe"},
    }
    assert query not in str(caught.value.to_dict())


MAILBOX_OPERATIONS: list[tuple[str, dict[str, Any], str]] = [
    ("test_connection", {"folders": ["INBOX"]}, "folders"),
    ("list_messages", {"folder": "INBOX", "limit": 1}, "folder"),
    ("get_message", {"folder": "INBOX", "uid": "42"}, "folder"),
    (
        "get_attachment",
        {"folder": "INBOX", "uid": "42", "attachment_id": "part-1"},
        "folder",
    ),
    ("search_messages", {"folder": "INBOX", "query": "invoice", "limit": 1}, "folder"),
    (
        "move_message",
        {"source_folder": "INBOX", "destination_folder": "Archive", "uid": "42"},
        "source_folder",
    ),
    (
        "move_message",
        {"source_folder": "INBOX", "destination_folder": "Archive", "uid": "42"},
        "destination_folder",
    ),
    ("mark_message_read", {"folder": "INBOX", "uid": "42", "read": True}, "folder"),
    ("get_message_flags", {"folder": "INBOX", "uid": "42"}, "folder"),
    (
        "update_message_flags",
        {"folder": "INBOX", "uid": "42", "add_flags": ["\\Seen"], "remove_flags": []},
        "folder",
    ),
    ("delete_message", {"folder": "INBOX", "uid": "42"}, "folder"),
    ("append_message", {"folder": "Drafts", "message_bytes": b"body\r\n"}, "folder"),
]


@pytest.mark.parametrize("operation,arguments,argument", MAILBOX_OPERATIONS)
@pytest.mark.parametrize("control", CONTROLS)
def test_mailbox_controls_are_rejected_before_any_command(
    monkeypatch, operation, arguments, argument, control
):
    client, server = wire_client(monkeypatch)
    value = f"INBOX{control}X1 DELETE INBOX"
    arguments = {
        **arguments,
        argument: ["INBOX", value] if argument == "folders" else value,
    }

    with pytest.raises(PluginValidationError):
        getattr(client, operation)(**arguments)

    assert server.wire == []


def test_quotes_and_backslashes_stay_inside_one_search_argument(monkeypatch):
    client, server = wire_client(monkeypatch)
    client.search_messages(folder='My "Inbox"\\Folder', query='a"\\b', limit=1)

    assert server.wire == [
        b'A0 EXAMINE "My \\"Inbox\\"\\\\Folder"\r\n',
        b'A1 UID SEARCH TEXT "a\\"\\\\b"\r\n',
        b"A2 LOGOUT\r\n",
    ]


def test_append_keeps_message_newlines_in_a_literal(monkeypatch):
    client, server = wire_client(monkeypatch)
    message = b"Subject: draft\r\n\r\nX1 DELETE INBOX\r\n"
    client.append_message(folder='My "Drafts"', message_bytes=message)

    assert server.wire == [
        b'A0 APPEND "My \\"Drafts\\"" (\\Seen) {'
        + str(len(message)).encode()
        + b"}\r\n",
        message,
        b"\r\n",
        b"A1 LOGOUT\r\n",
    ]


@pytest.mark.parametrize("argument", ["query", "folder"])
@pytest.mark.parametrize("control", [chr(c) for c in range(32)] + ["\x7f"])
def test_runtime_rejects_controls_before_stripping_and_client_creation(
    argument, control
):
    calls: list[IMAPConfig] = []

    def forbidden_factory(config: IMAPConfig) -> Any:
        calls.append(config)
        raise AssertionError("Invalid arguments must not create an IMAP client")

    runtime = IMAPRuntime(
        accounts={"primary": IMAPConfig()},
        policies={
            "bot": IMAPAccessPolicyConfig(
                folder_access=IMAPFolderAccessConfig(
                    rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
                )
            )
        },
        imap_client_factory=forbidden_factory,
    )
    arguments = {"account": "primary", "folder": "INBOX", "query": "invoice"}
    arguments[argument] = control + arguments[argument] + control

    with pytest.raises(PluginValidationError):
        IMAPServicePlugin().invoke_operation(
            "search_messages",
            arguments,
            ServicePluginContext(runtimes=RuntimeRegistry({"imap": runtime})),
        )

    assert calls == []


@pytest.mark.parametrize(
    "operation,arguments",
    [
        (name, {**arguments, "uid": INJECTION})
        for name, arguments, _ in MAILBOX_OPERATIONS
        if "uid" in arguments
    ],
)
def test_uid_injection_never_reaches_imap_wire(monkeypatch, operation, arguments):
    client, server = wire_client(monkeypatch)
    with pytest.raises(PluginValidationError):
        getattr(client, operation)(**arguments)
    assert server.wire == []


@pytest.mark.parametrize("flag", [INJECTION, "tag) (\\Deleted", "flag name"])
@pytest.mark.parametrize("argument", ["add_flags", "remove_flags", "flags"])
def test_invalid_flags_are_rejected_before_any_flag_write(monkeypatch, flag, argument):
    client, server = wire_client(monkeypatch)
    with pytest.raises(PluginValidationError):
        if argument == "flags":
            client.append_message(folder="Drafts", message_bytes=b"body", flags=[flag])
        else:
            arguments = {"add_flags": ["\\Seen"], "remove_flags": []}
            arguments[argument] = [flag]
            client.update_message_flags(folder="INBOX", uid="42", **arguments)
    assert server.wire == []


def test_move_fallback_uses_the_same_safe_mailbox_encoding(monkeypatch):
    client, server = wire_client(monkeypatch)

    def complete(tag: bytes, *, expect_bye: bool = False) -> tuple[str, list[bytes]]:
        if server.wire[-1].startswith(tag + b" UID MOVE "):
            return "BAD", [b"MOVE unsupported"]
        return "OK", []

    monkeypatch.setattr(server, "_get_tagged_response", complete)
    client.move_message(
        source_folder="INBOX", uid="42", destination_folder='My "Archive"\\Folder'
    )

    mailbox = b'"My \\"Archive\\"\\\\Folder"'
    assert server.wire == [
        b'A0 SELECT "INBOX"\r\n',
        b"A1 UID MOVE 42 " + mailbox + b"\r\n",
        b"A2 UID COPY 42 " + mailbox + b"\r\n",
        b"A3 UID STORE 42 +FLAGS.SILENT (\\Deleted)\r\n",
        b"A4 UID EXPUNGE 42\r\n",
        b"A5 LOGOUT\r\n",
    ]
