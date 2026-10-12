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
from typing import Any, cast

from .config import IMAPConfig, IMAPSystemFlag, MailTlsMode
from .errors import IMAPAuthenticationError, imap_operation_error
from arbiter_server.errors import OperationError, Replay, Transient


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


class IMAPOperationError(OperationError, RuntimeError):
    def __init__(
        self,
        diagnostic: str,
        *,
        code: str = "upstream_protocol_error",
        domain_code: str = "invalid_response",
        details: dict[str, object] | None = None,
        transient: Transient = "unknown",
        replay: Replay = "unknown",
    ) -> None:
        super().__init__(
            code,
            "IMAP operation failed.",
            plugin="imap",
            domain_code=domain_code,
            details=details,
            transient=transient,
            replay=replay,
        )
        self.args = (diagnostic,)


class IMAPClient:
    def __init__(self, config: IMAPConfig) -> None:
        self._config = config

    def test_connection(self, *, folders: Sequence[str]) -> None:
        with self._session() as server:
            status, data = server.noop()
            self._expect_ok(status, data, "NOOP")
            for folder in folders:
                self._select_folder(server, folder, readonly=True)

    def list_folders(self) -> list[str]:
        with self._session() as server:
            status, data = server.list()
            self._expect_ok(status, data, "list folders")
        return sorted(
            folder_name
            for item in data
            if (folder_name := self._parse_list_folder_name(item)) is not None
        )

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
                status, data = server.uid(
                    "MOVE",
                    uid,
                    self._quote_mailbox(destination_folder),
                )
            except imaplib.IMAP4.error as exc:
                # imaplib raises for tagged BAD replies instead of returning them.
                # A transport abort still leaves the write outcome uncertain.
                diagnostic = str(exc)
                if isinstance(exc, imaplib.IMAP4.abort) or not diagnostic.startswith(
                    "UID command error: BAD "
                ):
                    raise
                status, data = "BAD", [diagnostic]
            if self._move_status_supports_fallback(status, data):
                self._copy_then_delete(server, uid, destination_folder)
                return
            self._expect_ok(status, data, "move message")

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None:
        with self._session() as server:
            self._select_folder(server, folder, readonly=False)
            operation = "+FLAGS.SILENT" if read else "-FLAGS.SILENT"
            status, data = server.uid(
                "STORE",
                uid,
                operation,
                self._format_flag_list([IMAPSystemFlag.SEEN.value]),
            )
            self._expect_ok(status, data, "mark message read")

    def get_message_flags(self, *, folder: str, uid: str) -> list[str]:
        with self._session() as server:
            self._select_folder(server, folder, readonly=True)
            return self._fetch_flags(server, uid)

    def update_message_flags(
        self,
        *,
        folder: str,
        uid: str,
        add_flags: Sequence[str],
        remove_flags: Sequence[str],
    ) -> None:
        with self._session() as server:
            self._select_folder(server, folder, readonly=False)
            if add_flags:
                status, data = server.uid(
                    "STORE",
                    uid,
                    "+FLAGS.SILENT",
                    self._format_flag_list(add_flags),
                )
                self._expect_ok(status, data, "add message flags")
            if remove_flags:
                try:
                    status, data = server.uid(
                        "STORE",
                        uid,
                        "-FLAGS.SILENT",
                        self._format_flag_list(remove_flags),
                    )
                    self._expect_ok(status, data, "remove message flags")
                except (OperationError, imaplib.IMAP4.error, OSError):
                    if not add_flags:
                        raise
                    raise OperationError(
                        "partial_failure",
                        "IMAP added flags but could not complete flag removal.",
                        plugin="imap",
                        domain_code="flag_update_incomplete",
                        details={
                            "steps": {
                                "add_flags": "succeeded",
                                "remove_flags": "unknown",
                            }
                        },
                        replay="unknown",
                    ) from None

    def delete_message(self, *, folder: str, uid: str) -> None:
        with self._session() as server:
            self._select_folder(server, folder, readonly=False)
            self._mark_deleted(server, uid)
            try:
                self._expunge_uid(server, uid, "expunge deleted message")
            except (OperationError, imaplib.IMAP4.error, OSError):
                raise OperationError(
                    "partial_failure",
                    "IMAP marked the message deleted but could not confirm expunge.",
                    plugin="imap",
                    domain_code="delete_cleanup_failed",
                    details={
                        "steps": {"mark_deleted": "succeeded", "expunge": "unknown"}
                    },
                    replay="unknown",
                ) from None

    def append_message(
        self,
        *,
        folder: str,
        message_bytes: bytes,
        flags: Sequence[str] = (IMAPSystemFlag.SEEN.value,),
    ) -> None:
        try:
            session = self._session()
        except OperationError:
            raise
        except Exception as exc:
            # No APPEND command was attempted while establishing the session.
            raise imap_operation_error(exc, replay="safe") from None
        with session as server:
            quoted_folder = self._quote_mailbox(folder)
            try:
                # This client uses stdlib's ASCII command mode. Validate the
                # mailbox before APPEND can transmit its command or literal.
                quoted_folder.encode("ascii")
            except UnicodeError as exc:
                raise imap_operation_error(exc, replay="safe") from None
            date_time: Any = None
            status, data = server.append(
                quoted_folder,
                f"({' '.join(flags)})",
                date_time,
                message_bytes,
            )
            self._expect_ok(status, cast(list[Any], data), "append message")

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
            try:
                imap_client.login(self._config.username, self._config.password)
            except imaplib.IMAP4.abort:
                try:
                    imap_client.shutdown()
                except Exception:
                    pass
                raise
            except imaplib.IMAP4.error:
                try:
                    imap_client.shutdown()
                except Exception:
                    pass
                raise IMAPAuthenticationError("IMAP authentication failed") from None

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
        status, data = server.select(self._quote_mailbox(folder), readonly=readonly)
        access_mode = "read-only" if readonly else "read-write"
        self._expect_ok(
            status,
            data,
            f"open folder for {access_mode} access: {folder}",
        )

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
        status, data = server.uid("COPY", uid, self._quote_mailbox(destination_folder))
        self._expect_ok(status, data, "copy message")
        steps: dict[str, object] = {
            "copy": "succeeded",
            "mark_deleted": "unknown",
            "expunge": "not_attempted",
        }
        try:
            self._mark_deleted(server, uid)
            steps["mark_deleted"] = "succeeded"
            steps["expunge"] = "unknown"
            self._expunge_uid(server, uid, "expunge moved message")
        except (OperationError, imaplib.IMAP4.error, OSError):
            raise OperationError(
                "partial_failure",
                "IMAP copied the message but could not complete the move.",
                plugin="imap",
                domain_code="move_cleanup_failed",
                details={"steps": steps},
                replay="unsafe",
            ) from None

    def _mark_deleted(
        self,
        server: imaplib.IMAP4 | imaplib.IMAP4_SSL,
        uid: str,
    ) -> None:
        status, data = server.uid(
            "STORE",
            uid,
            "+FLAGS.SILENT",
            self._format_flag_list([IMAPSystemFlag.DELETED.value]),
        )
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
            raise IMAPOperationError(
                f"IMAP failed to {action}: {status} {self._format_response_data(data)}",
                code=(
                    "upstream_rejected"
                    if status.upper() in {"NO", "BAD"}
                    else "upstream_protocol_error"
                ),
                domain_code=(
                    "command_rejected"
                    if status.upper() in {"NO", "BAD"}
                    else "invalid_response"
                ),
                details=(
                    {"imap_status": status.upper()}
                    if status.upper() in {"NO", "BAD"}
                    else None
                ),
            )

    def _format_response_data(self, data: list[Any]) -> str:
        return "[" + ", ".join(self._format_response_item(item) for item in data) + "]"

    def _format_response_item(self, item: object) -> str:
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="replace")
        if isinstance(item, tuple):
            return (
                "("
                + ", ".join(self._format_response_item(value) for value in item)
                + ")"
            )
        if isinstance(item, list):
            return self._format_response_data(item)
        return str(item)

    def _format_flag_list(self, flags: Sequence[str]) -> str:
        return "(" + " ".join(flags) + ")"

    def _parse_list_folder_name(self, item: object) -> str | None:
        if item is None:
            return None
        raw = (
            item.decode("utf-8", errors="replace")
            if isinstance(item, bytes)
            else str(item)
        )
        match = re.search(r'\s(?:"((?:[^"\\]|\\.)*)"|([^ ]+))$', raw)
        if match is None:
            return None
        folder_name = match.group(1) if match.group(1) is not None else match.group(2)
        return folder_name.replace(r"\"", '"').replace(r"\\", "\\")

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

    def _quote_mailbox(self, folder: str) -> str:
        return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'

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
        raise IMAPOperationError(
            f"attachment not found: {attachment_id}",
            code="not_found",
            domain_code="attachment_not_found",
            transient="no",
            replay="safe",
        )

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
        except Exception:
            if exc is None:
                raise
