from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Callable, Protocol, cast

from pydantic import Field

from hydra.core.config_store import ConfigStore

from agent_arbiter.services import (
    ServicePluginContext,
    ServiceRuntimeContext,
    ToolServer,
)

from .config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    register_configs as register_imap_configs,
    resolve_imap_flag_mode,
    resolve_system_flag_key,
)

from .client import FetchedIMAPMessage


class IMAPClientProtocol(Protocol):
    def list_messages(self, *, folder: str, limit: int) -> list[FetchedIMAPMessage]: ...

    def get_message(self, *, folder: str, uid: str) -> FetchedIMAPMessage: ...

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


IMAPClientFactory = Callable[[IMAPConfig], IMAPClientProtocol]


class IMAPRuntime:
    service_name = "imap"
    tool_names = (
        "list_messages",
        "get_message",
        "search_messages",
        "move_message",
        "mark_message_read",
        "delete_message",
    )

    def __init__(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        imap_client_factory: IMAPClientFactory | None = None,
    ) -> None:
        self._accounts = cast(Mapping[str, IMAPConfig], accounts)
        self._policies = cast(
            Mapping[str, IMAPAccessPolicyConfig],
            policies,
        )
        self._imap_client_factory = imap_client_factory
        self._validate_policy_references()

    def account_summaries(self) -> dict[str, object]:
        summaries: dict[str, object] = {}
        for account_name, account in sorted(self._accounts.items()):
            imap_policy = self._policies[account.policy]
            summaries[account_name] = {
                "description": account.description,
                "policy": account.policy,
                "enabled": True,
                "confirmation_required": [
                    action.value for action in imap_policy.confirmation_required
                ],
                "message": self._message_summary(imap_policy),
            }
        return summaries

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

    def _resolve_context(
        self,
        tool_name: str,
        account_name: str,
        folder: str | None,
    ) -> tuple[IMAPConfig, IMAPAccessPolicyConfig, str]:
        imap_config = self._accounts.get(account_name)
        if imap_config is None:
            raise ValueError(
                f"{tool_name} requires an IMAP-enabled account: {account_name}"
            )

        folder_name = self._resolve_optional_folder(tool_name, imap_config, folder)
        imap_policy = self._policies.get(imap_config.policy)
        if imap_policy is None:
            raise ValueError(
                f"{tool_name} account references an unknown IMAP policy: {account_name}"
            )
        return imap_config, imap_policy, folder_name

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

    def _normalize_message_uid(self, message_id: str) -> str:
        uid = message_id.strip()
        if not uid:
            raise ValueError("IMAP message_id must be non-empty")
        if not uid.isdigit():
            raise ValueError("IMAP message_id must be an IMAP UID")
        return uid

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


IMAPAccountName = Annotated[
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

IMAPMessageId = Annotated[
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

IMAPSearchQuery = Annotated[
    str,
    Field(
        description="Text query used with IMAP TEXT search in the selected folder.",
        examples=["invoice"],
        min_length=1,
    ),
]

IMAPMessageLimit = Annotated[
    int,
    Field(
        description="Maximum number of messages to return.",
        ge=1,
        le=100,
        examples=[20],
    ),
]


class IMAPServicePlugin:
    name = "imap"

    def register_configs(self, config_store: ConfigStore) -> None:
        register_imap_configs(config_store)

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
        return IMAPRuntime(
            accounts=accounts,
            policies=policies,
            imap_client_factory=imap_client_factory,
        )

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None:
        runtime = context.runtimes.require(self.name, IMAPRuntime)

        @server.tool(
            description=(
                "List recent messages from a configured IMAP folder on the selected "
                "account. Message ids are IMAP UIDs scoped to that account and folder."
            )
        )
        def list_messages(
            account: IMAPAccountName,
            folder: OptionalFolderName = None,
            limit: IMAPMessageLimit = 20,
        ) -> dict[str, object]:
            return runtime.list_messages(account=account, folder=folder, limit=limit)

        @server.tool(
            description=(
                "Fetch one message by IMAP UID from a configured folder on the selected "
                "account, including plain text and HTML bodies when present."
            )
        )
        def get_message(
            account: IMAPAccountName,
            message_id: IMAPMessageId,
            folder: OptionalFolderName = None,
        ) -> dict[str, object]:
            return runtime.get_message(
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
            account: IMAPAccountName,
            query: IMAPSearchQuery,
            folder: OptionalFolderName = None,
            limit: IMAPMessageLimit = 20,
        ) -> dict[str, object]:
            return runtime.search_messages(
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
            account: IMAPAccountName,
            message_id: IMAPMessageId,
            destination_folder: FolderName,
            folder: OptionalFolderName = None,
        ) -> dict[str, object]:
            return runtime.move_message(
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
            account: IMAPAccountName,
            message_id: IMAPMessageId,
            folder: OptionalFolderName = None,
            read: bool = True,
        ) -> dict[str, object]:
            return runtime.mark_message_read(
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
            account: IMAPAccountName,
            message_id: IMAPMessageId,
            folder: OptionalFolderName = None,
        ) -> dict[str, object]:
            return runtime.delete_message(
                account=account,
                message_id=message_id,
                folder=folder,
            )


def plugin() -> IMAPServicePlugin:
    return IMAPServicePlugin()
