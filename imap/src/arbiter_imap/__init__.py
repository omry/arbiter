from __future__ import annotations

from collections.abc import Mapping
from typing import Callable, Protocol, cast

from hydra.core.config_store import ConfigStore

from arbiter_core.services import (
    CapabilityDescriptor,
    OperationDescriptor,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_core.version import distribution_version

from .config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    register_configs as register_imap_configs,
    resolve_imap_flag_mode,
    resolve_system_flag_key,
)

from .client import FetchedIMAPMessage

CORE_API_VERSION = "0.9"


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
MESSAGE_ID_PROPERTY = {
    "type": "string",
    "description": "IMAP UID scoped to the selected account and folder.",
}
LIMIT_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": "Maximum number of messages to return.",
}

IMAP_OPERATION_DESCRIPTORS = (
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


class IMAPRuntime:
    service_name = "imap"

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


class IMAPServicePlugin:
    name = "imap"
    version = distribution_version("arbiter-imap", package_file=__file__)
    core_api_version = CORE_API_VERSION

    def register_configs(self, config_store: ConfigStore) -> None:
        register_imap_configs(config_store)

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
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
        return IMAPRuntime(
            accounts=accounts,
            policies=policies,
            imap_client_factory=imap_client_factory,
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
        return IMAP_OPERATION_DESCRIPTORS

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
        if operation == "get_message":
            return runtime.get_message(
                account=cast(str, arguments.get("account")),
                message_id=cast(str, arguments.get("message_id")),
                folder=cast(str | None, arguments.get("folder")),
            )
        if operation == "search_messages":
            return runtime.search_messages(
                account=cast(str, arguments.get("account")),
                query=cast(str, arguments.get("query")),
                folder=cast(str | None, arguments.get("folder")),
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
