from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..services import ServicePluginContext, ToolServer


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


class ImapServicePlugin:
    name = "imap"

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None:
        app = context.app

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
            return app.get_message(
                account=account,
                message_id=message_id,
                folder=folder,
            )

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
