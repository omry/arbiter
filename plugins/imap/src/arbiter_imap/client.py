from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sequence
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, getaddresses
import imaplib
import re
import ssl
from typing import Any

from .config import IMAPConfig, MailTlsMode


@dataclass(frozen=True)
class IMAPAttachment:
    id: str
    filename: str | None
    content_type: str
    size: int
    disposition: str | None
    content_id: str | None
    inline: bool


@dataclass(frozen=True)
class IMAPAttachmentContent:
    attachment: IMAPAttachment
    content: bytes


@dataclass(frozen=True)
class FetchedIMAPMessage:
    uid: str
    subject: str
    from_addr: str
    to: list[str]
    cc: list[str]
    date: str
    flags: list[str]
    rfc822_message_id: str | None
    text_body: str | None
    html_body: str | None
    snippet: str
    attachments: list[IMAPAttachment] = field(default_factory=list)


class IMAPOperationError(RuntimeError):
    pass


class IMAPClient:
    def __init__(self, config: IMAPConfig) -> None:
        self._config = config

    def test_connection(self, *, folders: Sequence[str]) -> None:
        with self._session() as server:
            status, data = server.noop()
            self._expect_ok(status, data, "NOOP")
            for folder in folders:
                self._select_folder(server, folder, readonly=True)

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]:
        with self._session() as server:
            self._select_folder(server, folder, readonly=True)
            uids = self._search_uids(server, "ALL")
            selected_uids = list(reversed(uids))[:limit]
            return [self._fetch_message(server, uid) for uid in selected_uids]

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage:
        with self._session() as server:
            self._select_folder(server, folder, readonly=True)
            return self._fetch_message(server, uid)

    def get_attachment(
        self,
        *,
        folder: str,
        uid: str,
        attachment_id: str,
    ) -> IMAPAttachmentContent:
        with self._session() as server:
            self._select_folder(server, folder, readonly=True)
            message_bytes = self._fetch_message_bytes(server, uid)
        email_message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        return self._extract_attachment_content(
            email_message,
            attachment_id=attachment_id,
        )

    def search_messages(
        self, *, folder: str, query: str, limit: int
    ) -> list[FetchedIMAPMessage]:
        with self._session() as server:
            self._select_folder(server, folder, readonly=True)
            uids = self._search_uids(server, "TEXT", self._quote_search_text(query))
            selected_uids = list(reversed(uids))[:limit]
            return [self._fetch_message(server, uid) for uid in selected_uids]

    def move_message(
        self, *, source_folder: str, uid: str, destination_folder: str
    ) -> None:
        with self._session() as server:
            self._select_folder(server, source_folder, readonly=False)
            try:
                status, data = server.uid("MOVE", uid, destination_folder)
                if self._move_status_supports_fallback(status, data):
                    self._copy_then_delete(server, uid, destination_folder)
                    return
                self._expect_ok(status, data, "move message")
            except imaplib.IMAP4.error:
                self._copy_then_delete(server, uid, destination_folder)

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
        with self._session() as server:
            self._select_folder(server, folder, readonly=False)
            operation = "+FLAGS.SILENT" if read else "-FLAGS.SILENT"
            status, data = server.uid("STORE", uid, operation, r"(\Seen)")
            self._expect_ok(status, data, "mark message read")

    def delete_message(self, *, folder: str, uid: str) -> None:
        with self._session() as server:
            self._select_folder(server, folder, readonly=False)
            self._mark_deleted(server, uid)
            self._expunge_uid(server, uid, "expunge deleted message")

    def _session(self) -> IMAPSession:
        return IMAPSession(self._connect())

    def _connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        ssl_context = self._build_ssl_context()
        imap_client: imaplib.IMAP4 | imaplib.IMAP4_SSL
        if self._config.tls == MailTlsMode.implicit:
            imap_client = imaplib.IMAP4_SSL(
                self._config.host,
                self._config.port,
                ssl_context=ssl_context,
                timeout=self._config.timeout_seconds,
            )
        else:
            imap_client = imaplib.IMAP4(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
            )
            if self._config.tls == MailTlsMode.starttls:
                imap_client.starttls(ssl_context=ssl_context)

        if self._config.username:
            imap_client.login(self._config.username, self._config.password)

        return imap_client

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self._config.verify_peer:
            return ssl.create_default_context()

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _select_folder(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        folder: str,
        *,
        readonly: bool,
    ) -> None:
        status, data = server.select(folder, readonly=readonly)
        self._expect_ok(status, data, f"select folder {folder}")

    def _search_uids(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        *criteria: str,
    ) -> list[str]:
        status, data = server.uid("SEARCH", None, *criteria)  # type: ignore[arg-type]
        self._expect_ok(status, data, "search messages")
        if not data:
            return []
        raw_uids = data[0]
        if isinstance(raw_uids, bytes):
            return [uid.decode("ascii") for uid in raw_uids.split()]
        if isinstance(raw_uids, str):
            return raw_uids.split()
        return []

    def _fetch_message(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
    ) -> FetchedIMAPMessage:
        flags = self._fetch_flags(server, uid)
        message_bytes = self._fetch_message_bytes(server, uid)
        email_message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        text_body, html_body = self._extract_bodies(email_message)
        attachments = self._extract_attachments(email_message)
        snippet = self._snippet_from_body(text_body or html_body or "")
        return FetchedIMAPMessage(
            uid=uid,
            subject=email_message.get("Subject", ""),
            from_addr=self._first_address(email_message, "From"),
            to=self._addresses(email_message, "To"),
            cc=self._addresses(email_message, "Cc"),
            date=email_message.get("Date", ""),
            flags=flags,
            rfc822_message_id=email_message.get("Message-ID"),
            text_body=text_body,
            html_body=html_body,
            snippet=snippet,
            attachments=attachments,
        )

    def _fetch_flags(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
    ) -> list[str]:
        status, data = server.uid("FETCH", uid, "(FLAGS)")
        self._expect_ok(status, data, "fetch message flags")
        flags: list[str] = []
        for item in data:
            raw = self._raw_fetch_item(item)
            if raw is None:
                continue
            match = re.search(rb"FLAGS \((.*?)\)", raw)
            if match is None:
                continue
            flags.extend(
                flag.decode("utf-8", errors="replace")
                for flag in match.group(1).split()
            )
        return flags

    def _fetch_message_bytes(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
    ) -> bytes:
        status, data = server.uid("FETCH", uid, "(RFC822)")
        self._expect_ok(status, data, "fetch message body")
        for item in data:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], bytes)
            ):
                return item[1]
        raise IMAPOperationError(f"IMAP fetch for UID {uid} did not return RFC822 data")

    def _copy_then_delete(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
        destination_folder: str,
    ) -> None:
        status, data = server.uid("COPY", uid, destination_folder)
        self._expect_ok(status, data, "copy message")
        self._mark_deleted(server, uid)
        self._expunge_uid(server, uid, "expunge moved message")

    def _mark_deleted(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
    ) -> None:
        status, data = server.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
        self._expect_ok(status, data, "mark message deleted")

    def _expunge_uid(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
        action: str,
    ) -> None:
        status, data = server.uid("EXPUNGE", uid)
        self._expect_ok(status, data, action)

    def _expect_ok(self, status: str, data: list[Any], action: str) -> None:
        if status.upper() != "OK":
            raise IMAPOperationError(f"IMAP {action} failed: {status} {data!r}")

    def _move_status_supports_fallback(self, status: str, data: list[Any]) -> bool:
        normalized_status = status.upper()
        if normalized_status not in {"BAD", "NO"}:
            return False

        response_text = " ".join(
            (
                item.decode("utf-8", errors="replace")
                if isinstance(item, bytes)
                else str(item)
            )
            for item in data
        ).lower()
        return any(
            marker in response_text
            for marker in (
                "move unsupported",
                "move unavailable",
                "move not supported",
                "unknown command",
                "unrecognized command",
            )
        )

    def _raw_fetch_item(self, item: object) -> bytes | None:
        if isinstance(item, bytes):
            return item
        if isinstance(item, tuple) and item and isinstance(item[0], bytes):
            return item[0]
        return None

    def _quote_search_text(self, query: str) -> str:
        return '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _addresses(self, message: EmailMessage, header_name: str) -> list[str]:
        values = message.get_all(header_name, [])
        return [
            formataddr((display_name, address)) if display_name else address
            for display_name, address in getaddresses(values)
            if address
        ]

    def _first_address(self, message: EmailMessage, header_name: str) -> str:
        addresses = self._addresses(message, header_name)
        if not addresses:
            return ""
        return addresses[0]

    def _extract_bodies(self, message: EmailMessage) -> tuple[str | None, str | None]:
        text_body: str | None = None
        html_body: str | None = None

        if message.is_multipart():
            for part in message.walk():
                email_part = part
                if email_part.is_multipart():
                    continue
                if email_part.get_content_disposition() == "attachment":
                    continue
                content_type = email_part.get_content_type()
                if content_type == "text/plain" and text_body is None:
                    text_body = self._part_content(email_part)
                elif content_type == "text/html" and html_body is None:
                    html_body = self._part_content(email_part)
            return text_body, html_body

        content_type = message.get_content_type()
        if content_type == "text/html":
            return None, self._part_content(message)
        return self._part_content(message), None

    def _part_content(self, part: EmailMessage) -> str:
        content = part.get_content()
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            return content.decode(
                part.get_content_charset() or "utf-8", errors="replace"
            )
        return str(content)

    def _extract_attachments(self, message: EmailMessage) -> list[IMAPAttachment]:
        attachments: list[IMAPAttachment] = []
        for index, part in enumerate(self._iter_leaf_parts(message), start=1):
            attachment = self._attachment_metadata(part, index)
            if attachment is None:
                continue
            attachments.append(attachment)
        return attachments

    def _extract_attachment_content(
        self,
        message: EmailMessage,
        *,
        attachment_id: str,
    ) -> IMAPAttachmentContent:
        for index, part in enumerate(self._iter_leaf_parts(message), start=1):
            attachment = self._attachment_metadata(part, index)
            if attachment is None or attachment.id != attachment_id:
                continue
            content = self._part_payload_bytes(part)
            return IMAPAttachmentContent(
                attachment=attachment,
                content=content,
            )
        raise IMAPOperationError(f"attachment not found: {attachment_id}")

    def _attachment_metadata(
        self,
        part: EmailMessage,
        index: int,
    ) -> IMAPAttachment | None:
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        content_id = part.get("Content-ID")
        if (
            disposition not in {"attachment", "inline"}
            and filename is None
            and content_id is None
        ):
            return None
        return IMAPAttachment(
            id=f"part-{index}",
            filename=filename,
            content_type=part.get_content_type(),
            size=len(self._part_payload_bytes(part)),
            disposition=disposition,
            content_id=content_id,
            inline=disposition == "inline",
        )

    def _iter_leaf_parts(self, message: EmailMessage) -> list[EmailMessage]:
        if message.is_multipart():
            return [
                part
                for part in message.walk()
                if isinstance(part, EmailMessage) and not part.is_multipart()
            ]
        return [message]

    def _part_payload_size(self, part: EmailMessage) -> int:
        return len(self._part_payload_bytes(part))

    def _part_payload_bytes(self, part: EmailMessage) -> bytes:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload
        content = part.get_content()
        if isinstance(content, str):
            return content.encode(part.get_content_charset() or "utf-8")
        if isinstance(content, bytes):
            return content
        return b""

    def _snippet_from_body(self, body: str) -> str:
        compact = " ".join(body.split())
        return compact[:240]


class IMAPSession:
    def __init__(self, server: imaplib.IMAP4 | imaplib.IMAP4_SSL) -> None:
        self._server = server

    def __enter__(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        return self._server

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        try:
            self._server.logout()
        except OSError:
            return
