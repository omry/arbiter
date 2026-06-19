from __future__ import annotations

import ssl
import imaplib
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from arbiter_server.services import ConfigCheckError, operation_input_schema
from arbiter_imap import (
    IMAPClientProtocol,
    IMAP_OPERATION_DESCRIPTORS,
    IMAPRuntime,
    IMAPServicePlugin,
)
from arbiter_imap.client import (
    FetchedIMAPMessage,
    IMAPAttachmentContent,
    IMAPClient,
    IMAPOperationError,
)
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
    IMAPConfig,
    IMAPFolderConfig,
    IMAPFolderKind,
    IMAPFolderOperationPolicyConfig,
    IMAPFlagMode,
    IMAPOperationDecision,
    IMAPSystemFlagsPolicyConfig,
    MailTlsMode,
)


MESSAGE_BYTES = (
    b"From: Sender <sender@example.com>\r\n"
    b"To: Bot <bot@example.com>\r\n"
    b"Cc: Watcher <watcher@example.com>\r\n"
    b"Subject: Status update\r\n"
    b"Date: Tue, 03 Mar 2026 12:00:00 +0000\r\n"
    b"Message-ID: <message-42@example.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Plain text body\r\n"
)


def _allow_all_policy() -> IMAPAccessPolicyConfig:
    return IMAPAccessPolicyConfig(
        folder_access=IMAPFolderAccessConfig(
            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
        )
    )


def test_operation_schemas_describe_imap_inputs() -> None:
    schemas = {
        descriptor.name: operation_input_schema(descriptor.input_schema)
        for descriptor in IMAP_OPERATION_DESCRIPTORS
    }

    assert list(schemas) == [
        "list_folders",
        "list_messages",
        "get_message",
        "get_attachment",
        "search_messages",
        "move_message",
        "mark_message_read",
        "get_message_flags",
        "update_message_flags",
        "append_message",
        "save_draft",
        "search_folders",
        "delete_message",
    ]
    assert {name: schema["required"] for name, schema in schemas.items()} == {
        "list_folders": ["account"],
        "list_messages": ["account"],
        "get_message": ["account", "message_id"],
        "get_attachment": ["account", "message_id", "attachment_id"],
        "search_messages": ["account", "query"],
        "move_message": ["account", "message_id", "destination_folder"],
        "mark_message_read": ["account", "message_id"],
        "get_message_flags": ["account", "message_id"],
        "update_message_flags": ["account", "message_id"],
        "append_message": ["account"],
        "save_draft": ["account", "message"],
        "search_folders": ["account", "query"],
        "delete_message": ["account", "message_id"],
    }
    assert all(schema["additionalProperties"] is False for schema in schemas.values())

    def defaults(operation: str) -> dict[str, object]:
        properties = cast(
            dict[str, dict[str, object]], schemas[operation]["properties"]
        )
        return {
            name: schema["default"]
            for name, schema in properties.items()
            if "default" in schema
        }

    assert defaults("list_folders") == {"recursive": False, "limit": 50}
    assert defaults("list_messages") == {"limit": 20}
    assert defaults("search_messages") == {"limit": 20}
    assert defaults("mark_message_read") == {"read": True}
    assert defaults("update_message_flags") == {"add_flags": [], "remove_flags": []}
    assert defaults("append_message") == {"flags": ["SEEN"]}
    assert defaults("search_folders") == {"recursive": True, "limit": 20}
    assert defaults("delete_message") == {"permanent": False}


def test_plugin_config_check_warns_when_configured_folders_are_denied() -> None:
    warnings = IMAPServicePlugin().check_config(
        accounts={
            "primary": IMAPConfig(
                folders={"INBOX": IMAPFolderConfig(description="Inbox")}
            )
        },
        policies={
            "bot": IMAPAccessPolicyConfig(
                folder_access=IMAPFolderAccessConfig(
                    rules=[IMAPFolderAccessRuleConfig(deny_glob="*")]
                )
            )
        },
    )

    assert [
        (warning.account, warning.policy, warning.message) for warning in warnings
    ] == [("primary", "bot", "IMAP account has no accessible configured folders")]


def test_plugin_config_check_warns_when_drafts_is_not_marked_drafts() -> None:
    warnings = IMAPServicePlugin().check_config(
        accounts={
            "primary": IMAPConfig(
                folders={"Drafts": IMAPFolderConfig(description="Draft messages")}
            )
        },
        policies={"bot": _allow_all_policy()},
    )

    assert [
        (warning.account, warning.policy, warning.message) for warning in warnings
    ] == [
        (
            "primary",
            "bot",
            "save_draft will not select Drafts by default because the folder is "
            "not marked kind: DRAFTS",
        )
    ]


def test_plugin_config_check_warns_for_ambiguous_drafts_folders() -> None:
    policy = _allow_all_policy()
    policy.folders["Drafts"] = IMAPFolderOperationPolicyConfig(
        folder_append=IMAPOperationDecision.allow,
        system_flags=IMAPSystemFlagsPolicyConfig(
            SEEN=IMAPFlagMode.read_write,
            DRAFT=IMAPFlagMode.read_write,
        ),
    )
    warnings = IMAPServicePlugin().check_config(
        accounts={
            "primary": IMAPConfig(
                folders={
                    "Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS),
                    "Other Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS),
                }
            )
        },
        policies={"bot": policy},
    )

    assert [
        (warning.account, warning.policy, warning.message) for warning in warnings
    ] == [
        (
            "primary",
            "bot",
            "save_draft has multiple configured DRAFTS folders; using Drafts by "
            "default (configured: Drafts, Other Drafts)",
        )
    ]


def test_plugin_config_check_warns_for_save_draft_policy_gaps() -> None:
    warnings = IMAPServicePlugin().check_config(
        accounts={
            "primary": IMAPConfig(
                folders={"Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS)}
            )
        },
        policies={"bot": _allow_all_policy()},
    )

    assert [
        (warning.account, warning.policy, warning.message) for warning in warnings
    ] == [
        (
            "primary",
            "bot",
            "save_draft cannot append to Drafts because folder_append is deny",
        ),
        (
            "primary",
            "bot",
            "imap:save_draft cannot set required flags on Drafts folder; set "
            "DRAFT, SEEN to read_write to allow them to be modified",
        ),
    ]


def test_plugin_config_check_warns_for_inaccessible_save_draft_folder() -> None:
    policy = _allow_all_policy()
    policy.folder_access.rules.append(
        IMAPFolderAccessRuleConfig(deny_kind=IMAPFolderKind.DRAFTS)
    )
    policy.folders["Drafts"] = IMAPFolderOperationPolicyConfig(
        folder_append=IMAPOperationDecision.allow,
        system_flags=IMAPSystemFlagsPolicyConfig(
            SEEN=IMAPFlagMode.read_write,
            DRAFT=IMAPFlagMode.read_write,
        ),
    )

    warnings = IMAPServicePlugin().check_config(
        accounts={
            "primary": IMAPConfig(
                folders={"Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS)}
            )
        },
        policies={"bot": policy},
    )

    assert [
        (warning.account, warning.policy, warning.message) for warning in warnings
    ] == [
        ("primary", "bot", "IMAP account has no accessible configured folders"),
        (
            "primary",
            "bot",
            "save_draft cannot use Drafts because folder_access denies it",
        ),
    ]


def test_plugin_config_check_reports_invalid_drafts_metadata_as_issue() -> None:
    with pytest.raises(ConfigCheckError) as exc_info:
        IMAPServicePlugin().check_config(
            accounts={
                "primary": IMAPConfig(
                    folders={
                        "Drafts{bad": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS)
                    }
                )
            },
            policies={"bot": _allow_all_policy()},
        )

    assert [
        (issue.account, issue.policy, issue.message) for issue in exc_info.value.issues
    ] == [
        (
            "primary",
            "bot",
            "IMAP configured folder is invalid: Drafts{bad: "
            "unclosed capture block",
        )
    ]


def test_plugin_config_check_rejects_delete_without_accessible_trash() -> None:
    policy = _allow_all_policy()
    policy.operation_defaults.delete = IMAPOperationDecision.allow

    with pytest.raises(ConfigCheckError) as exc_info:
        IMAPServicePlugin().check_config(
            accounts={
                "primary": IMAPConfig(
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")}
                )
            },
            policies={"bot": policy},
        )

    assert [
        (issue.account, issue.policy, issue.message) for issue in exc_info.value.issues
    ] == [
        (
            "primary",
            "bot",
            "delete_message requires an accessible TRASH folder for account: primary",
        )
    ]


def test_plugin_config_check_rejects_default_delete_without_configured_trash() -> None:
    policy = _allow_all_policy()
    policy.operation_defaults.delete = IMAPOperationDecision.allow

    with pytest.raises(ConfigCheckError) as exc_info:
        IMAPServicePlugin().check_config(
            accounts={"primary": IMAPConfig()},
            policies={"bot": policy},
        )

    assert [
        (issue.account, issue.policy, issue.message) for issue in exc_info.value.issues
    ] == [
        (
            "primary",
            "bot",
            "delete_message requires an accessible TRASH folder for account: primary",
        )
    ]


def test_plugin_config_check_rejects_pattern_delete_without_configured_trash() -> None:
    policy = _allow_all_policy()
    policy.folders["Archives.{[0-9][0-9]*:year}"] = IMAPFolderOperationPolicyConfig(
        delete=IMAPOperationDecision.allow
    )

    with pytest.raises(ConfigCheckError) as exc_info:
        IMAPServicePlugin().check_config(
            accounts={
                "primary": IMAPConfig(
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")}
                )
            },
            policies={"bot": policy},
        )

    assert [
        (issue.account, issue.policy, issue.message) for issue in exc_info.value.issues
    ] == [
        (
            "primary",
            "bot",
            "delete_message requires an accessible TRASH folder for account: primary",
        )
    ]


def test_plugin_config_check_rejects_delete_with_denied_trash() -> None:
    policy = _allow_all_policy()
    policy.operation_defaults.delete = IMAPOperationDecision.allow
    policy.folder_access.rules.append(
        IMAPFolderAccessRuleConfig(deny_kind=IMAPFolderKind.TRASH)
    )

    with pytest.raises(ConfigCheckError) as exc_info:
        IMAPServicePlugin().check_config(
            accounts={
                "primary": IMAPConfig(
                    folders={
                        "INBOX": IMAPFolderConfig(description="Inbox"),
                        "Trash": IMAPFolderConfig(
                            description="Trash",
                            kind=IMAPFolderKind.TRASH,
                        ),
                    }
                )
            },
            policies={"bot": policy},
        )

    assert [
        (issue.account, issue.policy, issue.message) for issue in exc_info.value.issues
    ] == [
        (
            "primary",
            "bot",
            "delete_message requires an accessible TRASH folder for account: primary",
        )
    ]


class FakeIMAPServer:
    def __init__(self) -> None:
        self.selected: list[dict[str, object]] = []
        self.uid_calls: list[tuple[str, tuple[object, ...]]] = []
        self.login_args: tuple[str, str] | None = None
        self.starttls_context: ssl.SSLContext | None = None
        self.logged_out = False
        self.noop_called = False
        self.raise_on_move = False
        self.move_response: tuple[str, list[Any]] | None = None

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected.append({"mailbox": mailbox, "readonly": readonly})
        return "OK", [b"3"]

    def uid(self, command: str, *args: object) -> tuple[str, list[Any]]:
        self.uid_calls.append((command, args))
        if command == "MOVE" and self.raise_on_move:
            raise imaplib.IMAP4.error("MOVE unavailable")
        if command == "MOVE" and self.move_response is not None:
            return self.move_response
        if command == "SEARCH":
            return "OK", [b"40 41 42"]
        if command == "FETCH" and args[1] == "(FLAGS)":
            return "OK", [b"42 (UID 42 FLAGS (\\Seen bot.followed_up))"]
        if command == "FETCH" and args[1] == "(RFC822)":
            return "OK", [(b"42 (RFC822 {123}", MESSAGE_BYTES)]
        return "OK", [b"ok"]

    def starttls(self, *, ssl_context: ssl.SSLContext) -> None:
        self.starttls_context = ssl_context

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def noop(self) -> tuple[str, list[bytes]]:
        self.noop_called = True
        return "OK", [b"noop"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "OK", [b"logout"]


def test_list_messages_uses_ssl_login_and_parses_recent_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        assert host == "imap.example.com"
        assert port == 993
        assert timeout == 10.0
        assert ssl_context.check_hostname is False
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(
        IMAPConfig(
            host="imap.example.com",
            username="user",
            password="secret",
            verify_peer=False,
            timeout_seconds=10.0,
        )
    )

    messages = client.list_messages(folder="INBOX", limit=2)

    assert fake_server.login_args == ("user", "secret")
    assert fake_server.selected == [{"mailbox": '"INBOX"', "readonly": True}]
    assert [message.uid for message in messages] == ["42", "41"]
    assert messages[0].subject == "Status update"
    assert messages[0].from_addr == "Sender <sender@example.com>"
    assert messages[0].to == ["Bot <bot@example.com>"]
    assert messages[0].cc == ["Watcher <watcher@example.com>"]
    assert messages[0].flags == ["\\Seen", "bot.followed_up"]
    assert messages[0].text_body == "Plain text body\r\n"
    assert messages[0].html_body is None
    assert messages[0].snippet == "Plain text body"
    assert fake_server.logged_out is True


def test_starttls_uses_configured_context(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4(host: str, port: int, *, timeout: float) -> FakeIMAPServer:
        assert host == "imap.example.com"
        assert port == 143
        assert timeout == 30.0
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4", fake_imap4)

    client = IMAPClient(
        IMAPConfig(
            host="imap.example.com",
            port=143,
            tls=MailTlsMode.starttls,
            verify_peer=False,
        )
    )

    client.get_message(folder="INBOX", uid="42")

    assert fake_server.starttls_context is not None
    assert fake_server.starttls_context.check_hostname is False
    assert fake_server.starttls_context.verify_mode == ssl.CERT_NONE


def test_connection_probe_uses_noop_and_readonly_folder_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig(username="user", password="secret"))

    client.test_connection(folders=["Archive", "INBOX"])

    assert fake_server.login_args == ("user", "secret")
    assert fake_server.noop_called is True
    assert fake_server.selected == [
        {"mailbox": '"Archive"', "readonly": True},
        {"mailbox": '"INBOX"', "readonly": True},
    ]
    assert fake_server.logged_out is True


def test_connection_probe_quotes_mailbox_names_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig(username="user", password="secret"))

    client.test_connection(folders=["Archive.Various payments", 'Quotes "inside"'])

    assert fake_server.selected == [
        {"mailbox": '"Archive.Various payments"', "readonly": True},
        {"mailbox": '"Quotes \\"inside\\""', "readonly": True},
    ]


class LiveCheckRecordingIMAPClient:
    def __init__(self, folders: Sequence[str]) -> None:
        self._folders = list(folders)
        self.tested_folders: list[str] | None = None

    def test_connection(self, *, folders: Sequence[str]) -> None:
        self.tested_folders = list(folders)

    def list_folders(self) -> list[str]:
        return list(self._folders)

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
        raise AssertionError("list_messages should not be called")

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
        raise AssertionError("get_message should not be called")

    def get_attachment(
        self,
        *,
        folder: str,
        uid: str,
        attachment_id: str,
    ) -> IMAPAttachmentContent:
        raise AssertionError("get_attachment should not be called")

    def search_messages(
        self,
        *,
        folder: str,
        query: str,
        limit: int,
    ) -> list[FetchedIMAPMessage]:
        raise AssertionError("search_messages should not be called")

    def move_message(
        self,
        *,
        source_folder: str,
        uid: str,
        destination_folder: str,
    ) -> None:
        raise AssertionError("move_message should not be called")

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
        raise AssertionError("mark_message_read should not be called")

    def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
        raise AssertionError("get_message_flags should not be called")

    def update_message_flags(
        self,
        *,
        folder: str,
        uid: str,
        add_flags: Sequence[str],
        remove_flags: Sequence[str],
    ) -> None:
        raise AssertionError("update_message_flags should not be called")

    def delete_message(self, *, folder: str, uid: str) -> None:
        raise AssertionError("delete_message should not be called")

    def append_message(
        self,
        *,
        folder: str,
        message_bytes: bytes,
        flags: Sequence[str] = (r"\Seen",),
    ) -> None:
        raise AssertionError("append_message should not be called")


def test_runtime_tests_configured_folders_read_only() -> None:
    class RecordingIMAPClient:
        def __init__(self) -> None:
            self.tested_folders: list[str] | None = None

        def test_connection(self, *, folders: Sequence[str]) -> None:
            self.tested_folders = list(folders)

        def list_folders(self) -> list[str]:
            return ["Archive", "INBOX"]

        def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
            raise AssertionError("list_messages should not be called")

        def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
            raise AssertionError("get_message should not be called")

        def get_attachment(
            self,
            *,
            folder: str,
            uid: str,
            attachment_id: str,
        ) -> IMAPAttachmentContent:
            raise AssertionError("get_attachment should not be called")

        def search_messages(
            self,
            *,
            folder: str,
            query: str,
            limit: int,
        ) -> list[FetchedIMAPMessage]:
            raise AssertionError("search_messages should not be called")

        def move_message(
            self,
            *,
            source_folder: str,
            uid: str,
            destination_folder: str,
        ) -> None:
            raise AssertionError("move_message should not be called")

        def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
            raise AssertionError("mark_message_read should not be called")

        def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
            raise AssertionError("get_message_flags should not be called")

        def update_message_flags(
            self,
            *,
            folder: str,
            uid: str,
            add_flags: Sequence[str],
            remove_flags: Sequence[str],
        ) -> None:
            raise AssertionError("update_message_flags should not be called")

        def delete_message(self, *, folder: str, uid: str) -> None:
            raise AssertionError("delete_message should not be called")

        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (r"\Seen",),
        ) -> None:
            raise AssertionError("append_message should not be called")

    clients: list[RecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> IMAPClientProtocol:
        client = RecordingIMAPClient()
        clients.append(client)
        return cast(IMAPClientProtocol, client)

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "Archive": IMAPFolderConfig(),
                    "INBOX": IMAPFolderConfig(),
                },
            )
        },
        policies={"bot": _allow_all_policy()},
        imap_client_factory=client_factory,
    )

    progress_calls: list[str] = []

    assert runtime.test_accounts(progress=progress_calls.append) == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_examine",
            "checks": ["connect", "noop", "examine"],
            "folders": ["Archive", "INBOX"],
        }
    }
    assert clients[0].tested_folders == ["Archive", "INBOX"]
    assert progress_calls == ["primary"]


def test_runtime_live_check_requires_server_trash_when_delete_allowed() -> None:
    policy = _allow_all_policy()
    policy.operation_defaults.delete = IMAPOperationDecision.allow
    clients: list[LiveCheckRecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> IMAPClientProtocol:
        client = LiveCheckRecordingIMAPClient(["INBOX"])
        clients.append(client)
        return cast(IMAPClientProtocol, client)

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "INBOX": IMAPFolderConfig(),
                    "Trash": IMAPFolderConfig(kind=IMAPFolderKind.TRASH),
                },
            )
        },
        policies={"bot": policy},
        imap_client_factory=client_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_examine",
            "error_type": "ValueError",
            "message": (
                "delete_message requires an accessible TRASH folder for account: "
                "primary"
            ),
        }
    }
    assert clients[0].tested_folders == ["INBOX"]


def test_runtime_live_check_accepts_server_trash_when_delete_allowed() -> None:
    policy = _allow_all_policy()
    policy.operation_defaults.delete = IMAPOperationDecision.allow
    clients: list[LiveCheckRecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> IMAPClientProtocol:
        client = LiveCheckRecordingIMAPClient(["INBOX", "Trash"])
        clients.append(client)
        return cast(IMAPClientProtocol, client)

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "INBOX": IMAPFolderConfig(),
                    "Trash": IMAPFolderConfig(kind=IMAPFolderKind.TRASH),
                },
            )
        },
        policies={"bot": policy},
        imap_client_factory=client_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_examine",
            "checks": ["connect", "noop", "examine", "trash_destination"],
            "folders": ["INBOX", "Trash"],
        }
    }
    assert clients[0].tested_folders == ["INBOX", "Trash"]


def test_runtime_live_check_requires_configured_drafts_folder() -> None:
    clients: list[LiveCheckRecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> IMAPClientProtocol:
        client = LiveCheckRecordingIMAPClient(["INBOX"])
        clients.append(client)
        return cast(IMAPClientProtocol, client)

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "INBOX": IMAPFolderConfig(),
                    "Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS),
                },
            )
        },
        policies={"bot": _allow_all_policy()},
        imap_client_factory=client_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_examine",
            "error_type": "ValueError",
            "message": (
                "save_draft requires configured DRAFTS folder to exist for IMAP "
                "account: primary: Drafts"
            ),
        }
    }
    assert clients[0].tested_folders == ["INBOX"]


def test_runtime_live_check_accepts_configured_drafts_folder() -> None:
    clients: list[LiveCheckRecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> IMAPClientProtocol:
        client = LiveCheckRecordingIMAPClient(["Drafts", "INBOX"])
        clients.append(client)
        return cast(IMAPClientProtocol, client)

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "INBOX": IMAPFolderConfig(),
                    "Drafts": IMAPFolderConfig(kind=IMAPFolderKind.DRAFTS),
                },
            )
        },
        policies={"bot": _allow_all_policy()},
        imap_client_factory=client_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_examine",
            "checks": ["connect", "noop", "examine", "save_draft_destination"],
            "folders": ["Drafts", "INBOX"],
        }
    }
    assert clients[0].tested_folders == ["Drafts", "INBOX"]


def test_runtime_live_check_scopes_probe_to_metadata_patterns() -> None:
    class RecordingIMAPClient:
        def __init__(self) -> None:
            self.tested_folders: list[str] | None = None

        def test_connection(self, *, folders: Sequence[str]) -> None:
            self.tested_folders = list(folders)

        def list_folders(self) -> list[str]:
            return [
                "Archives.2026",
                "Archives._Misc.firestats.Various payments",
                "INBOX",
            ]

        def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
            raise AssertionError("list_messages should not be called")

        def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
            raise AssertionError("get_message should not be called")

        def get_attachment(
            self,
            *,
            folder: str,
            uid: str,
            attachment_id: str,
        ) -> IMAPAttachmentContent:
            raise AssertionError("get_attachment should not be called")

        def search_messages(
            self,
            *,
            folder: str,
            query: str,
            limit: int,
        ) -> list[FetchedIMAPMessage]:
            raise AssertionError("search_messages should not be called")

        def move_message(
            self,
            *,
            source_folder: str,
            uid: str,
            destination_folder: str,
        ) -> None:
            raise AssertionError("move_message should not be called")

        def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
            raise AssertionError("mark_message_read should not be called")

        def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
            raise AssertionError("get_message_flags should not be called")

        def update_message_flags(
            self,
            *,
            folder: str,
            uid: str,
            add_flags: Sequence[str],
            remove_flags: Sequence[str],
        ) -> None:
            raise AssertionError("update_message_flags should not be called")

        def delete_message(self, *, folder: str, uid: str) -> None:
            raise AssertionError("delete_message should not be called")

        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (),
        ) -> None:
            raise AssertionError("append_message should not be called")

    clients: list[RecordingIMAPClient] = []

    def client_factory(config: IMAPConfig) -> RecordingIMAPClient:
        client = RecordingIMAPClient()
        clients.append(client)
        return client

    runtime = IMAPRuntime(
        accounts={
            "primary": IMAPConfig(
                policy="bot",
                default_folder="INBOX",
                folders={
                    "Archives.{year}": IMAPFolderConfig(
                        description="Archive for {year}"
                    ),
                },
            )
        },
        policies={"bot": _allow_all_policy()},
        imap_client_factory=client_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_examine",
            "checks": ["connect", "noop", "examine"],
            "folders": ["Archives.2026", "INBOX"],
        }
    }
    assert clients[0].tested_folders == ["Archives.2026", "INBOX"]


def test_runtime_skips_folder_probe_when_no_folders_are_configured() -> None:
    class RecordingIMAPClient:
        def test_connection(self, *, folders: Sequence[str]) -> None:
            assert folders == []

        def list_folders(self) -> list[str]:
            return []

        def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
            raise AssertionError("list_messages should not be called")

        def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
            raise AssertionError("get_message should not be called")

        def get_attachment(
            self,
            *,
            folder: str,
            uid: str,
            attachment_id: str,
        ) -> IMAPAttachmentContent:
            raise AssertionError("get_attachment should not be called")

        def search_messages(
            self,
            *,
            folder: str,
            query: str,
            limit: int,
        ) -> list[FetchedIMAPMessage]:
            raise AssertionError("search_messages should not be called")

        def move_message(
            self,
            *,
            source_folder: str,
            uid: str,
            destination_folder: str,
        ) -> None:
            raise AssertionError("move_message should not be called")

        def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
            raise AssertionError("mark_message_read should not be called")

        def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
            raise AssertionError("get_message_flags should not be called")

        def update_message_flags(
            self,
            *,
            folder: str,
            uid: str,
            add_flags: Sequence[str],
            remove_flags: Sequence[str],
        ) -> None:
            raise AssertionError("update_message_flags should not be called")

        def delete_message(self, *, folder: str, uid: str) -> None:
            raise AssertionError("delete_message should not be called")

        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (r"\Seen",),
        ) -> None:
            raise AssertionError("append_message should not be called")

    runtime = IMAPRuntime(
        accounts={"primary": IMAPConfig(policy="bot")},
        policies={"bot": _allow_all_policy()},
        imap_client_factory=lambda config: RecordingIMAPClient(),
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "skipped",
            "stage": "connect_auth_noop",
            "checks": ["connect", "noop"],
            "reason": "no accessible IMAP folders to examine read-only",
        }
    }


def test_runtime_decodes_byte_authentication_failures() -> None:
    class FailingIMAPClient:
        def test_connection(self, *, folders: Sequence[str]) -> None:
            raise AssertionError("test_connection should not be called")

        def list_folders(self) -> list[str]:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed.")

        def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
            raise AssertionError("list_messages should not be called")

        def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
            raise AssertionError("get_message should not be called")

        def get_attachment(
            self,
            *,
            folder: str,
            uid: str,
            attachment_id: str,
        ) -> IMAPAttachmentContent:
            raise AssertionError("get_attachment should not be called")

        def search_messages(
            self,
            *,
            folder: str,
            query: str,
            limit: int,
        ) -> list[FetchedIMAPMessage]:
            raise AssertionError("search_messages should not be called")

        def move_message(
            self,
            *,
            source_folder: str,
            uid: str,
            destination_folder: str,
        ) -> None:
            raise AssertionError("move_message should not be called")

        def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
            raise AssertionError("mark_message_read should not be called")

        def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
            raise AssertionError("get_message_flags should not be called")

        def update_message_flags(
            self,
            *,
            folder: str,
            uid: str,
            add_flags: Sequence[str],
            remove_flags: Sequence[str],
        ) -> None:
            raise AssertionError("update_message_flags should not be called")

        def delete_message(self, *, folder: str, uid: str) -> None:
            raise AssertionError("delete_message should not be called")

        def append_message(
            self,
            *,
            folder: str,
            message_bytes: bytes,
            flags: Sequence[str] = (r"\Seen",),
        ) -> None:
            raise AssertionError("append_message should not be called")

    runtime = IMAPRuntime(
        accounts={"primary": IMAPConfig(policy="bot")},
        policies={"bot": _allow_all_policy()},
        imap_client_factory=lambda config: FailingIMAPClient(),
    )

    result = cast(Mapping[str, object], runtime.test_accounts()["primary"])

    assert result["message"] == "[AUTHENTICATIONFAILED] Authentication failed."


def test_move_falls_back_to_copy_delete_and_uid_expunge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()
    fake_server.raise_on_move = True

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    client.move_message(
        source_folder="INBOX",
        uid="42",
        destination_folder="Archive",
    )

    assert fake_server.selected == [{"mailbox": '"INBOX"', "readonly": False}]
    assert ("COPY", ("42", '"Archive"')) in fake_server.uid_calls
    assert ("STORE", ("42", "+FLAGS.SILENT", r"(\Deleted)")) in fake_server.uid_calls
    assert ("EXPUNGE", ("42",)) in fake_server.uid_calls


def test_move_falls_back_when_server_returns_unsupported_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()
    fake_server.move_response = ("NO", [b"MOVE unsupported"])

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    client.move_message(
        source_folder="INBOX",
        uid="42",
        destination_folder="Archive",
    )

    assert fake_server.uid_calls == [
        ("MOVE", ("42", '"Archive"')),
        ("COPY", ("42", '"Archive"')),
        ("STORE", ("42", "+FLAGS.SILENT", r"(\Deleted)")),
        ("EXPUNGE", ("42",)),
    ]


def test_move_non_fallback_status_raises_without_copying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()
    fake_server.move_response = ("NO", [b"destination mailbox does not exist"])

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    with pytest.raises(
        IMAPOperationError, match="destination mailbox does not exist"
    ) as exc_info:
        client.move_message(
            source_folder="INBOX",
            uid="42",
            destination_folder="Archive",
        )

    assert "b'" not in str(exc_info.value)
    assert fake_server.uid_calls == [("MOVE", ("42", '"Archive"'))]


def test_move_bad_status_without_unsupported_marker_raises_without_copying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()
    fake_server.move_response = ("BAD", [b"invalid destination mailbox"])

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    with pytest.raises(IMAPOperationError, match="move message"):
        client.move_message(
            source_folder="INBOX",
            uid="42",
            destination_folder="Archive",
        )

    assert fake_server.uid_calls == [("MOVE", ("42", '"Archive"'))]


def test_delete_message_uses_uid_expunge(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    client.delete_message(folder="INBOX", uid="42")

    assert ("STORE", ("42", "+FLAGS.SILENT", r"(\Deleted)")) in fake_server.uid_calls
    assert ("EXPUNGE", ("42",)) in fake_server.uid_calls


def test_fetch_without_rfc822_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeIMAPServer()

    def fake_uid(command: str, *args: object) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return "OK", [b"42"]
        if command == "FETCH" and args[1] == "(FLAGS)":
            return "OK", [b"42 (UID 42 FLAGS ())"]
        return "OK", [b"missing body"]

    fake_server.uid = fake_uid  # type: ignore[method-assign]

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("arbiter_imap.client.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    with pytest.raises(IMAPOperationError, match="did not return RFC822"):
        client.list_messages(folder="INBOX", limit=1)
