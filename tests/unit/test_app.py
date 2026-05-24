import pytest
from email.message import EmailMessage

from mail_sentry.app import MailSentryApp
from mail_sentry.config import (
    AccountAccessProfileConfig,
    AccountConfig,
    AccountSensitivityTier,
    ImapAccessPolicyConfig,
    ImapConfigLike,
    ImapConfig,
    ImapFlagMode,
    ImapFolderConfig,
    ImapSystemFlagsPolicyConfig,
    MailConfig,
    SmtpConfigLike,
    SmtpConfig,
)
from mail_sentry.imap import FetchedImapMessage


class FakeSmtpClient:
    def __init__(self) -> None:
        self.message: EmailMessage | None = None
        self.sender: str | None = None
        self.recipients: list[str] | None = None

    def send(
        self,
        message: EmailMessage,
        sender: str,
        recipients: list[str],
    ) -> None:
        self.message = message
        self.sender = sender
        self.recipients = recipients


class RecordingSmtpClientFactory:
    def __init__(self) -> None:
        self.configs: list[SmtpConfigLike] = []
        self.clients: list[FakeSmtpClient] = []

    def __call__(self, config: SmtpConfigLike) -> FakeSmtpClient:
        self.configs.append(config)
        client = FakeSmtpClient()
        self.clients.append(client)
        return client


class FakeImapClient:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.move_calls: list[dict[str, object]] = []
        self.mark_read_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.messages = [
            FetchedImapMessage(
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
            )
        ]

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedImapMessage]:
        self.list_calls.append({"folder": folder, "limit": limit})
        return self.messages[:limit]

    def get_message(self, *, folder: str, uid: str) -> FetchedImapMessage:
        self.get_calls.append({"folder": folder, "uid": uid})
        return self.messages[0]

    def search_messages(
        self, *, folder: str, query: str, limit: int
    ) -> list[FetchedImapMessage]:
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


class RecordingImapClientFactory:
    def __init__(self) -> None:
        self.configs: list[ImapConfigLike] = []
        self.clients: list[FakeImapClient] = []

    def __call__(self, config: ImapConfigLike) -> FakeImapClient:
        self.configs.append(config)
        client = FakeImapClient()
        self.clients.append(client)
        return client


def _mail_config() -> MailConfig:
    return MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
                smtp=SmtpConfig(),
            ),
            "personal": AccountConfig(
                description="Personal IMAP account",
                account_access_profile="personal",
                sensitivity_tier=AccountSensitivityTier.sensitive,
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
            "personal": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    allow_move=False,
                    allow_delete=False,
                )
            ),
        },
    )


def test_tool_names_contains_list_accounts_and_send_email() -> None:
    app = MailSentryApp(
        _mail_config(), smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.tool_names() == [
        "list_accounts",
        "send_email",
        "list_messages",
        "get_message",
        "search_messages",
        "move_message",
        "mark_message_read",
        "delete_message",
    ]


def test_list_accounts_returns_normalized_account_summaries() -> None:
    app = MailSentryApp(
        _mail_config(), smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal IMAP account",
            "account_access_profile": "personal",
            "sensitivity_tier": "sensitive",
            "smtp": {
                "send": "unavailable",
            },
            "imap": {
                "enabled": True,
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
                    },
                },
            },
        },
        {
            "name": "primary",
            "description": "Primary SMTP account",
            "account_access_profile": "bot",
            "sensitivity_tier": "standard",
            "smtp": {
                "send": "allowed",
            },
            "imap": {
                "enabled": False,
            },
        },
    ]


def test_list_accounts_reports_writable_imap_account() -> None:
    mail_config = MailConfig(
        accounts={
            "alerts": AccountConfig(
                description="Alerts account",
                account_access_profile="bot",
                sensitivity_tier=AccountSensitivityTier.standard,
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "alerts",
            "description": "Alerts account",
            "account_access_profile": "bot",
            "sensitivity_tier": "standard",
            "smtp": {
                "send": "unavailable",
            },
            "imap": {
                "enabled": True,
                "message": {
                    "read_allowed": True,
                    "move_allowed": True,
                    "delete_allowed": True,
                    "flags": {
                        "seen": "read_only",
                        "flagged": "read_only",
                        "answered": "read_only",
                        "deleted": "read_only",
                        "draft": "read_only",
                    },
                },
            },
        }
    ]


def test_list_accounts_reports_account_with_both_protocols() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary full account",
                account_access_profile="bot",
                sensitivity_tier=AccountSensitivityTier.standard,
                smtp=SmtpConfig(),
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "primary",
            "description": "Primary full account",
            "account_access_profile": "bot",
            "sensitivity_tier": "standard",
            "smtp": {
                "send": "allowed",
            },
            "imap": {
                "enabled": True,
                "message": {
                    "read_allowed": True,
                    "move_allowed": True,
                    "delete_allowed": True,
                    "flags": {
                        "seen": "read_only",
                        "flagged": "read_only",
                        "answered": "read_only",
                        "deleted": "read_only",
                        "draft": "read_only",
                    },
                },
            },
        }
    ]


def test_list_accounts_reports_configured_user_flag_access() -> None:
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal account",
                account_access_profile="personal",
                sensitivity_tier=AccountSensitivityTier.sensitive,
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    allow_move=False,
                    allow_delete=False,
                    user_flags={
                        "bot.followed_up": ImapFlagMode.read_write,
                        "triaged": ImapFlagMode.read_only,
                        "internal_only": ImapFlagMode.hidden,
                    },
                )
            ),
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal account",
            "account_access_profile": "personal",
            "sensitivity_tier": "sensitive",
            "smtp": {
                "send": "unavailable",
            },
            "imap": {
                "enabled": True,
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
        }
    ]


def test_list_accounts_reports_all_system_flags() -> None:
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal account",
                account_access_profile="personal",
                sensitivity_tier=AccountSensitivityTier.sensitive,
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    allow_move=False,
                    allow_delete=False,
                    system_flags=ImapSystemFlagsPolicyConfig(
                        seen=ImapFlagMode.read_write,
                        flagged=ImapFlagMode.read_write,
                        deleted=ImapFlagMode.hidden,
                    ),
                )
            ),
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal account",
            "account_access_profile": "personal",
            "sensitivity_tier": "sensitive",
            "smtp": {
                "send": "unavailable",
            },
            "imap": {
                "enabled": True,
                "message": {
                    "read_allowed": True,
                    "move_allowed": False,
                    "delete_allowed": False,
                    "flags": {
                        "seen": "read_write",
                        "flagged": "read_write",
                        "answered": "read_only",
                        "deleted": "hidden",
                        "draft": "read_only",
                    },
                },
            },
        }
    ]


def test_list_accounts_reports_disabled_smtp_account() -> None:
    mail_config = MailConfig(
        accounts={
            "secondary": AccountConfig(
                description="Secondary SMTP account",
                account_access_profile="personal",
                smtp=SmtpConfig(),
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(allow_smtp_send=False),
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    assert app.list_accounts() == [
        {
            "name": "secondary",
            "description": "Secondary SMTP account",
            "account_access_profile": "personal",
            "sensitivity_tier": "standard",
            "smtp": {
                "send": "disabled",
            },
            "imap": {
                "enabled": False,
            },
        }
    ]


def test_send_email_submits_message_and_excludes_bcc_header() -> None:
    smtp_factory = RecordingSmtpClientFactory()
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=smtp_factory,
    )

    result = app.send_email(
        account="primary",
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        subject="Hello",
        text_body="Plain text body",
        html_body="<p>HTML body</p>",
    )

    assert result.tool == "send_email"
    assert result.recipient_count == 3
    smtp_client = smtp_factory.clients[-1]
    assert smtp_client.message is not None
    assert smtp_client.sender is not None
    assert smtp_client.recipients is not None
    assert smtp_client.sender == "agent@example.com"
    assert smtp_client.recipients == [
        "to@example.com",
        "cc@example.com",
        "bcc@example.com",
    ]
    assert smtp_client.message["From"] == "Mail Sentry <agent@example.com>"
    assert smtp_client.message["To"] == "to@example.com"
    assert smtp_client.message["Cc"] == "cc@example.com"
    assert smtp_client.message["Subject"] == "Hello"
    assert smtp_client.message["Bcc"] is None


def test_send_email_requires_body_content() -> None:
    app = MailSentryApp(
        _mail_config(), smtp_client_factory=lambda config: FakeSmtpClient()
    )

    with pytest.raises(ValueError, match="text_body or html_body"):
        app.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Missing body",
        )


def test_send_email_supports_html_only_body() -> None:
    smtp_factory = RecordingSmtpClientFactory()
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=smtp_factory,
    )

    app.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        html_body="<p>HTML only</p>",
    )

    smtp_client = smtp_factory.clients[-1]
    assert smtp_client.message is not None
    assert smtp_client.message.get_content_type() == "text/html"
    assert smtp_client.message.is_multipart() is False
    assert "<p>HTML only</p>" in smtp_client.message.get_content()


def test_send_email_preserves_non_ascii_subject_and_display_name() -> None:
    smtp_factory = RecordingSmtpClientFactory()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
                sensitivity_tier=AccountSensitivityTier.standard,
                smtp=SmtpConfig(
                    from_email="agent@example.com",
                    from_name="Jöhn Döe",
                ),
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=smtp_factory,
    )

    app.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Héllo ✓",
        text_body="Plain text body",
    )

    smtp_client = smtp_factory.clients[-1]
    assert smtp_client.message is not None
    assert smtp_client.message["From"] == "Jöhn Döe <agent@example.com>"
    assert smtp_client.message["Subject"] == "Héllo ✓"


def test_send_email_rejects_unknown_account() -> None:
    app = MailSentryApp(
        _mail_config(), smtp_client_factory=lambda config: FakeSmtpClient()
    )

    with pytest.raises(ValueError, match="unknown account: missing"):
        app.send_email(
            account="missing",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_imap_only_account() -> None:
    app = MailSentryApp(
        _mail_config(), smtp_client_factory=lambda config: FakeSmtpClient()
    )

    with pytest.raises(ValueError, match="SMTP-enabled account: personal"):
        app.send_email(
            account="personal",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_account_with_disabled_send_policy() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
                smtp=SmtpConfig(),
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(allow_smtp_send=False)
        },
    )
    app = MailSentryApp(
        mail_config, smtp_client_factory=lambda config: FakeSmtpClient()
    )

    with pytest.raises(ValueError, match="not allowed for account: primary"):
        app.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_uses_selected_account_smtp_config() -> None:
    smtp_factory = RecordingSmtpClientFactory()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
                sensitivity_tier=AccountSensitivityTier.standard,
                smtp=SmtpConfig(
                    from_email="agent@example.com",
                    from_name="Primary Sender",
                ),
            ),
            "alerts": AccountConfig(
                description="Alerts SMTP account",
                account_access_profile="bot",
                sensitivity_tier=AccountSensitivityTier.sensitive,
                smtp=SmtpConfig(
                    from_email="alerts@example.com",
                    from_name="Alerts Sender",
                ),
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = MailSentryApp(mail_config, smtp_client_factory=smtp_factory)

    app.send_email(
        account="alerts",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain text body",
    )

    smtp_client = smtp_factory.clients[-1]
    assert smtp_client.message is not None
    assert smtp_factory.configs[-1].from_email == "alerts@example.com"
    assert smtp_client.sender == "alerts@example.com"
    assert smtp_client.message["From"] == "Alerts Sender <alerts@example.com>"


def test_list_messages_uses_default_folder_and_filters_hidden_flags() -> None:
    imap_factory = RecordingImapClientFactory()
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal IMAP account",
                account_access_profile="personal",
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    system_flags=ImapSystemFlagsPolicyConfig(
                        deleted=ImapFlagMode.hidden,
                    ),
                    user_flags={"bot.followed_up": ImapFlagMode.read_write},
                )
            )
        },
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=imap_factory,
    )

    result = app.list_messages(account="personal")

    assert imap_factory.clients[-1].list_calls == [{"folder": "INBOX", "limit": 20}]
    assert result == {
        "account": "personal",
        "folder": "INBOX",
        "messages": [
            {
                "id": "42",
                "uid": "42",
                "subject": "Status update",
                "from": "sender@example.com",
                "to": ["bot@example.com"],
                "cc": [],
                "date": "Tue, 03 Mar 2026 12:00:00 +0000",
                "flags": ["seen", "bot.followed_up"],
                "rfc822_message_id": "<message-42@example.com>",
                "snippet": "Plain text body",
            }
        ],
    }


def test_get_message_includes_bodies() -> None:
    imap_factory = RecordingImapClientFactory()
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=imap_factory,
    )

    result = app.get_message(account="personal", folder="INBOX", message_id="42")

    assert imap_factory.clients[-1].get_calls == [{"folder": "INBOX", "uid": "42"}]
    message = result["message"]
    assert isinstance(message, dict)
    assert message["text_body"] == "Plain text body"
    assert message["html_body"] is None


def test_search_messages_requires_search_policy() -> None:
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal IMAP account",
                account_access_profile="personal",
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    allow_read=True,
                    allow_search=False,
                    allow_move=False,
                    allow_delete=False,
                )
            )
        },
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=RecordingImapClientFactory(),
    )

    with pytest.raises(ValueError, match="search_messages is not allowed"):
        app.search_messages(account="personal", query="invoice")


def test_move_message_requires_configured_destination_folder() -> None:
    imap_factory = RecordingImapClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={"bot": AccountAccessProfileConfig()},
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=imap_factory,
    )

    with pytest.raises(ValueError, match="unconfigured folder"):
        app.move_message(
            account="bot",
            folder="INBOX",
            message_id="42",
            destination_folder="Archive",
        )


def test_move_message_calls_imap_client_when_policy_allows() -> None:
    imap_factory = RecordingImapClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={
                        "INBOX": ImapFolderConfig(description="Inbox"),
                        "Archive": ImapFolderConfig(description="Archive"),
                    },
                ),
            )
        },
        account_access_profiles={"bot": AccountAccessProfileConfig()},
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=imap_factory,
    )

    result = app.move_message(
        account="bot",
        message_id="42",
        destination_folder="Archive",
    )

    assert imap_factory.clients[-1].move_calls == [
        {
            "source_folder": "INBOX",
            "uid": "42",
            "destination_folder": "Archive",
        }
    ]
    assert result == {
        "ok": True,
        "account": "bot",
        "source_folder": "INBOX",
        "destination_folder": "Archive",
        "message_id": "42",
    }


def test_mark_message_read_requires_seen_read_write_policy() -> None:
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=RecordingImapClientFactory(),
    )

    with pytest.raises(ValueError, match="read_write access to the seen flag"):
        app.mark_message_read(account="personal", folder="INBOX", message_id="42")


def test_mark_message_read_calls_imap_client_when_seen_flag_is_writable() -> None:
    imap_factory = RecordingImapClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
                imap=ImapConfig(
                    default_folder="INBOX",
                    folders={"INBOX": ImapFolderConfig(description="Inbox")},
                ),
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                imap=ImapAccessPolicyConfig(
                    system_flags=ImapSystemFlagsPolicyConfig(
                        seen=ImapFlagMode.read_write,
                    )
                )
            )
        },
    )
    app = MailSentryApp(
        mail_config,
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=imap_factory,
    )

    result = app.mark_message_read(
        account="bot", folder="INBOX", message_id="42", read=False
    )

    assert imap_factory.clients[-1].mark_read_calls == [
        {"folder": "INBOX", "uid": "42", "read": False}
    ]
    assert result == {
        "ok": True,
        "account": "bot",
        "folder": "INBOX",
        "message_id": "42",
        "read": False,
    }


def test_delete_message_requires_delete_policy() -> None:
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=RecordingImapClientFactory(),
    )

    with pytest.raises(ValueError, match="delete_message is not allowed"):
        app.delete_message(account="personal", folder="INBOX", message_id="42")


def test_list_messages_rejects_unknown_imap_folder() -> None:
    app = MailSentryApp(
        _mail_config(),
        smtp_client_factory=lambda config: FakeSmtpClient(),
        imap_client_factory=RecordingImapClientFactory(),
    )

    with pytest.raises(ValueError, match="unconfigured folder"):
        app.list_messages(account="personal", folder="Archive")
