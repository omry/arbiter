from __future__ import annotations

from .config import MailConfig
from .plugins.imap import IMAPRuntime
from .plugins.smtp import SendEmailResult, SMTPRuntime
from .services import RuntimeRegistry


class MailSentryApp:
    """Transitional facade while mail behavior moves behind service runtimes."""

    def __init__(
        self,
        mail_config: MailConfig,
        runtime_registry: RuntimeRegistry,
    ) -> None:
        self._mail_config = mail_config
        self.runtime_registry = runtime_registry

    def tool_names(self) -> list[str]:
        names = ["list_accounts"]
        if "smtp" in self.runtime_registry.keys():
            names.append("send_email")
        if "imap" in self.runtime_registry.keys():
            names.extend(
                [
                    "list_messages",
                    "get_message",
                    "search_messages",
                    "move_message",
                    "mark_message_read",
                    "delete_message",
                ]
            )
        return names

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
                "services": {},
            }
            services = summary["services"]
            assert isinstance(services, dict)
            for service_name, runtime in sorted(self.runtime_registry.items()):
                if service_name == "smtp":
                    services[service_name] = self._smtp_runtime().account_summary(
                        account_name,
                        account,
                        profile.services.smtp,
                    )
                elif service_name == "imap":
                    services[service_name] = self._imap_runtime().account_summary(
                        account_name,
                        account,
                        profile.services.imap,
                    )
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
        return self._smtp_runtime().send_email(
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
        return self._imap_runtime().list_messages(
            account=account,
            folder=folder,
            limit=limit,
        )

    def get_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        return self._imap_runtime().get_message(
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
        return self._imap_runtime().search_messages(
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
        return self._imap_runtime().move_message(
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
        return self._imap_runtime().mark_message_read(
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
        return self._imap_runtime().delete_message(
            account=account,
            message_id=message_id,
            folder=folder,
        )

    def _smtp_runtime(self) -> SMTPRuntime:
        return self.runtime_registry.require("smtp", SMTPRuntime)

    def _imap_runtime(self) -> IMAPRuntime:
        return self.runtime_registry.require("imap", IMAPRuntime)
