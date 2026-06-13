from collections.abc import Sequence
from typing import cast

import pytest

from arbiter_server.artifacts import ArtifactDescriptor, PluginArtifactStore
from arbiter_server.app import SERVER_TOOL_NAMES, ArbiterApp
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    IMAPFolderKind,
    IMAPSystemFlagsPolicyConfig,
)
from arbiter_smtp.config import (
    SMTPConfig,
    SMTPLimitsConfig,
    SMTPRecipientPolicyConfig,
    SMTPSentCopyFailureMode,
    SMTPSentCopyPolicyConfig,
    SMTPServicePolicyConfig,
)
from arbiter_server.services import RuntimeRegistry, ServicePluginContext
from arbiter_imap import IMAPRuntime, IMAPServicePlugin
from arbiter_imap.client import (
    FetchedIMAPMessage,
    IMAPAttachment,
    IMAPAttachmentContent,
)
from arbiter_smtp import SMTPRuntime, SMTPServicePlugin, SentCopyDestination


class FakeSMTPClient:
    def __init__(self) -> None:
        self.message_bytes: bytes | None = None
        self.sender: str | None = None
        self.recipients: list[str] | None = None

    def send(
        self,
        message_bytes: bytes,
        sender: str,
        recipients: list[str],
    ) -> None:
        self.message_bytes = message_bytes
        self.sender = sender
        self.recipients = recipients

    def test_connection(self) -> None:
        return None


class RecordingSMTPClientFactory:
    def __init__(self) -> None:
        self.configs: list[SMTPConfig] = []
        self.clients: list[FakeSMTPClient] = []

    def __call__(self, config: SMTPConfig) -> FakeSMTPClient:
        self.configs.append(config)
        client = FakeSMTPClient()
        self.clients.append(client)
        return client


class RecordingSentMessageAppender:
    def __init__(
        self,
        destination: SentCopyDestination | None = None,
    ) -> None:
        self.destination = destination or SentCopyDestination(
            account="primary",
            folder="Sent",
        )
        self.checked: list[dict[str, object]] = []
        self.resolved: list[dict[str, object]] = []
        self.appended: list[dict[str, object]] = []

    def check_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination:
        self.checked.append({"account": account, "folder": folder})
        return self.destination

    def resolve_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination:
        self.resolved.append({"account": account, "folder": folder})
        return self.destination

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None:
        self.appended.append(
            {"account": account, "folder": folder, "message_bytes": message_bytes}
        )


class FakeIMAPClient:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.get_attachment_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.move_calls: list[dict[str, object]] = []
        self.mark_read_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.append_calls: list[dict[str, object]] = []
        self.messages = [
            FetchedIMAPMessage(
                uid="42",
                subject="Status update",
                from_addr="sender@example.com",
                to=["bot@example.com"],
                cc=[],
                date="Tue, 03 Mar 2026 12:00:00 +0000",
                flags=["\\Seen", "\\Deleted", "bot.followed_up", "internal_only"],
                rfc822_message_id="<message-42@example.com>",
                text_body="Plain text body",
                html_body=None,
                snippet="Plain text body",
                attachments=[
                    IMAPAttachment(
                        id="part-2",
                        filename="contract.pdf",
                        content_type="application/pdf",
                        size=1234,
                        disposition="attachment",
                        content_id=None,
                        inline=False,
                    )
                ],
            )
        ]

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
        self.list_calls.append({"folder": folder, "limit": limit})
        return self.messages[:limit]

    def test_connection(self, *, folders: Sequence[str]) -> None:
        return None

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
        self.get_calls.append({"folder": folder, "uid": uid})
        return self.messages[0]

    def get_attachment(
        self,
        *,
        folder: str,
        uid: str,
        attachment_id: str,
    ) -> IMAPAttachmentContent:
        self.get_attachment_calls.append(
            {
                "folder": folder,
                "uid": uid,
                "attachment_id": attachment_id,
            }
        )
        return IMAPAttachmentContent(
            attachment=self.messages[0].attachments[0],
            content=b"PDF",
        )

    def search_messages(
        self, *, folder: str, query: str, limit: int
    ) -> list[FetchedIMAPMessage]:
        self.search_calls.append({"folder": folder, "query": query, "limit": limit})
        return self.messages[:limit]

    def move_message(
        self, *, source_folder: str, uid: str, destination_folder: str
    ) -> None:
        self.move_calls.append(
            {
                "source_folder": source_folder,
                "uid": uid,
                "destination_folder": destination_folder,
            }
        )

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
        self.mark_read_calls.append({"folder": folder, "uid": uid, "read": read})

    def delete_message(self, *, folder: str, uid: str) -> None:
        self.delete_calls.append({"folder": folder, "uid": uid})

    def append_message(
        self,
        *,
        folder: str,
        message_bytes: bytes,
        flags: Sequence[str] = (r"\Seen",),
    ) -> None:
        self.append_calls.append(
            {"folder": folder, "message_bytes": message_bytes, "flags": tuple(flags)}
        )


class RecordingIMAPClientFactory:
    def __init__(self) -> None:
        self.configs: list[IMAPConfig] = []
        self.clients: list[FakeIMAPClient] = []

    def __call__(self, config: IMAPConfig) -> FakeIMAPClient:
        self.configs.append(config)
        client = FakeIMAPClient()
        self.clients.append(client)
        return client


class FakeArtifactStore:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    def create(
        self,
        *,
        content: bytes,
        filename: str | None,
        content_type: str,
        source: dict[str, object],
    ) -> ArtifactDescriptor:
        self.create_calls.append(
            {
                "content": content,
                "filename": filename,
                "content_type": content_type,
                "source": source,
            }
        )
        return ArtifactDescriptor(
            id="art-1",
            url="http://127.0.0.1:8000/_arbiter/artifacts/art-1?nonce=nonce-1",
            filename=filename,
            content_type=content_type,
            size=len(content),
            sha256="sha256",
            created_at="2026-06-09T00:00:00+00:00",
            expires_after_idle_seconds=600,
            one_time=True,
        )


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _smtp_runtime(
    *,
    accounts: dict[str, SMTPConfig] | None = None,
    policies: dict[str, SMTPServicePolicyConfig] | None = None,
    smtp_client_factory: RecordingSMTPClientFactory | None = None,
    time_provider: FakeClock | None = None,
) -> SMTPRuntime:
    if time_provider is None:
        return SMTPRuntime(
            accounts=accounts
            or {
                "primary": SMTPConfig(
                    description="Primary SMTP account",
                    policy="bot",
                )
            },
            policies=policies or {"bot": SMTPServicePolicyConfig()},
            smtp_client_factory=smtp_client_factory or RecordingSMTPClientFactory(),
        )
    return SMTPRuntime(
        accounts=accounts
        or {
            "primary": SMTPConfig(
                description="Primary SMTP account",
                policy="bot",
            )
        },
        policies=policies or {"bot": SMTPServicePolicyConfig()},
        smtp_client_factory=smtp_client_factory or RecordingSMTPClientFactory(),
        time_provider=time_provider,
    )


def _imap_config(policy: str = "personal") -> IMAPConfig:
    return IMAPConfig(
        description="Personal IMAP account",
        policy=policy,
        default_folder="INBOX",
        folders={
            "INBOX": IMAPFolderConfig(description="Inbox"),
            "Archive": IMAPFolderConfig(
                description="Archive",
                kind=IMAPFolderKind.archive,
            ),
            "Archive/2024": IMAPFolderConfig(
                description="Archived mail from 2024",
                kind=IMAPFolderKind.archive,
            ),
            "Sent": IMAPFolderConfig(
                description="Sent mail",
                kind=IMAPFolderKind.sent,
            ),
        },
    )


def _imap_runtime(
    *,
    accounts: dict[str, IMAPConfig] | None = None,
    policies: dict[str, IMAPAccessPolicyConfig] | None = None,
    imap_client_factory: RecordingIMAPClientFactory | None = None,
    artifact_store: FakeArtifactStore | None = None,
) -> IMAPRuntime:
    return IMAPRuntime(
        accounts=accounts or {"personal": _imap_config()},
        policies=policies
        or {
            "personal": IMAPAccessPolicyConfig(
                allow_move=False,
                allow_delete=False,
                user_flags={
                    "bot.followed_up": IMAPFlagMode.read_write,
                    "triaged": IMAPFlagMode.read_only,
                    "internal_only": IMAPFlagMode.hidden,
                },
            )
        },
        imap_client_factory=imap_client_factory or RecordingIMAPClientFactory(),
        artifact_store=cast(PluginArtifactStore | None, artifact_store),
    )


def _app(
    *,
    smtp_runtime: SMTPRuntime | None = None,
    imap_runtime: IMAPRuntime | None = None,
) -> ArbiterApp:
    runtimes: dict[str, object] = {}
    if smtp_runtime is not None:
        runtimes["smtp"] = smtp_runtime
    if imap_runtime is not None:
        runtimes["imap"] = imap_runtime
    return ArbiterApp(RuntimeRegistry(runtimes))


def test_tool_names_contains_server_discovery_tools() -> None:
    app = _app(smtp_runtime=_smtp_runtime(), imap_runtime=_imap_runtime())

    assert app.tool_names() == list(SERVER_TOOL_NAMES)


def test_list_accounts_returns_service_grouped_summaries() -> None:
    app = _app(smtp_runtime=_smtp_runtime(), imap_runtime=_imap_runtime())

    assert app.list_accounts() == {
        "imap": {
            "personal": {
                "description": "Personal IMAP account",
                "guidance": "",
                "policy": "personal",
                "enabled": True,
                "confirmation_required": [],
                "message": {
                    "read_allowed": True,
                    "move_allowed": False,
                    "delete_allowed": False,
                    "flags": {
                        "seen": "read_only",
                        "flagged": "read_only",
                        "answered": "read_only",
                        "deleted": "read_only",
                        "draft": "read_only",
                        "user": {
                            "bot.followed_up": "read_write",
                            "triaged": "read_only",
                        },
                    },
                },
            },
        },
        "smtp": {
            "primary": {
                "description": "Primary SMTP account",
                "guidance": "",
                "policy": "bot",
                "enabled": True,
                "send": "allowed",
                "require_confirmation": False,
            },
        },
    }


def test_list_accounts_accepts_entry_point_supplied_service_runtime() -> None:
    class FakeRuntime:
        def account_summaries(self) -> dict[str, object]:
            return {
                "primary": {
                    "description": "Primary external account",
                    "enabled": True,
                }
            }

    app = ArbiterApp(RuntimeRegistry({"external": FakeRuntime()}))

    assert app.tool_names() == list(SERVER_TOOL_NAMES)
    assert app.list_accounts() == {
        "external": {
            "primary": {
                "description": "Primary external account",
                "enabled": True,
            }
        }
    }


def test_send_email_uses_account_policy() -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _smtp_runtime(
        accounts={
            "personal": SMTPConfig(
                description="Personal SMTP account",
                policy="personal",
            )
        },
        policies={
            "personal": SMTPServicePolicyConfig(
                require_confirmation=True,
                recipient_policy=SMTPRecipientPolicyConfig(
                    allowed_domain_patterns=["example.com"],
                ),
            )
        },
        smtp_client_factory=factory,
    )

    result = runtime.send_email(
        account="personal",
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert result.tool == "send_email"
    assert result.recipient_count == 3
    assert len(factory.clients) == 1
    client = factory.clients[0]
    assert client.sender == "agent@example.com"
    assert client.recipients == [
        "to@example.com",
        "cc@example.com",
        "bcc@example.com",
    ]
    assert client.message_bytes is not None
    assert b"Subject: Hello" in client.message_bytes


def test_send_email_rejects_unconfigured_account() -> None:
    runtime = _smtp_runtime()

    with pytest.raises(ValueError, match="SMTP-enabled account: missing"):
        runtime.send_email(
            account="missing",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
        )


def test_smtp_runtime_rejects_unknown_policy_reference() -> None:
    with pytest.raises(
        ValueError,
        match="SMTP account references an unknown policy: primary -> missing",
    ):
        _smtp_runtime(accounts={"primary": SMTPConfig(policy="missing")}, policies={})


def test_send_email_enforces_recipient_policy() -> None:
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                recipient_policy=SMTPRecipientPolicyConfig(
                    blocked_domain_patterns=["blocked.example"],
                ),
            )
        }
    )

    with pytest.raises(ValueError, match="blocked by domain policy"):
        runtime.send_email(
            account="primary",
            to=["person@blocked.example"],
            subject="Hello",
            text_body="Plain body",
        )


def test_check_operation_reports_smtp_policy_denial() -> None:
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                recipient_policy=SMTPRecipientPolicyConfig(
                    blocked_domain_patterns=["blocked.example"],
                ),
            )
        }
    )
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))

    result = cast(
        dict[str, Any],
        plugin.check_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["person@blocked.example"],
                "subject": "Hello",
                "text_body": "Plain body",
            },
            context,
        ),
    )

    assert result["operation"] == "smtp:send_email"
    assert result["allowed"] is False
    assert result["failed_gate"] == "blocked_domain"
    assert result["why_not"] == (
        "send_email recipient is blocked by domain policy: person@blocked.example"
    )


def test_check_operation_allows_smtp_send_without_delivery() -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _smtp_runtime(smtp_client_factory=factory)
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))

    result = cast(
        dict[str, Any],
        plugin.check_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["to@example.com"],
                "subject": "Hello",
                "text_body": "Plain body",
            },
            context,
        ),
    )

    assert result["operation"] == "smtp:send_email"
    assert result["allowed"] is True
    assert result["evidence"]["recipient_count"] == 1
    assert factory.clients == []


def test_check_operation_reports_required_smtp_sent_copy_preflight_denial() -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                sent_copy=SMTPSentCopyPolicyConfig(
                    on_failure=SMTPSentCopyFailureMode.fail,
                ),
            )
        },
        smtp_client_factory=factory,
    )
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))

    result = cast(
        dict[str, Any],
        plugin.check_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["to@example.com"],
                "subject": "Hello",
                "text_body": "Plain body",
            },
            context,
        ),
    )

    assert result["operation"] == "smtp:send_email"
    assert result["allowed"] is False
    assert result["failed_gate"] == "sent_copy"
    assert result["why_not"] == (
        "send_email sent-copy preflight failed: "
        "IMAP sent-copy appender is not configured"
    )
    assert factory.clients == []


def test_check_operation_uses_smtp_sent_copy_check_without_resolving_or_appending() -> (
    None
):
    factory = RecordingSMTPClientFactory()
    appender = RecordingSentMessageAppender()
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                sent_copy=SMTPSentCopyPolicyConfig(
                    on_failure=SMTPSentCopyFailureMode.fail,
                ),
            )
        },
        smtp_client_factory=factory,
    )
    runtime.configure_sent_message_appender(appender)
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))

    result = cast(
        dict[str, Any],
        plugin.check_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["to@example.com"],
                "subject": "Hello",
                "text_body": "Plain body",
            },
            context,
        ),
    )

    assert result["operation"] == "smtp:send_email"
    assert result["allowed"] is True
    assert result["evidence"]["sent_copy"] == {
        "status": "resolved",
        "account": "primary",
        "folder": "Sent",
    }
    assert appender.checked == [{"account": "primary", "folder": None}]
    assert appender.resolved == []
    assert appender.appended == []
    assert factory.clients == []


def test_check_operation_reports_smtp_rate_limit_without_consuming_attempt() -> None:
    clock = FakeClock()
    factory = RecordingSMTPClientFactory()
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                limits=SMTPLimitsConfig(max_messages_per_minute=1)
            )
        },
        smtp_client_factory=factory,
        time_provider=clock,
    )
    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))

    result = cast(
        dict[str, Any],
        plugin.check_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["to@example.com"],
                "subject": "Hello again",
                "text_body": "Plain body",
            },
            context,
        ),
    )

    assert result["operation"] == "smtp:send_email"
    assert result["allowed"] is False
    assert result["failed_gate"] == "max_messages_per_minute"
    assert result["evidence"]["active_attempt_count"] == 1
    assert runtime._rate_limit_attempt_timestamps()["primary"] == [0.0]
    assert len(factory.clients) == 1


def test_send_email_enforces_rate_limit() -> None:
    clock = FakeClock()
    runtime = _smtp_runtime(
        policies={
            "bot": SMTPServicePolicyConfig(
                limits=SMTPLimitsConfig(max_messages_per_minute=1)
            )
        },
        time_provider=clock,
    )

    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    with pytest.raises(ValueError, match="max_messages_per_minute"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello again",
            text_body="Plain body",
        )

    clock.advance(61)
    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello later",
        text_body="Plain body",
    )


def test_list_messages_uses_account_policy_and_folder_config() -> None:
    factory = RecordingIMAPClientFactory()
    runtime = _imap_runtime(imap_client_factory=factory)

    result = runtime.list_messages(account="personal", limit=1)

    assert result["account"] == "personal"
    assert result["folder"] == "INBOX"
    assert result["messages"] == [
        {
            "id": "42",
            "uid": "42",
            "subject": "Status update",
            "from": "sender@example.com",
            "to": ["bot@example.com"],
            "cc": [],
            "date": "Tue, 03 Mar 2026 12:00:00 +0000",
            "flags": ["seen", "deleted", "bot.followed_up"],
            "rfc822_message_id": "<message-42@example.com>",
            "snippet": "Plain text body",
        }
    ]
    assert factory.clients[0].list_calls == [{"folder": "INBOX", "limit": 1}]


def test_list_folders_browses_configured_folders_without_imap_client() -> None:
    factory = RecordingIMAPClientFactory()
    runtime = _imap_runtime(imap_client_factory=factory)

    assert runtime.list_folders(account="personal", limit=2) == {
        "account": "personal",
        "root": None,
        "recursive": False,
        "limit": 2,
        "truncated": True,
        "folders": [
            {
                "name": "Archive",
                "description": "Archive",
                "kind": "archive",
                "default": False,
            },
            {
                "name": "INBOX",
                "description": "Inbox",
                "kind": None,
                "default": True,
            },
        ],
    }
    assert runtime.list_folders(account="personal", root="Archive") == {
        "account": "personal",
        "root": "Archive",
        "recursive": False,
        "limit": 50,
        "truncated": False,
        "folders": [
            {
                "name": "Archive/2024",
                "description": "Archived mail from 2024",
                "kind": "archive",
                "default": False,
            },
        ],
    }
    assert factory.clients == []


def test_search_folders_matches_name_description_and_kind() -> None:
    runtime = _imap_runtime()

    assert runtime.search_folders(account="personal", query="sent") == {
        "account": "personal",
        "query": "sent",
        "root": None,
        "recursive": True,
        "limit": 20,
        "truncated": False,
        "folders": [
            {
                "name": "Sent",
                "description": "Sent mail",
                "kind": "sent",
                "default": False,
            },
        ],
    }
    assert runtime.search_folders(
        account="personal",
        query="archive",
        root="Archive",
        recursive=True,
        limit=1,
    ) == {
        "account": "personal",
        "query": "archive",
        "root": "Archive",
        "recursive": True,
        "limit": 1,
        "truncated": False,
        "folders": [
            {
                "name": "Archive/2024",
                "description": "Archived mail from 2024",
                "kind": "archive",
                "default": False,
            },
        ],
    }


def test_search_folders_requires_non_empty_query() -> None:
    runtime = _imap_runtime()

    with pytest.raises(ValueError, match="search_folders requires a non-empty query"):
        runtime.search_folders(account="personal", query=" ")


def test_imap_runtime_rejects_unknown_policy_reference() -> None:
    with pytest.raises(
        ValueError,
        match="IMAP account references an unknown policy: personal -> missing",
    ):
        _imap_runtime(
            accounts={"personal": _imap_config(policy="missing")}, policies={}
        )


def test_get_message_includes_body() -> None:
    factory = RecordingIMAPClientFactory()
    runtime = _imap_runtime(imap_client_factory=factory)

    result = runtime.get_message(account="personal", message_id="42")

    message = result["message"]
    assert isinstance(message, dict)
    assert message["text_body"] == "Plain text body"
    assert message["html_body"] is None
    assert message["attachments"] == [
        {
            "id": "part-2",
            "filename": "contract.pdf",
            "content_type": "application/pdf",
            "size": 1234,
            "disposition": "attachment",
            "content_id": None,
            "inline": False,
        }
    ]
    assert factory.clients[0].get_calls == [{"folder": "INBOX", "uid": "42"}]


def test_get_attachment_returns_one_time_artifact_descriptor() -> None:
    factory = RecordingIMAPClientFactory()
    artifact_store = FakeArtifactStore()
    runtime = _imap_runtime(
        imap_client_factory=factory,
        artifact_store=artifact_store,
    )

    result = runtime.get_attachment(
        account="personal",
        message_id="42",
        attachment_id="part-2",
    )

    assert result["account"] == "personal"
    assert result["folder"] == "INBOX"
    assert result["message_id"] == "42"
    assert result["attachment"] == {
        "id": "part-2",
        "filename": "contract.pdf",
        "content_type": "application/pdf",
        "size": 1234,
        "disposition": "attachment",
        "content_id": None,
        "inline": False,
    }
    assert result["delivery"] == "arbiter_artifact"
    assert result["artifact"] == {
        "id": "art-1",
        "url": "http://127.0.0.1:8000/_arbiter/artifacts/art-1?nonce=nonce-1",
        "filename": "contract.pdf",
        "content_type": "application/pdf",
        "size": 3,
        "sha256": "sha256",
        "created_at": "2026-06-09T00:00:00+00:00",
        "expires_after_idle_seconds": 600,
        "one_time": True,
        "handling": {
            "prefer_inline": False,
            "execute_locally": True,
            "requires_explicit_user_request": True,
            "path_interface": "arbiter artifact with-temp <url> -- <argv...{}...>",
            "stdin_interface": "arbiter artifact with-stdin <url> -- <argv...>",
            "save_interface": "arbiter artifact save <url> <path>",
            "save_requires_explicit_user_request": True,
            "instructions": (
                "Use the one-time URL only through an explicit artifact reader "
                "such as `arbiter artifact get --stdout` for small textual "
                "attachments. For binary attachments, prefer "
                "`arbiter artifact with-temp <url> -- <argv...{}...>` for "
                "path-based tools or "
                "`arbiter artifact with-stdin <url> -- <argv...>` for "
                "stdin-based tools. If the user explicitly asks to save the "
                "attachment, use "
                "`arbiter artifact save <url> <path>`. Do not otherwise "
                "save, copy, or persist the file."
            ),
        },
    }
    assert factory.clients[0].get_attachment_calls == [
        {
            "folder": "INBOX",
            "uid": "42",
            "attachment_id": "part-2",
        }
    ]
    assert artifact_store.create_calls == [
        {
            "content": b"PDF",
            "filename": "contract.pdf",
            "content_type": "application/pdf",
            "source": {
                "account": "personal",
                "folder": "INBOX",
                "message_id": "42",
                "attachment_id": "part-2",
            },
        }
    ]


def test_imap_get_attachment_is_hidden_without_artifact_store() -> None:
    runtime = _imap_runtime()
    plugin = IMAPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"imap": runtime}))

    operation_names = {
        descriptor.name for descriptor in plugin.describe_operations(context)
    }

    assert "get_message" in operation_names
    assert "get_attachment" not in operation_names


def test_search_messages_requires_policy_permission() -> None:
    runtime = _imap_runtime(
        policies={"personal": IMAPAccessPolicyConfig(allow_search=False)}
    )

    with pytest.raises(ValueError, match="search_messages is not allowed"):
        runtime.search_messages(account="personal", query="invoice")


def test_move_message_requires_policy_permission() -> None:
    runtime = _imap_runtime()

    with pytest.raises(ValueError, match="move_message is not allowed"):
        runtime.move_message(
            account="personal",
            message_id="42",
            destination_folder="Archive",
        )


def test_mark_message_read_requires_read_write_seen_flag() -> None:
    runtime = _imap_runtime()

    with pytest.raises(ValueError, match="read_write access"):
        runtime.mark_message_read(account="personal", message_id="42")


def test_imap_mutations_use_configured_account_policy() -> None:
    factory = RecordingIMAPClientFactory()
    runtime = _imap_runtime(
        policies={
            "personal": IMAPAccessPolicyConfig(
                system_flags=IMAPSystemFlagsPolicyConfig(seen=IMAPFlagMode.read_write)
            )
        },
        imap_client_factory=factory,
    )

    assert runtime.move_message(
        account="personal",
        message_id="42",
        destination_folder="Archive",
    ) == {
        "ok": True,
        "account": "personal",
        "source_folder": "INBOX",
        "destination_folder": "Archive",
        "message_id": "42",
    }
    assert runtime.mark_message_read(
        account="personal",
        message_id="42",
        read=False,
    ) == {
        "ok": True,
        "account": "personal",
        "folder": "INBOX",
        "message_id": "42",
        "read": False,
    }
    assert runtime.delete_message(account="personal", message_id="42") == {
        "ok": True,
        "account": "personal",
        "folder": "INBOX",
        "message_id": "42",
    }
    assert factory.clients[0].move_calls == [
        {"source_folder": "INBOX", "uid": "42", "destination_folder": "Archive"}
    ]
    assert factory.clients[1].mark_read_calls == [
        {"folder": "INBOX", "uid": "42", "read": False}
    ]
    assert factory.clients[2].delete_calls == [{"folder": "INBOX", "uid": "42"}]


def test_append_sent_message_uses_configured_folder() -> None:
    factory = RecordingIMAPClientFactory()
    runtime = _imap_runtime(imap_client_factory=factory)

    runtime.append_sent_message(
        account="personal",
        folder="Sent",
        message_bytes=b"Subject: Hello\r\n\r\nBody",
    )

    assert factory.clients[0].append_calls == [
        {
            "folder": "Sent",
            "message_bytes": b"Subject: Hello\r\n\r\nBody",
            "flags": (r"\Seen",),
        }
    ]
