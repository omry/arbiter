from __future__ import annotations

import pytest

from arbiter_imap.client import IMAPClient
from arbiter_imap.config import IMAPConfig
from arbiter_server.errors import OperationError


class SizeAwareServer:
    def __init__(self, size: int, content: bytes = b"Subject: small\r\n\r\nbody"):
        self.size = size
        self.content = content
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.selected: list[bool] = []

    def select(self, mailbox, readonly=False):
        self.selected.append(readonly)
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b"42"]
        if args[1] == "(FLAGS)":
            return "OK", [b"1 (UID 42 FLAGS ())"]
        if args[1] == "(RFC822.SIZE)":
            return "OK", [f"1 (UID 42 RFC822.SIZE {self.size})".encode()]
        return "OK", [(b"1 (UID 42 BODY[] {20}", self.content)]

    def logout(self):
        return "BYE", [b"done"]


@pytest.mark.parametrize(
    "operation", ["get_message", "get_attachment", "list_messages", "search_messages"]
)
def test_default_message_limit_rejects_before_full_fetch(monkeypatch, operation):
    server = SizeAwareServer(50 * 1024 * 1024 + 1)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    arguments: dict[str, object] = {"folder": "INBOX"}
    if operation in {"get_message", "get_attachment"}:
        arguments["uid"] = "42"
    if operation == "get_attachment":
        arguments["attachment_id"] = "part-1"
    if operation in {"list_messages", "search_messages"}:
        arguments["limit"] = 1
    if operation == "search_messages":
        arguments["query"] = "message"

    with pytest.raises(OperationError) as caught:
        getattr(client, operation)(**arguments)

    assert caught.value.code == "policy_denied"
    assert caught.value.plugin == "imap"
    assert caught.value.domain_code == "message_too_large"
    assert caught.value.transient == "no"
    assert caught.value.replay == "safe"
    assert server.selected == [True]
    assert all(command in {"SEARCH", "FETCH"} for command, _ in server.calls)
    assert all(
        args[1] in {"(FLAGS)", "(RFC822.SIZE)"}
        for command, args in server.calls
        if command == "FETCH"
    )


def test_message_at_limit_is_fetched_with_peek(monkeypatch):
    content = b"Subject: bounded\r\n\r\nbody"
    server = SizeAwareServer(len(content), content)
    config = IMAPConfig()
    config.limits.max_message_bytes = len(content)
    client = IMAPClient(config)
    monkeypatch.setattr(client, "_connect", lambda: server)

    message = client.get_message(folder="INBOX", uid="42")

    assert message.subject == "bounded"
    assert server.selected == [True]
    assert server.calls == [
        ("FETCH", ("42", "(FLAGS)")),
        ("FETCH", ("42", "(RFC822.SIZE)")),
        ("FETCH", ("42", f"(BODY.PEEK[]<0.{len(content) + 1}>)")),
    ]


def test_body_uid_can_follow_the_literal(monkeypatch):
    content = b"Subject: valid\r\n\r\nbody"
    server = SizeAwareServer(len(content), content)
    original = server.uid

    def uid(command, *args):
        if command == "FETCH" and str(args[1]).startswith("(BODY.PEEK"):
            return "OK", [
                (f"1 (BODY[]<0> {{{len(content)}}}".encode(), content),
                b" UID 42)",
            ]
        return original(command, *args)

    monkeypatch.setattr(server, "uid", uid)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    message = client.get_message(folder="INBOX", uid="42")
    assert message.subject == "valid"
    assert message.text_body == "body"


@pytest.mark.parametrize(
    "size_uid,size_name,body_uid,flags",
    [
        ("uid", "RFC822.SIZE", "UID", ""),
        ("UID", "rfc822.size", "UID", ""),
        ("UID", "RFC822.SIZE", "uId", ""),
        ("UID", "RFC822.SIZE", "UID", "UID 42"),
        ("UID", "RFC822.SIZE", "UID", "RFC822.SIZE 9"),
    ],
)
def test_numeric_fetch_attributes_respect_case_and_flag_nesting(
    monkeypatch, size_uid, size_name, body_uid, flags
):
    content = b"Subject: valid\r\n\r\nbody"
    server = SizeAwareServer(len(content), content)
    original = server.uid
    flag_list = f" FLAGS ({flags})" if flags else ""

    def uid(command, *args):
        if command == "FETCH" and args[1] == "(RFC822.SIZE)":
            return "OK", [
                f"1 ({size_uid} 42 {size_name} {len(content)}{flag_list})".encode()
            ]
        if command == "FETCH" and str(args[1]).startswith("(BODY.PEEK"):
            return "OK", [
                (
                    f"1 ({body_uid} 42{flag_list} BODY[]<0> {{{len(content)}}}".encode(),
                    content,
                ),
                b")",
            ]
        return original(command, *args)

    monkeypatch.setattr(server, "uid", uid)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    assert client.get_message(folder="INBOX", uid="42").subject == "valid"


@pytest.mark.parametrize(
    "trailer", [b")", b" UID 43)", b"2 (UID 42 FLAGS ())", b" UID 42 UID 43)"]
)
def test_literal_cannot_borrow_a_missing_or_unrelated_uid(monkeypatch, trailer):
    from arbiter_imap.client import IMAPOperationError

    content = b"Subject: unrelated\r\n\r\nUID 42 in body"
    server = SizeAwareServer(len(content), content)
    original = server.uid

    def uid(command, *args):
        if command == "FETCH" and str(args[1]).startswith("(BODY.PEEK"):
            return "OK", [
                (f"1 (BODY[]<0> {{{len(content)}}}".encode(), content),
                trailer,
            ]
        return original(command, *args)

    monkeypatch.setattr(server, "uid", uid)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    with pytest.raises(IMAPOperationError, match="did not return RFC822"):
        client.get_message(folder="INBOX", uid="42")


def test_response_larger_than_reported_size_is_not_parsed(monkeypatch):
    config = IMAPConfig()
    config.limits.max_message_bytes = 10
    server = SizeAwareServer(10, b"x" * 11)
    client = IMAPClient(config)
    monkeypatch.setattr(client, "_connect", lambda: server)
    monkeypatch.setattr(
        "arbiter_imap.client.BytesParser",
        lambda **kwargs: pytest.fail("oversized body must not be parsed"),
    )

    with pytest.raises(OperationError) as caught:
        client.get_message(folder="INBOX", uid="42")

    assert caught.value.domain_code == "message_too_large"


@pytest.mark.parametrize(
    "size_response",
    [
        b"1 (UID 42)",
        b"1 (UID 42 RFC822.SIZE -1)",
        b"1 (UID 41 RFC822.SIZE 10)",
        b"1 (UID 42 RFC822.SIZE 10 RFC822.SIZE 10)",
        b"1 (UID 42 RFC822.SIZE 10junk)",
        b"1 (UID 42 RFC822.SIZE " + b"9" * 5500 + b")",
    ],
)
def test_missing_or_invalid_size_never_requests_content(monkeypatch, size_response):
    from arbiter_imap.client import IMAPOperationError

    server = SizeAwareServer(10)
    original = server.uid

    def uid(command, *args):
        if command == "FETCH" and args[1] == "(RFC822.SIZE)":
            server.calls.append((command, args))
            return "OK", [size_response]
        return original(command, *args)

    monkeypatch.setattr(server, "uid", uid)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)

    with pytest.raises(IMAPOperationError) as caught:
        client.get_message(folder="INBOX", uid="42")

    assert caught.value.code == "upstream_protocol_error"
    assert not any("BODY" in str(args) for _, args in server.calls)


def test_incomplete_body_is_not_returned_as_a_complete_message(monkeypatch):
    from arbiter_imap.client import IMAPOperationError

    server = SizeAwareServer(100, b"short")
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)

    with pytest.raises(IMAPOperationError, match="does not match RFC822.SIZE"):
        client.get_message(folder="INBOX", uid="42")


@pytest.mark.parametrize("maximum", [0, -1, True, None])
def test_invalid_limits_fail_before_client_or_runtime_connects(maximum):
    from arbiter_imap import IMAPRuntime, IMAPServicePlugin

    config = IMAPConfig()
    config.limits.max_message_bytes = maximum

    with pytest.raises(ValueError, match="positive integer"):
        IMAPClient(config)
    with pytest.raises(ValueError, match="positive integer"):
        IMAPRuntime(accounts={"bot": config}, policies={})
    from arbiter_server.services import ConfigCheckError

    with pytest.raises(ConfigCheckError, match="positive integer"):
        IMAPServicePlugin().check_config(accounts={"bot": config}, policies={})


@pytest.mark.parametrize("transport_name", ["BoundedIMAP4", "BoundedIMAP4SSL"])
def test_oversized_literal_closes_before_any_payload_read(monkeypatch, transport_name):
    from arbiter_imap import transport

    cls = getattr(transport, transport_name)
    connection = cls.__new__(cls)
    connection._max_literal_bytes = 10
    connection._message_literal_guard_active = False
    closed = []
    monkeypatch.setattr(connection, "shutdown", lambda: closed.append(True))
    monkeypatch.setattr(
        "imaplib.IMAP4.read",
        lambda *_args: pytest.fail(
            "literal must be rejected before the standard library reads it"
        ),
    )

    with connection.message_literal_guard():
        with pytest.raises(OperationError) as caught:
            connection.read(11)

    assert closed == [True]
    assert caught.value.domain_code == "message_too_large"
    assert caught.value.replay == "unknown"
    assert connection._message_literal_guard_active is False


@pytest.mark.parametrize("transport_name", ["BoundedIMAP4", "BoundedIMAP4SSL"])
def test_non_message_literals_are_unbounded_outside_guard(monkeypatch, transport_name):
    from arbiter_imap import transport

    cls = getattr(transport, transport_name)
    connection = cls.__new__(cls)
    connection._max_literal_bytes = 10
    connection._message_literal_guard_active = False
    payload = b"x" * 11
    monkeypatch.setattr("imaplib.IMAP4.read", lambda *_args: payload)

    assert connection.read(len(payload)) == payload
    assert connection._message_literal_guard_active is False


@pytest.mark.parametrize("transport_name", ["BoundedIMAP4", "BoundedIMAP4SSL"])
def test_message_literal_guard_restores_after_exception(transport_name):
    from arbiter_imap import transport

    cls = getattr(transport, transport_name)
    connection = cls.__new__(cls)
    connection._max_literal_bytes = 10
    connection._message_literal_guard_active = False

    with pytest.raises(RuntimeError, match="body fetch failed"):
        with connection.message_literal_guard():
            raise RuntimeError("body fetch failed")

    assert connection._message_literal_guard_active is False


def test_imap_limits_use_a_named_plugin_owned_schema():
    from omegaconf import OmegaConf

    config = OmegaConf.structured(IMAPConfig())
    assert config.limits.max_message_bytes == 50 * 1024 * 1024
    overridden = OmegaConf.merge(config, {"limits": {"max_message_bytes": 1000000}})
    assert overridden.limits.max_message_bytes == 1000000


@pytest.mark.parametrize("tls", ["none", "starttls", "implicit"])
def test_configured_limit_reaches_the_real_transport_factory(monkeypatch, tls):
    from arbiter_imap.config import MailTlsMode

    config = IMAPConfig(tls=MailTlsMode(tls))
    config.limits.max_message_bytes = 1234
    seen = []
    server = SizeAwareServer(10)
    monkeypatch.setattr(server, "starttls", lambda **kwargs: None, raising=False)

    def factory(*args, **kwargs):
        seen.append(kwargs["max_literal_bytes"])
        return server

    monkeypatch.setattr("arbiter_imap.client.BoundedIMAP4", factory)
    monkeypatch.setattr("arbiter_imap.client.BoundedIMAP4SSL", factory)

    assert IMAPClient(config)._connect() is server
    assert seen == [1234]


def test_limit_error_survives_plugin_dispatch(monkeypatch):
    from arbiter_imap import IMAPRuntime, IMAPServicePlugin
    from arbiter_imap.config import (
        IMAPAccessPolicyConfig,
        IMAPFolderAccessConfig,
        IMAPFolderAccessRuleConfig,
    )
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext

    server = SizeAwareServer(50 * 1024 * 1024 + 1)
    client = IMAPClient(IMAPConfig())
    monkeypatch.setattr(client, "_connect", lambda: server)
    policy = IMAPAccessPolicyConfig(
        folder_access=IMAPFolderAccessConfig(
            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
        )
    )
    runtime = IMAPRuntime(
        accounts={"bot": IMAPConfig()},
        policies={"bot": policy},
        imap_client_factory=lambda config: client,
    )

    with pytest.raises(OperationError) as caught:
        IMAPServicePlugin().invoke_operation(
            "get_message",
            {"account": "bot", "folder": "INBOX", "message_id": "42"},
            ServicePluginContext(runtimes=RuntimeRegistry({"imap": runtime})),
        )

    assert caught.value.to_dict() == {
        "error": {
            "code": "policy_denied",
            "message": "IMAP message exceeds the configured size limit.",
            "plugin": "imap",
            "domain_code": "message_too_large",
            "details": {"max_message_bytes": 50 * 1024 * 1024},
            "retry": {"transient": "no", "replay": "safe"},
        }
    }
