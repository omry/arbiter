from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..services import ServicePluginContext, ToolServer


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


class SmtpServicePlugin:
    name = "smtp"

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None:
        app = context.app

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
