from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable, Protocol, cast

from hydra.core.config_store import ConfigStore

from arbiter_server.artifacts import PluginArtifactStore
from arbiter_server.services import (
    CapabilityDescriptor,
    OperationDescriptor,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.version import distribution_version

from .config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    register_configs as register_imap_configs,
    resolve_imap_flag_mode,
    resolve_system_flag_key,
)

from .client import FetchedIMAPMessage, IMAPAttachmentContent

SERVER_API_VERSION = "0.9"


class IMAPClientProtocol(Protocol):
    def test_connection(self, *, folders: Sequence[str]) -> None: ...

    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]: ...

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage: ...

    def get_attachment(
        self,
        *,
        folder: str,
        uid: str,
        attachment_id: str,
    ) -> IMAPAttachmentContent: ...

    def search_messages(
        self,
        *,
        folder: str,
        query: str,
        limit: int,
    ) -> list[FetchedIMAPMessage]: ...

    def move_message(
        self,
        *,
        source_folder: str,
        uid: str,
        destination_folder: str,
    ) -> None: ...

    def mark_message_read(self, *, folder: str, uid: str, read: bool) -> None: ...

    def delete_message(self, *, folder: str, uid: str) -> None: ...

    def append_message(
        self,
        *,
        folder: str,
        message_bytes: bytes,
        flags: Sequence[str] = (r"\Seen",),
    ) -> None: ...


IMAPClientFactory = Callable[[IMAPConfig], IMAPClientProtocol]


def _object_schema(
    properties: Mapping[str, object],
    required: list[str],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": required,
        "additionalProperties": False,
    }


ACCOUNT_PROPERTY = {
    "type": "string",
    "description": "Configured IMAP account name.",
}
FOLDER_PROPERTY = {
    "type": "string",
    "description": "Configured IMAP folder name. Defaults to the account default folder.",
}
ROOT_FOLDER_PROPERTY = {
    "type": "string",
    "description": (
        "Optional configured folder prefix to browse or search beneath. "
        "Omit to start at the account root."
    ),
}
FOLDER_LIMIT_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": (
        "Maximum number of folders to return. Results include truncated=true "
        "when more folders match."
    ),
}
RECURSIVE_PROPERTY = {
    "type": "boolean",
    "description": "Whether to include all configured descendants under root.",
}
MESSAGE_ID_PROPERTY = {
    "type": "string",
    "description": "IMAP UID scoped to the selected account and folder.",
}
ATTACHMENT_ID_PROPERTY = {
    "type": "string",
    "description": "Attachment MIME-part id returned by imap:get_message.",
}
LIMIT_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": "Maximum number of messages to return.",
}
IMAP_OPERATION_DESCRIPTORS = (
    OperationDescriptor(
        name="list_folders",
        description=(
            "List configured IMAP folders for the selected account, optionally "
            "beneath a folder prefix."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "root": ROOT_FOLDER_PROPERTY,
                "recursive": RECURSIVE_PROPERTY,
                "limit": FOLDER_LIMIT_PROPERTY,
            },
            ["account"],
        ),
    ),
    OperationDescriptor(
        name="list_messages",
        description=(
            "List recent messages from a configured IMAP folder on the selected "
            "account."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "folder": FOLDER_PROPERTY,
                "limit": LIMIT_PROPERTY,
            },
            ["account"],
        ),
    ),
    OperationDescriptor(
        name="get_message",
        description=(
            "Fetch one message by IMAP UID from a configured folder on the selected "
            "account."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "message_id": MESSAGE_ID_PROPERTY,
                "folder": FOLDER_PROPERTY,
            },
            ["account", "message_id"],
        ),
    ),
    OperationDescriptor(
        name="get_attachment",
        description=(
            "Create a one-time server artifact URL for one attachment by message "
            "UID and attachment id. The attachment bytes are not returned in the "
            "tool result. Use local file save only when the user explicitly asks "
            "to save the attachment."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "message_id": MESSAGE_ID_PROPERTY,
                "attachment_id": ATTACHMENT_ID_PROPERTY,
                "folder": FOLDER_PROPERTY,
            },
            ["account", "message_id", "attachment_id"],
        ),
    ),
    OperationDescriptor(
        name="search_messages",
        description=(
            "Search messages in a configured IMAP folder using an IMAP TEXT query."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "query": {
                    "type": "string",
                    "description": "IMAP TEXT search query.",
                },
                "folder": FOLDER_PROPERTY,
                "limit": LIMIT_PROPERTY,
            },
            ["account", "query"],
        ),
    ),
    OperationDescriptor(
        name="move_message",
        description=(
            "Move one message by IMAP UID from a configured source folder to a "
            "configured destination folder."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "message_id": MESSAGE_ID_PROPERTY,
                "destination_folder": {
                    "type": "string",
                    "description": "Configured destination folder name.",
                },
                "folder": FOLDER_PROPERTY,
            },
            ["account", "message_id", "destination_folder"],
        ),
    ),
    OperationDescriptor(
        name="mark_message_read",
        description="Set or clear the IMAP seen flag for one message by UID.",
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "message_id": MESSAGE_ID_PROPERTY,
                "folder": FOLDER_PROPERTY,
                "read": {
                    "type": "boolean",
                    "description": "Whether the message should be marked read.",
                },
            },
            ["account", "message_id"],
        ),
    ),
    OperationDescriptor(
        name="search_folders",
        description=(
            "Search configured IMAP folders for the selected account by folder "
            "name, description, or kind."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "query": {
                    "type": "string",
                    "description": "Text to match against configured folder metadata.",
                },
                "root": ROOT_FOLDER_PROPERTY,
                "recursive": RECURSIVE_PROPERTY,
                "limit": FOLDER_LIMIT_PROPERTY,
            },
            ["account", "query"],
        ),
    ),
    OperationDescriptor(
        name="delete_message",
        description=(
            "Delete one message by IMAP UID from a configured folder on the selected "
            "account."
        ),
        input_schema=_object_schema(
            {
                "account": ACCOUNT_PROPERTY,
                "message_id": MESSAGE_ID_PROPERTY,
                "folder": FOLDER_PROPERTY,
            },
            ["account", "message_id"],
        ),
    ),
)


@dataclass(frozen=True)
class _FolderItems:
    items: list[dict[str, object]]
    truncated: bool


class IMAPRuntime:
    service_name = "imap"

    def __init__(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        imap_client_factory: IMAPClientFactory | None = None,
        artifact_store: PluginArtifactStore | None = None,
    ) -> None:
        self._accounts = cast(Mapping[str, IMAPConfig], accounts)
        self._policies = cast(
            Mapping[str, IMAPAccessPolicyConfig],
            policies,
        )
        self._imap_client_factory = imap_client_factory
        self._artifact_store = artifact_store
        self._validate_policy_references()

    def account_summaries(self) -> dict[str, object]:
        summaries: dict[str, object] = {}
        for account_name, account in sorted(self._accounts.items()):
            imap_policy = self._policies[account.policy]
            summaries[account_name] = {
                "description": account.description,
                "guidance": account.guidance,
                "policy": account.policy,
                "enabled": True,
                "confirmation_required": [
                    action.value for action in imap_policy.confirmation_required
                ],
                "message": self._message_summary(imap_policy),
            }
        return summaries

    def test_accounts(self) -> dict[str, object]:
        results: dict[str, object] = {}
        for account_name, imap_config in sorted(self._accounts.items()):
            folders = self._test_folders(imap_config)
            try:
                self._make_client(imap_config).test_connection(folders=folders)
            except Exception as exc:
                results[account_name] = {
                    "status": "failed",
                    "stage": "connect_auth_noop_examine",
                    "folders": folders,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                continue
            if not folders:
                results[account_name] = {
                    "status": "skipped",
                    "stage": "connect_auth_noop",
                    "checks": ["connect", "noop"],
                    "reason": "no configured IMAP folders to examine read-only",
                }
                continue
            results[account_name] = {
                "status": "ok",
                "stage": "connect_auth_noop_examine",
                "checks": ["connect", "noop", "examine"],
                "folders": folders,
            }
        return results

    def list_messages(
        self,
        account: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_context(
            "list_messages", account, folder
        )
        if not imap_policy.allow_read:
            raise ValueError(f"list_messages is not allowed for account: {account}")

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_client(imap_config).list_messages(
            folder=folder_name,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_name,
            "messages": [
                self._message_to_dict(message, imap_policy, include_body=False)
                for message in messages
            ],
        }

    def list_folders(
        self,
        account: str,
        root: str | None = None,
        recursive: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        imap_config = self._resolve_account_config("list_folders", account)
        normalized_root = self._normalize_folder_root(root)
        normalized_limit = self._normalize_folder_limit(limit)
        folder_items = self._folder_items(
            imap_config,
            root=normalized_root,
            recursive=recursive,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "root": normalized_root,
            "recursive": recursive,
            "limit": normalized_limit,
            "truncated": folder_items.truncated,
            "folders": folder_items.items,
        }

    def get_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_context(
            "get_message", account, folder
        )
        if not imap_policy.allow_read:
            raise ValueError(f"get_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        message = self._make_client(imap_config).get_message(
            folder=folder_name,
            uid=uid,
        )
        return {
            "account": account,
            "folder": folder_name,
            "message": self._message_to_dict(
                message,
                imap_policy,
                include_body=True,
            ),
        }

    def get_attachment(
        self,
        account: str,
        message_id: str,
        attachment_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_context(
            "get_attachment", account, folder
        )
        if not imap_policy.allow_read:
            raise ValueError(f"get_attachment is not allowed for account: {account}")
        if self._artifact_store is None:
            raise ValueError(
                "get_attachment requires server artifact storage; "
                "HTTP artifact delivery is unavailable"
            )

        uid = self._normalize_message_uid(message_id)
        normalized_attachment_id = self._normalize_attachment_id(attachment_id)
        attachment_content = self._make_client(imap_config).get_attachment(
            folder=folder_name,
            uid=uid,
            attachment_id=normalized_attachment_id,
        )
        attachment = attachment_content.attachment
        artifact = self._artifact_store.create(
            content=attachment_content.content,
            filename=attachment.filename,
            content_type=attachment.content_type,
            source={
                "account": account,
                "folder": folder_name,
                "message_id": uid,
                "attachment_id": attachment.id,
            },
        )
        return {
            "account": account,
            "folder": folder_name,
            "message_id": uid,
            "attachment": {
                "id": attachment.id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "disposition": attachment.disposition,
                "content_id": attachment.content_id,
                "inline": attachment.inline,
            },
            "delivery": "arbiter_artifact",
            "artifact": {
                **artifact.to_dict(),
                "handling": {
                    "prefer_inline": False,
                    "execute_locally": True,
                    "requires_explicit_user_request": True,
                    "path_interface": (
                        "arbiter artifact with-temp <url> -- <argv...{}...>"
                    ),
                    "stdin_interface": "arbiter artifact with-stdin <url> -- <argv...>",
                    "save_interface": "arbiter artifact save <url> <path>",
                    "save_requires_explicit_user_request": True,
                    "instructions": (
                        "Use the one-time URL only through an explicit artifact "
                        "reader such as `arbiter artifact get --stdout` for small "
                        "textual attachments. For binary attachments, prefer "
                        "`arbiter artifact with-temp <url> -- <argv...{}...>` "
                        "for path-based tools or "
                        "`arbiter artifact with-stdin <url> -- <argv...>` for "
                        "stdin-based tools. If the user explicitly asks to save "
                        "the attachment, use "
                        "`arbiter artifact save <url> <path>`. Do not "
                        "otherwise save, copy, or persist the file."
                    ),
                },
            },
        }

    def artifact_delivery_available(self) -> bool:
        return self._artifact_store is not None

    def search_messages(
        self,
        account: str,
        query: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config, imap_policy, folder_name = self._resolve_context(
            "search_messages", account, folder
        )
        if not imap_policy.allow_search:
            raise ValueError(f"search_messages is not allowed for account: {account}")

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_messages requires a non-empty query")

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_client(imap_config).search_messages(
            folder=folder_name,
            query=normalized_query,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_name,
            "query": normalized_query,
            "messages": [
                self._message_to_dict(message, imap_policy, include_body=False)
                for message in messages
            ],
        }

    def search_folders(
        self,
        account: str,
        query: str,
        root: str | None = None,
        recursive: bool = True,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config = self._resolve_account_config("search_folders", account)
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_folders requires a non-empty query")
        normalized_root = self._normalize_folder_root(root)
        normalized_limit = self._normalize_folder_limit(limit)
        query_text = normalized_query.casefold()
        folder_items = self._folder_items(
            imap_config,
            root=normalized_root,
            recursive=recursive,
            limit=normalized_limit,
            query=query_text,
        )
        return {
            "account": account,
            "query": normalized_query,
            "root": normalized_root,
            "recursive": recursive,
            "limit": normalized_limit,
            "truncated": folder_items.truncated,
            "folders": folder_items.items,
        }

    def move_message(
        self,
        account: str,
        message_id: str,
        destination_folder: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, imap_policy, source_folder = self._resolve_context(
            "move_message", account, folder
        )
        if not imap_policy.allow_move:
            raise ValueError(f"move_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        normalized_destination = self._resolve_folder(
            "move_message", imap_config, destination_folder
        )
        self._make_client(imap_config).move_message(
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
        imap_config, imap_policy, folder_name = self._resolve_context(
            "mark_message_read", account, folder
        )
        if resolve_imap_flag_mode(imap_policy, "\\Seen") is not IMAPFlagMode.read_write:
            raise ValueError(
                f"mark_message_read requires read_write access to the seen flag for account: {account}"
            )

        uid = self._normalize_message_uid(message_id)
        self._make_client(imap_config).mark_message_read(
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
        imap_config, imap_policy, folder_name = self._resolve_context(
            "delete_message", account, folder
        )
        if not imap_policy.allow_delete:
            raise ValueError(f"delete_message is not allowed for account: {account}")

        uid = self._normalize_message_uid(message_id)
        self._make_client(imap_config).delete_message(
            folder=folder_name,
            uid=uid,
        )
        return {
            "ok": True,
            "account": account,
            "folder": folder_name,
            "message_id": uid,
        }

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None:
        imap_config = self._resolve_account_config("append_sent_message", account)
        folder_name = self._resolve_folder(
            "append_sent_message",
            imap_config,
            folder,
        )
        self._make_client(imap_config).append_message(
            folder=folder_name,
            message_bytes=message_bytes,
            flags=(r"\Seen",),
        )

    def _resolve_context(
        self,
        tool_name: str,
        account_name: str,
        folder: str | None,
    ) -> tuple[IMAPConfig, IMAPAccessPolicyConfig, str]:
        imap_config = self._resolve_account_config(tool_name, account_name)
        folder_name = self._resolve_optional_folder(tool_name, imap_config, folder)
        imap_policy = self._policies.get(imap_config.policy)
        if imap_policy is None:
            raise ValueError(
                f"{tool_name} account references an unknown IMAP policy: {account_name}"
            )
        return imap_config, imap_policy, folder_name

    def _resolve_account_config(self, tool_name: str, account_name: str) -> IMAPConfig:
        imap_config = self._accounts.get(account_name)
        if imap_config is None:
            raise ValueError(
                f"{tool_name} requires an IMAP-enabled account: {account_name}"
            )
        return imap_config

    def _validate_policy_references(self) -> None:
        for account_name, imap_config in sorted(self._accounts.items()):
            if imap_config.policy not in self._policies:
                raise ValueError(
                    "IMAP account references an unknown policy: "
                    f"{account_name} -> {imap_config.policy}"
                )

    def _resolve_optional_folder(
        self,
        tool_name: str,
        imap_config: IMAPConfig,
        folder: str | None,
    ) -> str:
        folder_name = folder.strip() if folder else imap_config.default_folder
        if not folder_name:
            raise ValueError(
                f"{tool_name} requires folder when the account has no default_folder"
            )
        return self._resolve_folder(tool_name, imap_config, folder_name)

    def _resolve_folder(
        self,
        tool_name: str,
        imap_config: IMAPConfig,
        folder: str,
    ) -> str:
        folder_name = folder.strip()
        if not folder_name:
            raise ValueError(f"{tool_name} requires a non-empty folder")
        if folder_name not in imap_config.folders:
            raise ValueError(f"{tool_name} received an unconfigured folder: {folder}")
        return folder_name

    def _make_client(self, imap_config: IMAPConfig) -> IMAPClientProtocol:
        if self._imap_client_factory is None:
            raise RuntimeError("IMAP client factory is not configured")
        return self._imap_client_factory(imap_config)

    def _test_folders(self, imap_config: IMAPConfig) -> list[str]:
        folders = sorted(imap_config.folders)
        if imap_config.default_folder and imap_config.default_folder not in folders:
            folders.append(imap_config.default_folder)
        return folders

    def _message_summary(
        self,
        imap_policy: IMAPAccessPolicyConfig,
    ) -> dict[str, object]:
        flags = self._flag_summary(imap_policy)
        return {
            "read_allowed": imap_policy.allow_read,
            "move_allowed": imap_policy.allow_move,
            "delete_allowed": imap_policy.allow_delete,
            "flags": flags,
        }

    def _flag_summary(
        self,
        imap_policy: IMAPAccessPolicyConfig,
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
            if mode is not IMAPFlagMode.hidden
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

    def _normalize_folder_limit(self, limit: int) -> int:
        if limit < 1:
            raise ValueError("IMAP folder limit must be at least 1")
        if limit > 100:
            raise ValueError("IMAP folder limit must be at most 100")
        return limit

    def _normalize_folder_root(self, root: str | None) -> str | None:
        if root is None:
            return None
        normalized = root.strip().strip("/")
        return normalized or None

    def _folder_items(
        self,
        imap_config: IMAPConfig,
        *,
        root: str | None,
        recursive: bool,
        limit: int,
        query: str | None = None,
    ) -> _FolderItems:
        items: list[dict[str, object]] = []
        for folder_name, folder_config in sorted(imap_config.folders.items()):
            if not self._folder_matches_root(folder_name, root, recursive=recursive):
                continue
            item = self._folder_to_dict(
                folder_name,
                folder_config,
                default_folder=imap_config.default_folder,
            )
            if query is not None and not self._folder_matches_query(item, query):
                continue
            items.append(item)
            if len(items) > limit:
                return _FolderItems(items=items[:limit], truncated=True)
        return _FolderItems(items=items, truncated=False)

    def _folder_matches_root(
        self,
        folder_name: str,
        root: str | None,
        *,
        recursive: bool,
    ) -> bool:
        if root is None:
            relative = folder_name
        elif folder_name == root:
            return False
        elif folder_name.startswith(f"{root}/"):
            relative = folder_name[len(root) + 1 :]
        else:
            return False
        if recursive:
            return True
        return "/" not in relative

    def _folder_to_dict(
        self,
        folder_name: str,
        folder_config: IMAPFolderConfig,
        *,
        default_folder: str | None,
    ) -> dict[str, object]:
        return {
            "name": folder_name,
            "description": folder_config.description,
            "kind": (
                folder_config.kind.value if folder_config.kind is not None else None
            ),
            "default": folder_name == default_folder,
        }

    def _folder_matches_query(self, folder: dict[str, object], query: str) -> bool:
        searchable = [
            cast(str, folder["name"]),
            cast(str, folder["description"]),
            cast(str | None, folder["kind"]) or "",
        ]
        return any(query in value.casefold() for value in searchable)

    def _normalize_message_uid(self, message_id: str) -> str:
        uid = message_id.strip()
        if not uid:
            raise ValueError("IMAP message_id must be non-empty")
        if not uid.isdigit():
            raise ValueError("IMAP message_id must be an IMAP UID")
        return uid

    def _normalize_attachment_id(self, attachment_id: str) -> str:
        normalized = attachment_id.strip()
        if not normalized:
            raise ValueError("IMAP attachment_id must be non-empty")
        return normalized

    def _message_to_dict(
        self,
        message: FetchedIMAPMessage,
        imap_policy: IMAPAccessPolicyConfig,
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
            "flags": self._visible_flags(imap_policy, message.flags),
        }
        if message.rfc822_message_id:
            message_dict["rfc822_message_id"] = message.rfc822_message_id
        if message.snippet:
            message_dict["snippet"] = message.snippet
        if include_body:
            message_dict["text_body"] = message.text_body
            message_dict["html_body"] = message.html_body
            message_dict["attachments"] = [
                {
                    "id": attachment.id,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                    "disposition": attachment.disposition,
                    "content_id": attachment.content_id,
                    "inline": attachment.inline,
                }
                for attachment in message.attachments
            ]
        return message_dict

    def _visible_flags(
        self,
        imap_policy: IMAPAccessPolicyConfig,
        flags: list[str],
    ) -> list[str]:
        visible_flags: list[str] = []
        for flag in flags:
            mode = resolve_imap_flag_mode(imap_policy, flag)
            if mode is IMAPFlagMode.hidden:
                continue
            visible_flags.append(resolve_system_flag_key(flag) or flag)
        return visible_flags


def _imap_account_bootstrap_template(
    *,
    name: str,
    policy_name: str,
    env_suffix: str,
) -> str:
    return f"""# @package arbiter.account.imap.{name}
defaults:
  # Extend the plugin-owned structured schema, then override values below.
  - schema@_here_
  - _self_

# Human-facing summary shown by account listing tools.
description: IMAP account for (${{.username}})

# Operator guidance shown to agents during discovery.
guidance: ""

# Matching policy generated alongside this account.
policy: {policy_name}

# IMAP mailbox endpoint.
host: imap.example.com
port: 993

# Credentials are read from the Arbiter process environment.
username: ${{oc.env:IMAP_{env_suffix}_USERNAME}}
password: ${{oc.env:IMAP_{env_suffix}_PASSWORD}}

# TLS mode: implicit, starttls, or none.
tls: implicit
verify_peer: true
timeout_seconds: 30

# Default mailbox folder for tools that accept an optional folder.
default_folder: INBOX
folders:
  INBOX:
    description: Primary inbox.
    # Optional folder kind: all, archive, drafts, flagged, junk, sent, or trash.
    # These map to IMAP special-use mailbox attributes.
    kind:
"""


def _imap_policy_bootstrap_template(*, name: str) -> str:
    return f"""# @package arbiter.policy.imap.{name}
defaults:
  # Extend the plugin-owned structured schema, then override values below.
  - schema@_here_
  - _self_

# Read/search are enabled by default; mutating mailbox actions are disabled.
allow_read: true
allow_search: true
allow_move: false
allow_delete: false
confirmation_required: []

# System flags remain visible but read-only unless deliberately opened up.
system_flags:
  seen: read_only
  flagged: read_only
  answered: read_only
  deleted: read_only
  draft: read_only
user_flags: {{}}
"""


class IMAPServicePlugin:
    name = "imap"
    version = distribution_version("arbiter-imap", package_file=__file__)
    server_api_version = SERVER_API_VERSION

    def register_configs(self, config_store: ConfigStore) -> None:
        register_imap_configs(config_store)

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
        if kind == "account":
            env_suffix = name.upper().replace("-", "_")
            if not env_suffix.endswith("_ACCOUNT"):
                env_suffix = f"{env_suffix}_ACCOUNT"
            return _imap_account_bootstrap_template(
                name=name,
                policy_name=f"{name}_policy",
                env_suffix=env_suffix,
            )
        if kind == "policy":
            return _imap_policy_bootstrap_template(name=name)
        return None

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> object:
        from .client import IMAPClient

        imap_client_factory = cast(
            IMAPClientFactory,
            context.dependencies.get("imap_client_factory", IMAPClient),
        )
        artifact_store = cast(
            PluginArtifactStore | None,
            context.dependencies.get("artifact_store"),
        )
        return IMAPRuntime(
            accounts=accounts,
            policies=policies,
            imap_client_factory=imap_client_factory,
            artifact_store=artifact_store,
        )

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name=self.name,
            description="Read and manage mail through configured IMAP accounts.",
        )

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> tuple[OperationDescriptor, ...]:
        runtime = context.runtimes.require(self.name, IMAPRuntime)
        if runtime.artifact_delivery_available():
            return IMAP_OPERATION_DESCRIPTORS
        return tuple(
            descriptor
            for descriptor in IMAP_OPERATION_DESCRIPTORS
            if descriptor.name != "get_attachment"
        )

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
        context: ServicePluginContext,
    ) -> object:
        runtime = context.runtimes.require(self.name, IMAPRuntime)
        if operation == "list_messages":
            return runtime.list_messages(
                account=cast(str, arguments.get("account")),
                folder=cast(str | None, arguments.get("folder")),
                limit=cast(int, arguments.get("limit", 20)),
            )
        if operation == "list_folders":
            return runtime.list_folders(
                account=cast(str, arguments.get("account")),
                root=cast(str | None, arguments.get("root")),
                recursive=cast(bool, arguments.get("recursive", False)),
                limit=cast(int, arguments.get("limit", 50)),
            )
        if operation == "get_message":
            return runtime.get_message(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                folder=cast(str | None, arguments.get("folder")),
            )
        if operation == "get_attachment":
            return runtime.get_attachment(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                attachment_id=cast(str, arguments.get("attachment_id")),
                folder=cast(str | None, arguments.get("folder")),
            )
        if operation == "search_messages":
            return runtime.search_messages(
                account=cast(str, arguments.get("account")),
                query=cast(str, arguments.get("query")),
                folder=cast(str | None, arguments.get("folder")),
                limit=cast(int, arguments.get("limit", 20)),
            )
        if operation == "search_folders":
            return runtime.search_folders(
                account=cast(str, arguments.get("account")),
                query=cast(str, arguments.get("query")),
                root=cast(str | None, arguments.get("root")),
                recursive=cast(bool, arguments.get("recursive", True)),
                limit=cast(int, arguments.get("limit", 20)),
            )
        if operation == "move_message":
            return runtime.move_message(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                destination_folder=cast(str, arguments.get("destination_folder")),
                folder=cast(str | None, arguments.get("folder")),
            )
        if operation == "mark_message_read":
            return runtime.mark_message_read(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                folder=cast(str | None, arguments.get("folder")),
                read=cast(bool, arguments.get("read", True)),
            )
        if operation == "delete_message":
            return runtime.delete_message(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                folder=cast(str | None, arguments.get("folder")),
            )
        raise ValueError(f"unknown IMAP operation: {operation}")


def plugin() -> IMAPServicePlugin:
    return IMAPServicePlugin()
