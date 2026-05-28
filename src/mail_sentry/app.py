from __future__ import annotations

from time import monotonic

from .config import MailConfig
from .plugins.imap import IMAPClientFactory, IMAPRuntime
from .plugins.smtp import SendEmailResult, SMTPClientFactory, SMTPRuntime, TimeProvider
from .services import RuntimeRegistry


class MailSentryApp:
    """Transitional facade while mail behavior moves behind service runtimes."""

    def __init__(
        self,
        mail_config: MailConfig,
        smtp_client_factory: SMTPClientFactory,
        imap_client_factory: IMAPClientFactory | None = None,
        time_provider: TimeProvider = monotonic,
    ) -> None:
        self._mail_config = mail_config
        self.smtp = SMTPRuntime(
            mail_config,
            smtp_client_factory=smtp_client_factory,
            time_provider=time_provider,
        )
        self.imap = IMAPRuntime(
            mail_config,
            imap_client_factory=imap_client_factory,
        )
        self.runtime_registry = RuntimeRegistry(
            {
                self.smtp.service_name: self.smtp,
                self.imap.service_name: self.imap,
            }
        )

    def tool_names(self) -> list[str]:
        return [
            "list_accounts",
            "send_email",
            "list_messages",
            "get_message",
            "search_messages",
            "move_message",
            "mark_message_read",
            "delete_message",
        ]

    def list_accounts(self) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        for account_name in sorted(self._mail_config.accounts):
            account = self._mail_config.accounts[account_name]
            profile = self._mail_config.account_access_profiles[
                account.account_access_profile
            ]
            summary: dict[str, object] = {
                "name": account_name,
                "description": account.description,
                "account_access_profile": account.account_access_profile,
                "smtp": self.smtp.account_summary(account, profile.services.smtp),
                "imap": self.imap.account_summary(account, profile.services.imap),
            }
            summaries.append(summary)
        return summaries

    def send_email(
        self,
        account: str,
        to: list[str],
        subject: str,
        text_body: str | None = None,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> SendEmailResult:
        return self.smtp.send_email(
            account=account,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
        )

    def list_messages(
        self,
        account: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        return self.imap.list_messages(account=account, folder=folder, limit=limit)

    def get_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        return self.imap.get_message(
            account=account,
            message_id=message_id,
            folder=folder,
        )

    def search_messages(
        self,
        account: str,
        query: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        return self.imap.search_messages(
            account=account,
            query=query,
            folder=folder,
            limit=limit,
        )

    def move_message(
        self,
        account: str,
        message_id: str,
        destination_folder: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        return self.imap.move_message(
            account=account,
            message_id=message_id,
            destination_folder=destination_folder,
            folder=folder,
        )

    def mark_message_read(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
        read: bool = True,
    ) -> dict[str, object]:
        return self.imap.mark_message_read(
            account=account,
            message_id=message_id,
            folder=folder,
            read=read,
        )

    def delete_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        return self.imap.delete_message(
            account=account,
            message_id=message_id,
            folder=folder,
        )
