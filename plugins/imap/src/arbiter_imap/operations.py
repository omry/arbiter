from __future__ import annotations

from dataclasses import dataclass, field

from arbiter_server.services import OperationDescriptor

from .config import IMAPSystemFlag


@dataclass(frozen=True)
class ListFoldersInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    root: str | None = field(
        default=None,
        metadata={
            "description": (
                "Optional configured folder prefix to browse or search beneath. "
                "Omit to start at the account root."
            ),
        },
    )
    recursive: bool = field(
        default=False,
        metadata={
            "description": "Whether to include all configured descendants under root."
        },
    )
    limit: int = field(
        default=50,
        metadata={
            "description": (
                "Maximum number of folders to return. Results include truncated=true "
                "when more folders match."
            ),
            "minimum": 1,
            "maximum": 100,
        },
    )


@dataclass(frozen=True)
class ListMessagesInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    limit: int = field(
        default=20,
        metadata={
            "description": "Maximum number of messages to return.",
            "minimum": 1,
            "maximum": 100,
        },
    )


@dataclass(frozen=True)
class GetMessageInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )


@dataclass(frozen=True)
class GetAttachmentInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    attachment_id: str = field(
        metadata={
            "description": "Attachment MIME-part id returned by imap:get_message."
        },
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )


@dataclass(frozen=True)
class SearchMessagesInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    query: str = field(metadata={"description": "IMAP TEXT search query."})
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    limit: int = field(
        default=20,
        metadata={
            "description": "Maximum number of messages to return.",
            "minimum": 1,
            "maximum": 100,
        },
    )


@dataclass(frozen=True)
class MoveMessageInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    destination_folder: str = field(
        metadata={"description": "Configured destination folder name."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )


@dataclass(frozen=True)
class MarkMessageReadInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    read: bool = field(
        default=True,
        metadata={"description": "Whether the message should be marked read."},
    )


@dataclass(frozen=True)
class GetMessageFlagsInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )


@dataclass(frozen=True)
class UpdateMessageFlagsInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    add_flags: list[str] = field(
        default_factory=list,
        metadata={
            "description": "IMAP system flag enum names or configured user flag names."
        },
    )
    remove_flags: list[str] = field(
        default_factory=list,
        metadata={
            "description": "IMAP system flag enum names or configured user flag names."
        },
    )


@dataclass(frozen=True)
class AppendMessageInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    message: str | None = field(
        default=None,
        metadata={"description": "Raw RFC 5322 message text to append."},
    )
    flags: list[str] = field(
        default_factory=lambda: [IMAPSystemFlag.SEEN.name],
        metadata={
            "description": "IMAP system flag enum names or configured user flag names."
        },
    )


@dataclass(frozen=True)
class SaveDraftInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message: str = field(
        metadata={"description": "Raw RFC 5322 draft message text to append."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured Drafts folder name. Defaults to the account's configured "
                "DRAFTS folder."
            ),
        },
    )


@dataclass(frozen=True)
class SearchFoldersInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    query: str = field(
        metadata={"description": "Text to match against configured folder metadata."},
    )
    root: str | None = field(
        default=None,
        metadata={
            "description": (
                "Optional configured folder prefix to browse or search beneath. "
                "Omit to start at the account root."
            ),
        },
    )
    recursive: bool = field(
        default=True,
        metadata={
            "description": "Whether to include all configured descendants under root."
        },
    )
    limit: int = field(
        default=20,
        metadata={
            "description": (
                "Maximum number of folders to return. Results include truncated=true "
                "when more folders match."
            ),
            "minimum": 1,
            "maximum": 100,
        },
    )


@dataclass(frozen=True)
class DeleteMessageInput:
    account: str = field(
        metadata={"description": "Configured IMAP account name."},
    )
    message_id: str = field(
        metadata={"description": "IMAP UID scoped to the selected account and folder."},
    )
    folder: str | None = field(
        default=None,
        metadata={
            "description": (
                "Configured IMAP folder name. Defaults to the account default folder."
            ),
        },
    )
    permanent: bool = field(
        default=False,
        metadata={
            "description": (
                "Hard-delete instead of moving to an accessible TRASH folder."
            ),
        },
    )


IMAP_OPERATION_DESCRIPTORS = (
    OperationDescriptor(
        name="list_folders",
        description=(
            "List configured IMAP folders for the selected account, optionally "
            "beneath a folder prefix."
        ),
        input_schema=ListFoldersInput,
    ),
    OperationDescriptor(
        name="list_messages",
        description=(
            "List recent messages from a configured IMAP folder on the selected "
            "account."
        ),
        input_schema=ListMessagesInput,
    ),
    OperationDescriptor(
        name="get_message",
        description=(
            "Fetch one message by IMAP UID from a configured folder on the selected "
            "account."
        ),
        input_schema=GetMessageInput,
    ),
    OperationDescriptor(
        name="get_attachment",
        description=(
            "Create a one-time server artifact URL for one attachment by message "
            "UID and attachment id. The attachment bytes are not returned in the "
            "tool result. Use local file save only when the user explicitly asks "
            "to save the attachment."
        ),
        input_schema=GetAttachmentInput,
    ),
    OperationDescriptor(
        name="search_messages",
        description=(
            "Search messages in a configured IMAP folder using an IMAP TEXT query."
        ),
        input_schema=SearchMessagesInput,
    ),
    OperationDescriptor(
        name="move_message",
        description=(
            "Move one message by IMAP UID from a configured source folder to a "
            "configured destination folder."
        ),
        input_schema=MoveMessageInput,
    ),
    OperationDescriptor(
        name="mark_message_read",
        description="Set or clear the IMAP SEEN flag for one message by UID.",
        input_schema=MarkMessageReadInput,
    ),
    OperationDescriptor(
        name="get_message_flags",
        description="Fetch visible flags for one message by IMAP UID.",
        input_schema=GetMessageFlagsInput,
    ),
    OperationDescriptor(
        name="update_message_flags",
        description="Add or remove allowed IMAP flags for one message by UID.",
        input_schema=UpdateMessageFlagsInput,
    ),
    OperationDescriptor(
        name="append_message",
        description="Append a raw message to an allowed IMAP folder.",
        input_schema=AppendMessageInput,
    ),
    OperationDescriptor(
        name="save_draft",
        description=(
            "Append a raw draft message to the account's configured Drafts folder."
        ),
        input_schema=SaveDraftInput,
    ),
    OperationDescriptor(
        name="search_folders",
        description=(
            "Search configured IMAP folders for the selected account by folder "
            "name, description, or kind."
        ),
        input_schema=SearchFoldersInput,
    ),
    OperationDescriptor(
        name="delete_message",
        description=(
            "Delete one message by IMAP UID from a configured folder on the selected "
            "account."
        ),
        input_schema=DeleteMessageInput,
    ),
)
