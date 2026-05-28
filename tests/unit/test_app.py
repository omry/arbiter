import pytest
from email.message import EmailMessage

from mail_sentry.app import MailSentryApp
from mail_sentry.config import (
    AccountAccessProfileConfig,
    AccountServicesConfig,
    AccountConfig,
    IMAPAccessPolicyConfig,
    IMAPConfigLike,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    IMAPServiceConfig,
    IMAPSystemFlagsPolicyConfig,
    MailConfig,
    ServicesConfig,
    SMTPConfigLike,
    SMTPConfig,
    SMTPServiceConfig,
    SMTPLimitsConfig,
    SMTPRecipientPolicyConfig,
    SMTPServicePolicyConfig,
)
from mail_sentry.imap import FetchedIMAPMessage
from mail_sentry.plugins.imap import IMAPClientFactory, IMAPRuntime
from mail_sentry.plugins.smtp import SMTPClientFactory, SMTPRuntime, TimeProvider
from mail_sentry.services import RuntimeRegistry


class FakeSMTPClient:
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


class RecordingSMTPClientFactory:
    def __init__(self) -> None:
        self.configs: list[SMTPConfigLike] = []
        self.clients: list[FakeSMTPClient] = []

    def __call__(self, config: SMTPConfigLike) -> FakeSMTPClient:
        self.configs.append(config)
        client = FakeSMTPClient()
        self.clients.append(client)
        return client


class FakeIMAPClient:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.move_calls: list[dict[str, object]] = []
        self.mark_read_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
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
            )
        ]

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
        self.list_calls.append({"folder": folder, "limit": limit})
        return self.messages[:limit]

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
        self.get_calls.append({"folder": folder, "uid": uid})
        return self.messages[0]

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


class RecordingIMAPClientFactory:
    def __init__(self) -> None:
        self.configs: list[IMAPConfigLike] = []
        self.clients: list[FakeIMAPClient] = []

    def __call__(self, config: IMAPConfigLike) -> FakeIMAPClient:
        self.configs.append(config)
        client = FakeIMAPClient()
        self.clients.append(client)
        return client


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _services_config(
    *,
    smtp_accounts: dict[str, SMTPConfig] | None = None,
    imap_accounts: dict[str, IMAPConfig] | None = None,
) -> ServicesConfig:
    return ServicesConfig(
        smtp=(
            SMTPServiceConfig(accounts=smtp_accounts)
            if smtp_accounts is not None
            else None
        ),
        imap=(
            IMAPServiceConfig(accounts=imap_accounts)
            if imap_accounts is not None
            else None
        ),
    )


def _app(
    mail_config: MailConfig,
    services_config: ServicesConfig,
    *,
    smtp_client_factory: SMTPClientFactory | None = None,
    imap_client_factory: IMAPClientFactory | None = None,
    time_provider: TimeProvider | None = None,
) -> MailSentryApp:
    runtimes: dict[str, object] = {}
    if services_config.smtp is not None:
        assert smtp_client_factory is not None
        runtimes["smtp"] = SMTPRuntime(
            mail_config,
            services_config.smtp,
            smtp_client_factory=smtp_client_factory,
            **({"time_provider": time_provider} if time_provider is not None else {}),
        )
    if services_config.imap is not None:
        runtimes["imap"] = IMAPRuntime(
            mail_config,
            services_config.imap,
            imap_client_factory=imap_client_factory,
        )
    return MailSentryApp(mail_config, RuntimeRegistry(runtimes))


def _mail_config() -> MailConfig:
    return MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            ),
            "personal": AccountConfig(
                description="Personal IMAP account",
                account_access_profile="personal",
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(require_confirmation=True),
                    imap=IMAPAccessPolicyConfig(
                        allow_move=False,
                        allow_delete=False,
                    ),
                )
            ),
        },
    )


def _default_services_config() -> ServicesConfig:
    return _services_config(
        smtp_accounts={"primary": SMTPConfig()},
        imap_accounts={
            "personal": IMAPConfig(
                default_folder="INBOX",
                folders={"INBOX": IMAPFolderConfig(description="Inbox")},
            )
        },
    )


def test_tool_names_contains_list_accounts_and_send_email() -> None:
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
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
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal IMAP account",
            "account_access_profile": "personal",
            "services": {
                "imap": {
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
                        },
                    },
                },
                "smtp": {
                    "enabled": False,
                    "send": "unavailable",
                    "require_confirmation": False,
                },
            },
        },
        {
            "name": "primary",
            "description": "Primary SMTP account",
            "account_access_profile": "bot",
            "services": {
                "imap": {
                    "enabled": False,
                },
                "smtp": {
                    "enabled": True,
                    "send": "allowed",
                    "require_confirmation": False,
                },
            },
        },
    ]


def test_list_accounts_reports_smtp_confirmation_requirement() -> None:
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal SMTP account",
                account_access_profile="personal",
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(require_confirmation=True),
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"personal": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal SMTP account",
            "account_access_profile": "personal",
            "services": {
                "smtp": {
                    "enabled": True,
                    "send": "allowed",
                    "require_confirmation": True,
                },
            },
        }
    ]


def test_list_accounts_reports_writable_imap_account() -> None:
    mail_config = MailConfig(
        accounts={
            "alerts": AccountConfig(
                description="Alerts account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "alerts": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    assert app.list_accounts() == [
        {
            "name": "alerts",
            "description": "Alerts account",
            "account_access_profile": "bot",
            "services": {
                "imap": {
                    "enabled": True,
                    "confirmation_required": [],
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
            },
        }
    ]


def test_list_accounts_reports_account_with_both_protocols() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary full account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            smtp_accounts={"primary": SMTPConfig()},
            imap_accounts={
                "primary": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            },
        ),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    assert app.list_accounts() == [
        {
            "name": "primary",
            "description": "Primary full account",
            "account_access_profile": "bot",
            "services": {
                "imap": {
                    "enabled": True,
                    "confirmation_required": [],
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
                "smtp": {
                    "enabled": True,
                    "send": "allowed",
                    "require_confirmation": False,
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
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    imap=IMAPAccessPolicyConfig(
                        allow_move=False,
                        allow_delete=False,
                        user_flags={
                            "bot.followed_up": IMAPFlagMode.read_write,
                            "triaged": IMAPFlagMode.read_only,
                            "internal_only": IMAPFlagMode.hidden,
                        },
                    )
                )
            ),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "personal": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal account",
            "account_access_profile": "personal",
            "services": {
                "imap": {
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
        }
    ]


def test_list_accounts_reports_all_system_flags() -> None:
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal account",
                account_access_profile="personal",
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    imap=IMAPAccessPolicyConfig(
                        allow_move=False,
                        allow_delete=False,
                        system_flags=IMAPSystemFlagsPolicyConfig(
                            seen=IMAPFlagMode.read_write,
                            flagged=IMAPFlagMode.read_write,
                            deleted=IMAPFlagMode.hidden,
                        ),
                    )
                )
            ),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "personal": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    assert app.list_accounts() == [
        {
            "name": "personal",
            "description": "Personal account",
            "account_access_profile": "personal",
            "services": {
                "imap": {
                    "enabled": True,
                    "confirmation_required": [],
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
            },
        }
    ]


def test_list_accounts_reports_smtp_account_without_confirmation() -> None:
    mail_config = MailConfig(
        accounts={
            "secondary": AccountConfig(
                description="Secondary SMTP account",
                account_access_profile="personal",
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(require_confirmation=False)
                )
            ),
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"secondary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    assert app.list_accounts() == [
        {
            "name": "secondary",
            "description": "Secondary SMTP account",
            "account_access_profile": "personal",
            "services": {
                "smtp": {
                    "enabled": True,
                    "send": "allowed",
                    "require_confirmation": False,
                },
            },
        }
    ]


def test_send_email_submits_message_and_excludes_bcc_header() -> None:
    smtp_factory = RecordingSMTPClientFactory()
    app = _app(
        _mail_config(),
        _default_services_config(),
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
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="text_body or html_body"):
        app.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Missing body",
        )


def test_send_email_supports_html_only_body() -> None:
    smtp_factory = RecordingSMTPClientFactory()
    app = _app(
        _mail_config(),
        _default_services_config(),
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
    smtp_factory = RecordingSMTPClientFactory()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            smtp_accounts={
                "primary": SMTPConfig(
                    from_email="agent@example.com",
                    from_name="Jöhn Döe",
                )
            }
        ),
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
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="unknown account: missing"):
        app.send_email(
            account="missing",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_imap_only_account() -> None:
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="SMTP-enabled account: personal"):
        app.send_email(
            account="personal",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_recipient_blocked_by_exact_address_policy() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(
                        recipient_policy=SMTPRecipientPolicyConfig(
                            blocked_recipients=["to@example.com"]
                        )
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"primary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="blocked by exact address policy"):
        app.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_recipient_outside_allowlist() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(
                        recipient_policy=SMTPRecipientPolicyConfig(
                            allowed_domain_patterns=["example.com"]
                        )
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"primary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="recipient is not allowed by policy"):
        app.send_email(
            account="primary",
            to=["to@other.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_recipient_count_over_policy_limit() -> None:
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(
                        limits=SMTPLimitsConfig(max_recipients_per_message=1)
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"primary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
    )

    with pytest.raises(ValueError, match="max_recipients_per_message"):
        app.send_email(
            account="primary",
            to=["one@example.com", "two@example.com"],
            subject="Hello",
            text_body="Plain text body",
        )


def test_send_email_rejects_rate_limit_exceeded() -> None:
    clock = FakeClock()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(
                        limits=SMTPLimitsConfig(max_messages_per_minute=1)
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"primary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
        time_provider=clock,
    )

    app.send_email(
        account="primary",
        to=["one@example.com"],
        subject="Hello",
        text_body="Plain text body",
    )

    with pytest.raises(ValueError, match="max_messages_per_minute"):
        app.send_email(
            account="primary",
            to=["two@example.com"],
            subject="Hello again",
            text_body="Plain text body",
        )


def test_send_email_allows_rate_limited_account_after_window_expires() -> None:
    clock = FakeClock()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    smtp=SMTPServicePolicyConfig(
                        limits=SMTPLimitsConfig(max_messages_per_minute=1)
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(smtp_accounts={"primary": SMTPConfig()}),
        smtp_client_factory=lambda config: FakeSMTPClient(),
        time_provider=clock,
    )

    app.send_email(
        account="primary",
        to=["one@example.com"],
        subject="Hello",
        text_body="Plain text body",
    )

    clock.advance(60.0)

    app.send_email(
        account="primary",
        to=["two@example.com"],
        subject="Hello again",
        text_body="Plain text body",
    )


def test_send_email_uses_selected_account_smtp_config() -> None:
    smtp_factory = RecordingSMTPClientFactory()
    mail_config = MailConfig(
        accounts={
            "primary": AccountConfig(
                description="Primary SMTP account",
                account_access_profile="bot",
            ),
            "alerts": AccountConfig(
                description="Alerts SMTP account",
                account_access_profile="bot",
            ),
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(),
        },
    )
    app = _app(
        mail_config,
        _services_config(
            smtp_accounts={
                "primary": SMTPConfig(
                    from_email="agent@example.com",
                    from_name="Primary Sender",
                ),
                "alerts": SMTPConfig(
                    from_email="alerts@example.com",
                    from_name="Alerts Sender",
                ),
            }
        ),
        smtp_client_factory=smtp_factory,
    )

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
    imap_factory = RecordingIMAPClientFactory()
    mail_config = MailConfig(
        accounts={
            "personal": AccountConfig(
                description="Personal IMAP account",
                account_access_profile="personal",
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    imap=IMAPAccessPolicyConfig(
                        system_flags=IMAPSystemFlagsPolicyConfig(
                            deleted=IMAPFlagMode.hidden,
                        ),
                        user_flags={"bot.followed_up": IMAPFlagMode.read_write},
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "personal": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
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
    imap_factory = RecordingIMAPClientFactory()
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
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
            )
        },
        account_access_profiles={
            "personal": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    imap=IMAPAccessPolicyConfig(
                        allow_read=True,
                        allow_search=False,
                        allow_move=False,
                        allow_delete=False,
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "personal": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    with pytest.raises(ValueError, match="search_messages is not allowed"):
        app.search_messages(account="personal", query="invoice")


def test_move_message_requires_configured_destination_folder() -> None:
    imap_factory = RecordingIMAPClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={"bot": AccountAccessProfileConfig()},
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "bot": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
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
    imap_factory = RecordingIMAPClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={"bot": AccountAccessProfileConfig()},
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "bot": IMAPConfig(
                    default_folder="INBOX",
                    folders={
                        "INBOX": IMAPFolderConfig(description="Inbox"),
                        "Archive": IMAPFolderConfig(description="Archive"),
                    },
                )
            }
        ),
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
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    with pytest.raises(ValueError, match="read_write access to the seen flag"):
        app.mark_message_read(account="personal", folder="INBOX", message_id="42")


def test_mark_message_read_calls_imap_client_when_seen_flag_is_writable() -> None:
    imap_factory = RecordingIMAPClientFactory()
    mail_config = MailConfig(
        accounts={
            "bot": AccountConfig(
                description="Bot IMAP account",
                account_access_profile="bot",
            )
        },
        account_access_profiles={
            "bot": AccountAccessProfileConfig(
                services=AccountServicesConfig(
                    imap=IMAPAccessPolicyConfig(
                        system_flags=IMAPSystemFlagsPolicyConfig(
                            seen=IMAPFlagMode.read_write,
                        )
                    )
                )
            )
        },
    )
    app = _app(
        mail_config,
        _services_config(
            imap_accounts={
                "bot": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                )
            }
        ),
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
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    with pytest.raises(ValueError, match="delete_message is not allowed"):
        app.delete_message(account="personal", folder="INBOX", message_id="42")


def test_list_messages_rejects_unknown_imap_folder() -> None:
    app = _app(
        _mail_config(),
        _default_services_config(),
        smtp_client_factory=lambda config: FakeSMTPClient(),
        imap_client_factory=RecordingIMAPClientFactory(),
    )

    with pytest.raises(ValueError, match="unconfigured folder"):
        app.list_messages(account="personal", folder="Archive")
