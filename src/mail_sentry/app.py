from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Callable, Protocol

from .config import (
    AccountConfig,
    ImapAccessPolicyConfig,
    ImapConfigLike,
    ImapFlagMode,
    MailConfig,
    SmtpConfigLike,
    resolve_imap_flag_mode,
    resolve_system_flag_key,
)
from .imap import FetchedImapMessage


@dataclass(frozen=True)
class SendEmailResult:
    tool: str
    message_id: str
    recipient_count: int


class SmtpClientLike(Protocol):
    def send(
        self, message: EmailMessage, sender: str, recipients: list[str]
    ) -> None: ...


SmtpClientFactory = Callable[[SmtpConfigLike], SmtpClientLike]


class ImapClientLike(Protocol):
    def list_messages(self, *, folder: str, limit: int) -> list[FetchedImapMessage]: ...

    def get_message(self, *, folder: str, uid: str) -> FetchedImapMessage: ...

    def search_messages(
        self, *, folder: str, query: str, limit: int
    ) -> list[FetchedImapMessage]: ...

    def move_message(
        self, *, source_folder: str, uid: str, destination_folder: str
    ) -> None: ...

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None: ...

    def delete_message(self, *, folder: str, uid: str) -> None: ...


ImapClientFactory = Callable[[ImapConfigLike], ImapClientLike]


class MailSentryApp:
    """Minimal application surface before wiring a concrete MCP SDK."""

    def __init__(
        self,
        mail_config: MailConfig,
        smtp_client_factory: SmtpClientFactory,
        imap_client_factory: ImapClientFactory | None = None,
    ) -> None:
        self._mail_config = mail_config
        self._smtp_client_factory = smtp_client_factory
        self._imap_client_factory = imap_client_factory

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
            smtp_send_state = self._smtp_send_state(account, profile.allow_smtp_send)
            imap_enabled = account.imap is not None
            smtp_summary: dict[str, object] = {
                "send": smtp_send_state,
            }
            imap_summary: dict[str, object] = {
                "enabled": imap_enabled,
            }
            summary: dict[str, object] = {
                "name": account_name,
                "description": account.description,
                "account_access_profile": account.account_access_profile,
                "sensitivity_tier": account.sensitivity_tier.value,
                "smtp": smtp_summary,
                "imap": imap_summary,
            }
            if imap_enabled:
                imap_summary["message"] = self._imap_message_summary(profile.imap)
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
        smtp_config = self._resolve_smtp_config(account)
        recipients_to = self._normalize_recipients("to", to)
        recipients_cc = self._normalize_recipients("cc", cc or [])
        recipients_bcc = self._normalize_recipients("bcc", bcc or [])

        if not text_body and not html_body:
            raise ValueError("send_email requires text_body or html_body")

        normalized_subject = subject.strip()
        if not normalized_subject:
            raise ValueError("send_email requires a non-empty subject")

        sender = formataddr((smtp_config.from_name, smtp_config.from_email))
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipients_to)
        if recipients_cc:
            message["Cc"] = ", ".join(recipients_cc)
        message["Subject"] = normalized_subject
        message["Message-ID"] = make_msgid(domain=self._sender_domain(smtp_config))

        if text_body:
            message.set_content(text_body)
            if html_body:
                message.add_alternative(html_body, subtype="html")
        else:
            message.set_content(html_body or "", subtype="html")

        envelope_recipients = recipients_to + recipients_cc + recipients_bcc
        smtp_client = self._smtp_client_factory(smtp_config)
        smtp_client.send(
            message,
            sender=smtp_config.from_email,
            recipients=envelope_recipients,
        )

        return SendEmailResult(
            tool="send_email",
            message_id=str(message["Message-ID"]),
            recipient_count=len(envelope_recipients),
        )

    def list_messages(
        self,
        account: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_imap_context(
            "list_messages", account, folder
        )
        if not imap_policy.allow_read:
            raise ValueError(f"list_messages is not allowed for account: {account}")

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_imap_client(imap_config).list_messages(
            folder=folder_name,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_name,
            "messages": [
                self._imap_message_to_dict(message, imap_policy, include_body=False)
                for message in messages
            ],
        }

    def get_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_imap_context(
            "get_message", account, folder
        )
        if not imap_policy.allow_read:
            raise ValueError(f"get_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        message = self._make_imap_client(imap_config).get_message(
            folder=folder_name,
            uid=uid,
        )
        return {
            "account": account,
            "folder": folder_name,
            "message": self._imap_message_to_dict(
                message, imap_policy, include_body=True
            ),
        }

    def search_messages(
        self,
        account: str,
        query: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_imap_context(
            "search_messages", account, folder
        )
        if not imap_policy.allow_search:
            raise ValueError(f"search_messages is not allowed for account: {account}")

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_messages requires a non-empty query")

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_imap_client(imap_config).search_messages(
            folder=folder_name,
            query=normalized_query,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_name,
            "query": normalized_query,
            "messages": [
                self._imap_message_to_dict(message, imap_policy, include_body=False)
                for message in messages
            ],
        }

    def move_message(
        self,
        account: str,
        message_id: str,
        destination_folder: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, source_folder = self._resolve_imap_context(
            "move_message", account, folder
        )
        if not imap_policy.allow_move:
            raise ValueError(f"move_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        normalized_destination = self._resolve_imap_folder(
            "move_message", imap_config, destination_folder
        )
        self._make_imap_client(imap_config).move_message(
            source_folder=source_folder,
            uid=uid,
            destination_folder=normalized_destination,
        )
        return {
            "ok": True,
            "account": account,
            "source_folder": source_folder,
            "destination_folder": normalized_destination,
            "message_id": uid,
        }

    def mark_message_read(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
        read: bool = True,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_imap_context(
            "mark_message_read", account, folder
        )
        if resolve_imap_flag_mode(imap_policy, "\\Seen") is not ImapFlagMode.read_write:
            raise ValueError(
                f"mark_message_read requires read_write access to the seen flag for account: {account}"
            )

        uid = self._normalize_message_uid(message_id)
        self._make_imap_client(imap_config).mark_message_read(
            folder=folder_name,
            uid=uid,
            read=read,
        )
        return {
            "ok": True,
            "account": account,
            "folder": folder_name,
            "message_id": uid,
            "read": read,
        }

    def delete_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_imap_context(
            "delete_message", account, folder
        )
        if not imap_policy.allow_delete:
            raise ValueError(f"delete_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        self._make_imap_client(imap_config).delete_message(
            folder=folder_name,
            uid=uid,
        )
        return {
            "ok": True,
            "account": account,
            "folder": folder_name,
            "message_id": uid,
        }

    def _normalize_recipients(
        self, field_name: str, recipients: list[str]
    ) -> list[str]:
        normalized = [
            recipient.strip() for recipient in recipients if recipient.strip()
        ]
        if field_name == "to" and not normalized:
            raise ValueError("send_email requires at least one recipient in to")

        for recipient in normalized:
            if "@" not in recipient:
                raise ValueError(f"send_email received an invalid {field_name} address")

        return normalized

    def _sender_domain(self, smtp_config: SmtpConfigLike) -> str:
        _, _, domain = smtp_config.from_email.partition("@")
        return domain or "localhost"

    def _resolve_smtp_config(self, account_name: str) -> SmtpConfigLike:
        account = self._mail_config.accounts.get(account_name)
        if account is None:
            raise ValueError(f"send_email received an unknown account: {account_name}")

        if account.smtp is None:
            raise ValueError(
                f"send_email requires an SMTP-enabled account: {account_name}"
            )

        profile = self._mail_config.account_access_profiles[
            account.account_access_profile
        ]
        if not profile.allow_smtp_send:
            raise ValueError(f"send_email is not allowed for account: {account_name}")

        return account.smtp

    def _resolve_imap_context(
        self, tool_name: str, account_name: str, folder: str | None
    ) -> tuple[ImapConfigLike, ImapAccessPolicyConfig, str]:
        account = self._mail_config.accounts.get(account_name)
        if account is None:
            raise ValueError(f"{tool_name} received an unknown account: {account_name}")

        if account.imap is None:
            raise ValueError(
                f"{tool_name} requires an IMAP-enabled account: {account_name}"
            )

        profile = self._mail_config.account_access_profiles[
            account.account_access_profile
        ]
        folder_name = self._resolve_optional_imap_folder(
            tool_name, account.imap, folder
        )
        return account.imap, profile.imap, folder_name

    def _resolve_optional_imap_folder(
        self,
        tool_name: str,
        imap_config: ImapConfigLike,
        folder: str | None,
    ) -> str:
        folder_name = folder.strip() if folder else imap_config.default_folder
        if not folder_name:
            raise ValueError(
                f"{tool_name} requires folder when the account has no default_folder"
            )
        return self._resolve_imap_folder(tool_name, imap_config, folder_name)

    def _resolve_imap_folder(
        self,
        tool_name: str,
        imap_config: ImapConfigLike,
        folder: str,
    ) -> str:
        folder_name = folder.strip()
        if not folder_name:
            raise ValueError(f"{tool_name} requires a non-empty folder")
        if folder_name not in imap_config.folders:
            raise ValueError(f"{tool_name} received an unconfigured folder: {folder}")
        return folder_name

    def _make_imap_client(self, imap_config: ImapConfigLike) -> ImapClientLike:
        if self._imap_client_factory is None:
            raise RuntimeError("IMAP client factory is not configured")
        return self._imap_client_factory(imap_config)

    def _smtp_send_state(self, account: AccountConfig, allow_smtp_send: bool) -> str:
        if account.smtp is None:
            return "unavailable"
        if allow_smtp_send:
            return "allowed"
        return "disabled"

    def _imap_message_summary(
        self, imap_policy: ImapAccessPolicyConfig
    ) -> dict[str, object]:
        flags = self._imap_flag_summary(imap_policy)
        return {
            "read_allowed": imap_policy.allow_read,
            "move_allowed": imap_policy.allow_move,
            "delete_allowed": imap_policy.allow_delete,
            "flags": flags,
        }

    def _imap_flag_summary(
        self, imap_policy: ImapAccessPolicyConfig
    ) -> dict[str, object]:
        system_flags = {
            "seen": imap_policy.system_flags.seen,
            "flagged": imap_policy.system_flags.flagged,
            "answered": imap_policy.system_flags.answered,
            "deleted": imap_policy.system_flags.deleted,
            "draft": imap_policy.system_flags.draft,
        }
        flags: dict[str, object] = {
            flag_name: mode.value for flag_name, mode in system_flags.items()
        }

        user_flags = {
            flag_name: mode.value
            for flag_name, mode in sorted(imap_policy.user_flags.items())
            if mode is not ImapFlagMode.hidden
        }
        if user_flags:
            flags["user"] = user_flags

        return flags

    def _normalize_limit(self, limit: int) -> int:
        if limit < 1:
            raise ValueError("IMAP message limit must be at least 1")
        if limit > 100:
            raise ValueError("IMAP message limit must be at most 100")
        return limit

    def _normalize_message_uid(self, message_id: str) -> str:
        uid = message_id.strip()
        if not uid:
            raise ValueError("IMAP message_id must be non-empty")
        if not uid.isdigit():
            raise ValueError("IMAP message_id must be an IMAP UID")
        return uid

    def _imap_message_to_dict(
        self,
        message: FetchedImapMessage,
        imap_policy: ImapAccessPolicyConfig,
        *,
        include_body: bool,
    ) -> dict[str, object]:
        message_dict: dict[str, object] = {
            "id": message.uid,
            "uid": message.uid,
            "subject": message.subject,
            "from": message.from_addr,
            "to": message.to,
            "cc": message.cc,
            "date": message.date,
            "flags": self._visible_imap_flags(imap_policy, message.flags),
        }
        if message.rfc822_message_id:
            message_dict["rfc822_message_id"] = message.rfc822_message_id
        if message.snippet:
            message_dict["snippet"] = message.snippet
        if include_body:
            message_dict["text_body"] = message.text_body
            message_dict["html_body"] = message.html_body
        return message_dict

    def _visible_imap_flags(
        self,
        imap_policy: ImapAccessPolicyConfig,
        flags: list[str],
    ) -> list[str]:
        visible_flags: list[str] = []
        for flag in flags:
            mode = resolve_imap_flag_mode(imap_policy, flag)
            if mode is ImapFlagMode.hidden:
                continue
            visible_flags.append(resolve_system_flag_key(flag) or flag)
        return visible_flags
