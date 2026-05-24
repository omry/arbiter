from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, cast

import hydra
from pydantic import Field

from .app import MailSentryApp
from .config import AppConfig, register_configs
from .imap import ImapClient
from .smtp import SmtpSubmissionClient

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


TransportMode = Literal["stdio", "sse", "streamable-http"]


RecipientList = Annotated[
    list[str],
    Field(
        description="JSON array of recipient email addresses.",
        examples=[["to@example.com"]],
    ),
]

OptionalRecipientList = Annotated[
    list[str] | None,
    Field(
        description="Optional JSON array of recipient email addresses.",
        examples=[["person@example.com"]],
    ),
]

AccountName = Annotated[
    str,
    Field(
        description=(
            "Configured account name returned by list_accounts. The selected account "
            "must have SMTP enabled."
        ),
        examples=["primary"],
        min_length=1,
    ),
]

SubjectLine = Annotated[
    str,
    Field(
        description="Email subject line.",
        examples=["Hello from MCP"],
        min_length=1,
    ),
]

TextBody = Annotated[
    str | None,
    Field(
        description="Optional plain-text body. Provide this or html_body.",
        examples=["Plain text message body."],
    ),
]

HtmlBody = Annotated[
    str | None,
    Field(
        description="Optional HTML body. Provide this or text_body.",
        examples=["<p>Hello from MCP</p>"],
    ),
]

ImapAccountName = Annotated[
    str,
    Field(
        description=(
            "Configured account name returned by list_accounts. The selected account "
            "must have IMAP enabled."
        ),
        examples=["primary"],
        min_length=1,
    ),
]

OptionalFolderName = Annotated[
    str | None,
    Field(
        description=(
            "Optional configured IMAP folder name. When omitted, the account's "
            "configured default_folder is used."
        ),
        examples=["INBOX"],
    ),
]

FolderName = Annotated[
    str,
    Field(
        description="Configured IMAP folder name for the selected account.",
        examples=["Archive"],
        min_length=1,
    ),
]

ImapMessageId = Annotated[
    str,
    Field(
        description=(
            "IMAP UID returned as a message id by list_messages or search_messages. "
            "UIDs are scoped to the selected account and folder."
        ),
        examples=["42"],
        min_length=1,
    ),
]

ImapSearchQuery = Annotated[
    str,
    Field(
        description="Text query used with IMAP TEXT search in the selected folder.",
        examples=["invoice"],
        min_length=1,
    ),
]

ImapMessageLimit = Annotated[
    int,
    Field(
        description="Maximum number of messages to return.",
        ge=1,
        le=100,
        examples=[20],
    ),
]


def build_app(cfg: AppConfig) -> MailSentryApp:
    return MailSentryApp(
        cfg.mail,
        smtp_client_factory=SmtpSubmissionClient,
        imap_client_factory=ImapClient,
    )


def build_server(cfg: AppConfig) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP

    app = build_app(cfg)
    server = FastMCP(
        cfg.server.name,
        stateless_http=cfg.server.stateless_http,
        json_response=cfg.server.json_response,
    )
    server.settings.host = cfg.server.host
    server.settings.port = cfg.server.port
    server.settings.streamable_http_path = cfg.server.path

    @server.tool(
        description=(
            "Return the configured accounts available to the caller, along with "
            "lightweight metadata needed to choose an account for later SMTP or "
            "future IMAP operations."
        )
    )
    def list_accounts() -> dict[str, object]:
        return {
            "accounts": app.list_accounts(),
        }

    @server.tool(
        description=(
            "Send a single email message through the configured SMTP submission "
            "server for the selected account. Use an account name returned by "
            "list_accounts, JSON arrays for to, cc, and bcc, at least one "
            "recipient in to, and at least one of text_body or html_body."
        )
    )
    def send_email(
        account: AccountName,
        to: RecipientList,
        subject: SubjectLine,
        text_body: TextBody = None,
        html_body: HtmlBody = None,
        cc: OptionalRecipientList = None,
        bcc: OptionalRecipientList = None,
    ) -> dict[str, object]:
        result = app.send_email(
            account=account,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
        )
        return {
            "ok": True,
            "message_id": result.message_id,
            "recipient_count": result.recipient_count,
        }

    @server.tool(
        description=(
            "List recent messages from a configured IMAP folder on the selected "
            "account. Message ids are IMAP UIDs scoped to that account and folder."
        )
    )
    def list_messages(
        account: ImapAccountName,
        folder: OptionalFolderName = None,
        limit: ImapMessageLimit = 20,
    ) -> dict[str, object]:
        return app.list_messages(account=account, folder=folder, limit=limit)

    @server.tool(
        description=(
            "Fetch one message by IMAP UID from a configured folder on the selected "
            "account, including plain text and HTML bodies when present."
        )
    )
    def get_message(
        account: ImapAccountName,
        message_id: ImapMessageId,
        folder: OptionalFolderName = None,
    ) -> dict[str, object]:
        return app.get_message(account=account, message_id=message_id, folder=folder)

    @server.tool(
        description=(
            "Search messages in a configured IMAP folder using an IMAP TEXT query. "
            "Results include message ids that can be passed to get_message."
        )
    )
    def search_messages(
        account: ImapAccountName,
        query: ImapSearchQuery,
        folder: OptionalFolderName = None,
        limit: ImapMessageLimit = 20,
    ) -> dict[str, object]:
        return app.search_messages(
            account=account,
            query=query,
            folder=folder,
            limit=limit,
        )

    @server.tool(
        description=(
            "Move one message by IMAP UID from a configured source folder to a "
            "configured destination folder on the selected account."
        )
    )
    def move_message(
        account: ImapAccountName,
        message_id: ImapMessageId,
        destination_folder: FolderName,
        folder: OptionalFolderName = None,
    ) -> dict[str, object]:
        return app.move_message(
            account=account,
            message_id=message_id,
            destination_folder=destination_folder,
            folder=folder,
        )

    @server.tool(
        description=(
            "Set or clear the IMAP seen flag for one message by UID. The selected "
            "account must grant read_write access to the standard seen flag."
        )
    )
    def mark_message_read(
        account: ImapAccountName,
        message_id: ImapMessageId,
        folder: OptionalFolderName = None,
        read: bool = True,
    ) -> dict[str, object]:
        return app.mark_message_read(
            account=account,
            message_id=message_id,
            folder=folder,
            read=read,
        )

    @server.tool(
        description=(
            "Delete one message by IMAP UID from a configured folder on the selected "
            "account. The account policy must explicitly allow IMAP delete."
        )
    )
    def delete_message(
        account: ImapAccountName,
        message_id: ImapMessageId,
        folder: OptionalFolderName = None,
    ) -> dict[str, object]:
        return app.delete_message(
            account=account,
            message_id=message_id,
            folder=folder,
        )

    return server


register_configs()


@hydra.main(version_base=None, config_path="conf", config_name="config")
def _main(cfg: AppConfig) -> None:
    server = build_server(cfg)
    server.run(transport=cast(TransportMode, cfg.server.transport))


def main() -> None:
    _main()
