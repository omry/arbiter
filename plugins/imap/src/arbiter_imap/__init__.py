from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable, Protocol, cast

from hydra.core.config_store import ConfigStore

from arbiter_server.artifacts import PluginArtifactStore
from arbiter_server.services import (
    CapabilityDescriptor,
    ConfigCheckError,
    ConfigCheckIssue,
    ConfigCheckWarning,
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
    IMAPFolderKind,
    IMAPOperationDecision,
    IMAPSystemFlag,
    normalize_imap_flag_name,
    register_configs as register_imap_configs,
    resolve_system_flag,
    validate_user_flag_name,
)
from .policy import (
    IMAPFolderResolution,
    IMAPPolicyResolver,
    IMAPResolvedFolderPolicy,
    folder_metadata_matches,
    validate_imap_policy,
)
from .runtime_policy import _IMAPRuntimePolicyMixin, _PreparedAppend

from .client import FetchedIMAPMessage, IMAPAttachmentContent
from .operations import IMAP_OPERATION_DESCRIPTORS

SERVER_API_VERSION = "0.9"
IMAP_DRAFT_SYSTEM_FLAGS = (IMAPSystemFlag.DRAFT, IMAPSystemFlag.SEEN)
IMAP_DRAFT_FLAGS = (IMAPSystemFlag.DRAFT.name, IMAPSystemFlag.SEEN.name)


class IMAPClientProtocol(Protocol):
    def test_connection(self, *, folders: Sequence[str]) -> None: ...

    def list_folders(self) -> list[str]: ...

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

    def get_message_flags(self, *, folder: str, uid: str) -> list[str]: ...

    def update_message_flags(
        self,
        *,
        folder: str,
        uid: str,
        add_flags: Sequence[str],
        remove_flags: Sequence[str],
    ) -> None: ...

    def delete_message(self, *, folder: str, uid: str) -> None: ...

    def append_message(
        self,
        *,
        folder: str,
        message_bytes: bytes,
        flags: Sequence[str] = (IMAPSystemFlag.SEEN.value,),
    ) -> None: ...


IMAPClientFactory = Callable[[IMAPConfig], IMAPClientProtocol]


def _format_exception_message(exc: BaseException) -> str:
    if len(exc.args) == 1 and isinstance(exc.args[0], bytes):
        return exc.args[0].decode("utf-8", errors="replace")
    return str(exc)


def _imap_live_auth_error(imap_config: IMAPConfig) -> str | None:
    has_username = bool(imap_config.username.strip())
    has_password = bool(imap_config.password)
    if has_username and not has_password:
        return "IMAP account missing password for live authentication check"
    if has_password and not has_username:
        return "IMAP account missing username for live authentication check"
    return None


def _accessible_trash_folders(
    resolver: IMAPPolicyResolver,
    folder_names: Sequence[str],
) -> list[IMAPFolderResolution]:
    trash_folders: list[IMAPFolderResolution] = []
    for folder_name in sorted(set(folder_names)):
        folder = resolver.resolve_folder(folder_name)
        if folder.metadata.kind is not IMAPFolderKind.TRASH:
            continue
        if folder.access.allowed:
            trash_folders.append(folder)
    return trash_folders


def _delete_allowed_folders(
    resolver: IMAPPolicyResolver,
    folder_names: Sequence[str],
) -> list[IMAPFolderResolution]:
    delete_folders: list[IMAPFolderResolution] = []
    for folder_name in sorted(set(folder_names)):
        folder = resolver.resolve_folder(folder_name)
        if not folder.access.allowed:
            continue
        if folder.policy.delete == IMAPOperationDecision.allow:
            delete_folders.append(folder)
    return delete_folders


def _delete_may_require_trash(
    resolver: IMAPPolicyResolver,
    folder_names: Sequence[str],
    policy: IMAPAccessPolicyConfig,
) -> bool:
    if policy.operation_defaults.delete == IMAPOperationDecision.allow:
        return True
    if any(
        folder_policy.delete == IMAPOperationDecision.allow
        for folder_policy in policy.folders.values()
    ):
        return True
    return bool(_delete_allowed_folders(resolver, folder_names))


def _trash_required_message(account: str) -> str:
    return (
        "delete_message requires an accessible TRASH folder for account: " f"{account}"
    )


def _draft_folder_names(imap_config: IMAPConfig) -> list[str]:
    return [
        folder_name
        for folder_name, folder_config in imap_config.folders.items()
        if folder_config.kind is IMAPFolderKind.DRAFTS
    ]


def _preferred_draft_folder_name(folder_names: Sequence[str]) -> str:
    return sorted(
        folder_names,
        key=lambda name: (name.lower() != "drafts", name.lower()),
    )[0]


def _save_draft_folder_required_message(account: str, folder: str) -> str:
    return (
        "save_draft requires configured DRAFTS folder to exist for IMAP account: "
        f"{account}: {folder}"
    )


_OperationArgumentSpec = str | tuple[str, object]


IMAP_OPERATION_ARGUMENTS: Mapping[str, tuple[_OperationArgumentSpec, ...]] = {
    "list_messages": ("account", "folder", ("limit", 20)),
    "list_folders": ("account", "root", ("recursive", False), ("limit", 50)),
    "get_message": ("account", "message_id", "folder"),
    "get_attachment": ("account", "message_id", "attachment_id", "folder"),
    "search_messages": ("account", "query", "folder", ("limit", 20)),
    "search_folders": ("account", "query", "root", ("recursive", True), ("limit", 20)),
    "move_message": ("account", "message_id", "destination_folder", "folder"),
    "mark_message_read": ("account", "message_id", "folder", ("read", True)),
    "get_message_flags": ("account", "message_id", "folder"),
    "update_message_flags": (
        "account",
        "message_id",
        "folder",
        ("add_flags", ()),
        ("remove_flags", ()),
    ),
    "append_message": (
        "account",
        "folder",
        "message",
        ("flags", (IMAPSystemFlag.SEEN.name,)),
    ),
    "save_draft": ("account", "message", "folder"),
    "delete_message": ("account", "message_id", "folder", ("permanent", False)),
}


def _operation_arguments(
    operation: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    try:
        argument_specs = IMAP_OPERATION_ARGUMENTS[operation]
    except KeyError as exc:
        raise ValueError(f"unknown IMAP operation: {operation}") from exc
    runtime_arguments: dict[str, object] = {}
    for spec in argument_specs:
        argument_name, default = spec if isinstance(spec, tuple) else (spec, None)
        runtime_arguments[argument_name] = arguments.get(argument_name, default)
    return runtime_arguments


@dataclass(frozen=True)
class _FolderItems:
    items: list[dict[str, object]]
    truncated: bool


class IMAPRuntime(_IMAPRuntimePolicyMixin):
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
                "message": self._message_summary(imap_policy),
            }
        return summaries

    def test_accounts(
        self,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        results: dict[str, object] = {}
        for account_name, imap_config in sorted(self._accounts.items()):
            if progress is not None:
                progress(account_name)
            if auth_error := _imap_live_auth_error(imap_config):
                results[account_name] = {
                    "status": "failed",
                    "stage": "configuration",
                    "error_type": "ValueError",
                    "message": auth_error,
                }
                continue
            client = self._make_client(imap_config)
            try:
                server_folders = client.list_folders()
                folders = self._test_folders(account_name, imap_config, server_folders)
                client.test_connection(folders=folders)
                trash_checked = self._test_delete_trash_destination(
                    account_name,
                    imap_config,
                    server_folders,
                )
                (
                    save_draft_checked,
                    save_draft_warning,
                ) = self._test_save_draft_destination(
                    account_name,
                    imap_config,
                    server_folders,
                )
            except Exception as exc:
                results[account_name] = {
                    "status": "failed",
                    "stage": "connect_auth_noop_examine",
                    "error_type": type(exc).__name__,
                    "message": _format_exception_message(exc),
                }
                continue
            if not folders:
                results[account_name] = {
                    "status": "skipped",
                    "stage": "connect_auth_noop",
                    "checks": ["connect", "noop"],
                    "reason": "no accessible IMAP folders to examine read-only",
                }
                continue
            if save_draft_warning is not None:
                results[account_name] = {
                    "status": "warning",
                    "stage": "connect_auth_noop_examine",
                    "checks": [
                        "connect",
                        "noop",
                        "examine",
                        *(["trash_destination"] if trash_checked else []),
                    ],
                    "folders": folders,
                    "message": save_draft_warning,
                }
                continue
            results[account_name] = {
                "status": "ok",
                "stage": "connect_auth_noop_examine",
                "checks": [
                    "connect",
                    "noop",
                    "examine",
                    *(["trash_destination"] if trash_checked else []),
                    *(["save_draft_destination"] if save_draft_checked else []),
                ],
                "folders": folders,
            }
        return results

    def list_messages(
        self,
        account: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        imap_config, folder_resolution = self._resolve_context(
            "list_messages", account, folder
        )
        self._require_accessible(account, "list_messages", folder_resolution)
        self._require_operation(
            account,
            "list_messages",
            folder_resolution,
            folder_resolution.policy.read,
        )

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_client(imap_config).list_messages(
            folder=folder_resolution.name,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_resolution.name,
            "messages": [
                self._message_to_dict(
                    message, folder_resolution.policy, include_body=False
                )
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
        resolver = self._resolver(imap_config)
        normalized_root = self._normalize_folder_root(root)
        normalized_limit = self._normalize_folder_limit(limit)
        folder_items = self._folder_items(
            account,
            imap_config,
            resolver,
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
        imap_config, folder_resolution = self._resolve_context(
            "get_message", account, folder
        )
        self._require_accessible(account, "get_message", folder_resolution)
        self._require_operation(
            account, "get_message", folder_resolution, folder_resolution.policy.read
        )

        uid = self._normalize_message_uid(message_id)
        message = self._make_client(imap_config).get_message(
            folder=folder_resolution.name,
            uid=uid,
        )
        return {
            "account": account,
            "folder": folder_resolution.name,
            "message": self._message_to_dict(
                message,
                folder_resolution.policy,
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
        imap_config, folder_resolution = self._resolve_context(
            "get_attachment", account, folder
        )
        self._require_accessible(account, "get_attachment", folder_resolution)
        self._require_operation(
            account,
            "get_attachment",
            folder_resolution,
            folder_resolution.policy.read,
        )
        if self._artifact_store is None:
            raise ValueError(
                "get_attachment requires server artifact storage; "
                "HTTP artifact delivery is unavailable"
            )

        uid = self._normalize_message_uid(message_id)
        normalized_attachment_id = self._normalize_attachment_id(attachment_id)
        attachment_content = self._make_client(imap_config).get_attachment(
            folder=folder_resolution.name,
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
                "folder": folder_resolution.name,
                "message_id": uid,
                "attachment_id": attachment.id,
            },
        )
        return {
            "account": account,
            "folder": folder_resolution.name,
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
        imap_config, folder_resolution = self._resolve_context(
            "search_messages", account, folder
        )
        self._require_accessible(account, "search_messages", folder_resolution)
        self._require_operation(
            account,
            "search_messages",
            folder_resolution,
            folder_resolution.policy.search,
        )

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_messages requires a non-empty query")

        normalized_limit = self._normalize_limit(limit)
        messages = self._make_client(imap_config).search_messages(
            folder=folder_resolution.name,
            query=normalized_query,
            limit=normalized_limit,
        )
        return {
            "account": account,
            "folder": folder_resolution.name,
            "query": normalized_query,
            "messages": [
                self._message_to_dict(
                    message, folder_resolution.policy, include_body=False
                )
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
        resolver = self._resolver(imap_config)
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_folders requires a non-empty query")
        normalized_root = self._normalize_folder_root(root)
        normalized_limit = self._normalize_folder_limit(limit)
        query_text = normalized_query.casefold()
        folder_items = self._folder_items(
            account,
            imap_config,
            resolver,
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
        prepared = self._prepare_move_message(
            account=account,
            message_id=message_id,
            destination_folder=destination_folder,
            folder=folder,
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).move_message(
            source_folder=prepared.source.name,
            uid=prepared.uid,
            destination_folder=prepared.destination.name,
        )
        return {
            "ok": True,
            "account": account,
            "source_folder": prepared.source.name,
            "destination_folder": prepared.destination.name,
            "message_id": prepared.uid,
        }

    def mark_message_read(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
        read: bool = True,
    ) -> dict[str, object]:
        prepared = self._prepare_mark_message_read(
            account=account,
            message_id=message_id,
            folder=folder,
            read=read,
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).mark_message_read(
            folder=prepared.folder.name,
            uid=prepared.uid,
            read=prepared.read,
        )
        return {
            "ok": True,
            "account": account,
            "folder": prepared.folder.name,
            "message_id": prepared.uid,
            "read": prepared.read,
        }

    def get_message_flags(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        imap_config, folder_resolution = self._resolve_context(
            "get_message_flags", account, folder
        )
        self._require_accessible(account, "get_message_flags", folder_resolution)
        self._require_operation(
            account,
            "get_message_flags",
            folder_resolution,
            folder_resolution.policy.read,
            operation_label="read",
        )

        uid = self._normalize_message_uid(message_id)
        flags = self._make_client(imap_config).get_message_flags(
            folder=folder_resolution.name,
            uid=uid,
        )
        return {
            "account": account,
            "folder": folder_resolution.name,
            "message_id": uid,
            "flags": self._visible_flags(folder_resolution.policy, flags),
        }

    def update_message_flags(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
        add_flags: Sequence[str] = (),
        remove_flags: Sequence[str] = (),
    ) -> dict[str, object]:
        prepared = self._prepare_update_message_flags(
            account=account,
            message_id=message_id,
            folder=folder,
            add_flags=add_flags,
            remove_flags=remove_flags,
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).update_message_flags(
            folder=prepared.folder.name,
            uid=prepared.uid,
            add_flags=prepared.add_flags,
            remove_flags=prepared.remove_flags,
        )
        return {
            "ok": True,
            "account": account,
            "folder": prepared.folder.name,
            "message_id": prepared.uid,
            "add_flags": list(prepared.add_flags),
            "remove_flags": list(prepared.remove_flags),
        }

    def check_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        operation_id = f"imap:{operation}"
        account = cast(str, arguments.get("account"))
        try:
            if operation == "move_message":
                return self._check_move_message(operation_id, account, arguments)
            if operation == "mark_message_read":
                prepared = self._prepare_mark_message_read(
                    account=account,
                    message_id=cast(str, arguments.get("message_id")),
                    folder=cast(str | None, arguments.get("folder")),
                    read=cast(bool, arguments.get("read", True)),
                )
                return self._decision_check_result(operation_id, prepared.decision)
            if operation == "delete_message":
                prepared = self._prepare_delete_message(
                    account=account,
                    message_id=cast(str, arguments.get("message_id")),
                    folder=cast(str | None, arguments.get("folder")),
                    permanent=cast(bool, arguments.get("permanent", False)),
                )
                return self._decision_check_result(operation_id, prepared.decision)
            if operation == "update_message_flags":
                return self._check_update_message_flags(
                    operation_id, account, arguments
                )
            if operation == "append_message":
                return self._check_append_message(operation_id, account, arguments)
            if operation == "save_draft":
                return self._check_save_draft(operation_id, account, arguments)
            folder = cast(str | None, arguments.get("folder"))
            imap_config, folder_resolution = self._resolve_context(
                operation, account, folder
            )
            del imap_config
            decision = self._folder_operation_decision(
                account,
                operation,
                folder_resolution,
                self._operation_decision_for_check(operation, folder_resolution),
                access_why_not="folder is not accessible for account",
            ).evaluate()
            result = self._decision_check_result(operation_id, decision)
            if decision.allowed:
                return result
            if decision.failed_gate == "folder_access":
                result["access_rules"] = self._access_rules_for_output(
                    folder_resolution
                )
            return result
        except Exception as exc:
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": str(exc),
            }

    def delete_message(
        self,
        account: str,
        message_id: str,
        folder: str | None = None,
        permanent: bool = False,
    ) -> dict[str, object]:
        prepared = self._prepare_delete_message(
            account=account,
            message_id=message_id,
            folder=folder,
            permanent=permanent,
        )
        prepared.decision.require_allowed()
        if prepared.permanent:
            self._make_client(prepared.imap_config).delete_message(
                folder=prepared.folder.name,
                uid=prepared.uid,
            )
            destination_folder = None
        else:
            destination = self._resolve_trash_destination(account, prepared.imap_config)
            self._make_client(prepared.imap_config).move_message(
                source_folder=prepared.folder.name,
                uid=prepared.uid,
                destination_folder=destination.name,
            )
            destination_folder = destination.name
        return {
            "ok": True,
            "account": account,
            "folder": prepared.folder.name,
            "message_id": prepared.uid,
            "permanent": prepared.permanent,
            "destination_folder": destination_folder,
        }

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None:
        prepared = self._prepare_append_message(
            account,
            folder=folder,
            message_bytes=message_bytes,
            flags=(IMAPSystemFlag.SEEN.name,),
            tool_name="append_sent_message",
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).append_message(
            folder=prepared.folder.name,
            message_bytes=cast(bytes, prepared.message_bytes),
            flags=prepared.flags,
        )

    def append_message(
        self,
        *,
        account: str,
        message: str,
        folder: str | None = None,
        flags: Sequence[str] = (IMAPSystemFlag.SEEN.name,),
    ) -> dict[str, object]:
        prepared = self._prepare_append_message(
            account,
            folder=folder,
            message=message,
            flags=flags,
            tool_name="append_message",
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).append_message(
            folder=prepared.folder.name,
            message_bytes=cast(bytes, prepared.message_bytes),
            flags=prepared.flags,
        )
        return {
            "ok": True,
            "account": account,
            "folder": prepared.folder.name,
            "flags": list(prepared.flags),
        }

    def save_draft(
        self,
        *,
        account: str,
        message: str,
        folder: str | None = None,
    ) -> dict[str, object]:
        prepared = self._prepare_save_draft(
            account=account,
            message=message,
            folder=folder,
        )
        prepared.decision.require_allowed()
        self._make_client(prepared.imap_config).append_message(
            folder=prepared.folder.name,
            message_bytes=cast(bytes, prepared.message_bytes),
            flags=prepared.flags,
        )
        return {
            "ok": True,
            "account": account,
            "folder": prepared.folder.name,
            "flags": list(prepared.flags),
        }

    def _check_save_draft(
        self,
        operation_id: str,
        account: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            prepared = self._prepare_save_draft(
                account=account,
                message=cast(str | None, arguments.get("message")),
                folder=cast(str | None, arguments.get("folder")),
            )
            return self._decision_check_result(operation_id, prepared.decision)
        except Exception as exc:
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": str(exc),
            }

    def _prepare_save_draft(
        self,
        *,
        account: str,
        message: str | None,
        folder: str | None,
    ) -> _PreparedAppend:
        imap_config = self._resolve_account_config("save_draft", account)
        draft_folder = self._resolve_draft_folder(
            "save_draft",
            account,
            imap_config,
            folder,
        )
        return self._prepare_append_message(
            account,
            folder=draft_folder.name,
            message=message,
            flags=IMAP_DRAFT_FLAGS,
            tool_name="save_draft",
        )

    def _resolve_context(
        self,
        tool_name: str,
        account_name: str,
        folder: str | None,
    ) -> tuple[IMAPConfig, IMAPFolderResolution]:
        imap_config = self._resolve_account_config(tool_name, account_name)
        folder_resolution = self._resolve_optional_folder(
            tool_name, account_name, imap_config, folder
        )
        return imap_config, folder_resolution

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
            self._resolver(imap_config)

    def _resolve_optional_folder(
        self,
        tool_name: str,
        account_name: str,
        imap_config: IMAPConfig,
        folder: str | None,
    ) -> IMAPFolderResolution:
        folder_name = folder.strip() if folder else imap_config.default_folder
        if not folder_name:
            raise ValueError(
                f"{tool_name} requires folder when the account has no default_folder"
            )
        return self._resolve_named_folder(
            tool_name, account_name, imap_config, folder_name
        )

    def _resolve_named_folder(
        self,
        tool_name: str,
        account_name: str,
        imap_config: IMAPConfig,
        folder: str,
    ) -> IMAPFolderResolution:
        folder_name = folder.strip()
        if not folder_name:
            raise ValueError(f"{tool_name} requires a non-empty folder")
        return self._resolver(imap_config).resolve_folder(folder_name)

    def _resolve_draft_folder(
        self,
        tool_name: str,
        account_name: str,
        imap_config: IMAPConfig,
        folder: str | None,
    ) -> IMAPFolderResolution:
        if folder:
            return self._resolve_named_folder(
                tool_name,
                account_name,
                imap_config,
                folder,
            )
        draft_folder_names = [
            folder_name
            for folder_name, folder_config in imap_config.folders.items()
            if folder_config.kind == IMAPFolderKind.DRAFTS
        ]
        if not draft_folder_names:
            raise ValueError(
                f"{tool_name} requires folder or a configured DRAFTS folder "
                f"for account: {account_name}"
            )
        draft_folder_name = sorted(
            draft_folder_names,
            key=lambda name: (name.lower() != "drafts", name.lower()),
        )[0]
        return self._resolve_named_folder(
            tool_name,
            account_name,
            imap_config,
            draft_folder_name,
        )

    def _resolver(self, imap_config: IMAPConfig) -> IMAPPolicyResolver:
        imap_policy = self._policies.get(imap_config.policy)
        if imap_policy is None:
            raise ValueError(
                f"IMAP account references an unknown policy: {imap_config.policy}"
            )
        return IMAPPolicyResolver(
            folder_metadata=imap_config.folders,
            policy=imap_policy,
        )

    def _make_client(self, imap_config: IMAPConfig) -> IMAPClientProtocol:
        if self._imap_client_factory is None:
            raise RuntimeError("IMAP client factory is not configured")
        return self._imap_client_factory(imap_config)

    def _test_folders(
        self,
        account_name: str,
        imap_config: IMAPConfig,
        server_folders: Sequence[str],
    ) -> list[str]:
        resolver = self._resolver(imap_config)
        folder_set = set(server_folders)
        test_folders: set[str] = set()
        if imap_config.default_folder:
            if imap_config.default_folder not in folder_set:
                raise ValueError(
                    "default_folder does not exist for IMAP account: " f"{account_name}"
                )
            default_resolution = resolver.resolve_folder(imap_config.default_folder)
            if not default_resolution.access.allowed:
                raise ValueError(
                    "default_folder is not accessible for IMAP account: "
                    f"{account_name}"
                )
            test_folders.add(imap_config.default_folder)

        for folder_name in sorted(folder_set):
            if not folder_metadata_matches(imap_config.folders, folder_name):
                continue
            if resolver.resolve_folder(folder_name).access.allowed:
                test_folders.add(folder_name)

        return sorted(test_folders)

    def _test_delete_trash_destination(
        self,
        account: str,
        imap_config: IMAPConfig,
        server_folders: Sequence[str],
    ) -> bool:
        resolver = self._resolver(imap_config)
        delete_folders = _delete_allowed_folders(resolver, server_folders)
        if not delete_folders:
            return False
        if not _accessible_trash_folders(resolver, server_folders):
            raise ValueError(_trash_required_message(account))
        return True

    def _test_save_draft_destination(
        self,
        account: str,
        imap_config: IMAPConfig,
        server_folders: Sequence[str],
    ) -> tuple[bool, str | None]:
        draft_folder_names = _draft_folder_names(imap_config)
        if not draft_folder_names:
            return False, None
        draft_folder = _preferred_draft_folder_name(draft_folder_names)
        if draft_folder not in set(server_folders):
            return False, _save_draft_folder_required_message(account, draft_folder)
        return True, None

    def _resolve_trash_destination(
        self,
        account: str,
        imap_config: IMAPConfig,
    ) -> IMAPFolderResolution:
        resolver = self._resolver(imap_config)
        trash_folders = _accessible_trash_folders(
            resolver,
            self._make_client(imap_config).list_folders(),
        )
        if not trash_folders:
            raise ValueError(_trash_required_message(account))
        return sorted(trash_folders, key=lambda folder: folder.name)[0]

    def _message_summary(
        self,
        imap_policy: IMAPAccessPolicyConfig,
    ) -> dict[str, object]:
        return {
            "defaults": {
                "read": imap_policy.operation_defaults.read.value,
                "search": imap_policy.operation_defaults.search.value,
                "move": (
                    imap_policy.operation_defaults.move
                    if isinstance(imap_policy.operation_defaults.move, bool)
                    else {"allowed": imap_policy.operation_defaults.move.allowed}
                ),
                "mark_read": imap_policy.operation_defaults.mark_read.value,
                "delete": imap_policy.operation_defaults.delete.value,
                "folder_append": imap_policy.operation_defaults.folder_append.value,
                "flags": self._flag_summary(imap_policy),
            },
        }

    def _flag_summary(
        self,
        imap_policy: IMAPAccessPolicyConfig,
    ) -> dict[str, object]:
        system_flags = {
            IMAPSystemFlag.SEEN: imap_policy.operation_defaults.system_flags.SEEN,
            IMAPSystemFlag.FLAGGED: imap_policy.operation_defaults.system_flags.FLAGGED,
            IMAPSystemFlag.ANSWERED: imap_policy.operation_defaults.system_flags.ANSWERED,
            IMAPSystemFlag.DELETED: imap_policy.operation_defaults.system_flags.DELETED,
            IMAPSystemFlag.DRAFT: imap_policy.operation_defaults.system_flags.DRAFT,
        }
        flags: dict[str, object] = {
            flag_name.name: mode.value for flag_name, mode in system_flags.items()
        }

        user_flags = {
            flag_name: mode.value
            for flag_name, mode in sorted(
                imap_policy.operation_defaults.user_flags.items()
            )
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
        account: str,
        imap_config: IMAPConfig,
        resolver: IMAPPolicyResolver,
        *,
        root: str | None,
        recursive: bool,
        limit: int,
        query: str | None = None,
    ) -> _FolderItems:
        items: list[dict[str, object]] = []
        for folder_name in self._make_client(imap_config).list_folders():
            if not self._folder_matches_root(folder_name, root, recursive=recursive):
                continue
            folder_resolution = resolver.resolve_folder(folder_name)
            if not folder_resolution.access.allowed:
                continue
            item = self._folder_to_dict(
                folder_resolution,
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
        folder: IMAPFolderResolution,
        *,
        default_folder: str | None,
    ) -> dict[str, object]:
        return {
            "name": folder.name,
            "description": folder.metadata.description,
            "kind": (
                folder.metadata.kind.value if folder.metadata.kind is not None else None
            ),
            "default": folder.name == default_folder,
            "operations": folder.policy.operation_summary(),
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

    def _normalize_flags(self, flags: Sequence[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for flag in flags:
            stripped = flag.strip()
            if not stripped:
                raise ValueError("IMAP flag names must be non-empty")
            normalized_flag = normalize_imap_flag_name(stripped)
            if resolve_system_flag(normalized_flag) is None:
                validate_user_flag_name(normalized_flag)
            normalized.append(normalized_flag)
        return tuple(dict.fromkeys(normalized))

    def _message_to_dict(
        self,
        message: FetchedIMAPMessage,
        imap_policy: IMAPResolvedFolderPolicy,
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
host: ${{oc.env:IMAP_{env_suffix}_HOST}}
port: ${{oc.env:IMAP_{env_suffix}_PORT,993}}

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
    kind: INBOX
  Sent:
    description: Sent mail.
    kind: SENT
  Drafts:
    description: Draft messages.
    kind: DRAFTS
    # Optional folder kind: INBOX, ALL, ARCHIVE, DRAFTS, FLAGGED, JUNK, SENT, or TRASH.
    # These map to IMAP special-use mailbox attributes.
"""


def _imap_policy_bootstrap_template(*, name: str, variant: str = "default-open") -> str:
    if variant not in {"default-open", "default-closed"}:
        raise ValueError(f"unknown IMAP policy bootstrap variant: {variant}")
    access_rule = (
        '    - allow_glob: "*"' if variant == "default-open" else '    - deny_glob: "*"'
    )
    access_description = (
        "default-open variant exposes all server folders first, then lets you add deny rules below"
        if variant == "default-open"
        else "default-closed variant denies all server folders first, then lets you add allow rules below"
    )
    return f"""# @package arbiter.policy.imap.{name}
defaults:
  # Extend the plugin-owned structured schema, then override values below.
  - schema@_here_
  - _self_

# Explicit folder access baseline. This {access_description}.
folder_access:
  rules:
{access_rule}
operation_defaults:
  read: allow
  search: allow
  move: false
  mark_read: deny
  delete: deny
  folder_append: deny
  system_flags:
    SEEN: read_only
    FLAGGED: read_only
    ANSWERED: read_only
    DELETED: read_only
    DRAFT: read_only
  user_flags: {{}}

folders:
  Sent:
    folder_append: allow
    system_flags:
      SEEN: read_write
  Drafts:
    folder_append: allow
    system_flags:
      SEEN: read_write
      DRAFT: read_write
"""


class IMAPServicePlugin:
    name = "imap"
    version = distribution_version("arbiter-imap", package_file=__file__)
    server_api_version = SERVER_API_VERSION

    def register_configs(self, config_store: ConfigStore) -> None:
        register_imap_configs(config_store)

    def bootstrap_variants(self, *, kind: str) -> Mapping[str, str]:
        if kind != "policy":
            return {}
        return {
            "default-open": "allow all folders first, then add deny rules",
            "default-closed": "deny all folders first, then add allow rules",
        }

    def bootstrap_config(
        self,
        *,
        kind: str,
        name: str,
        variant: str | None = None,
    ) -> object | None:
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
            return _imap_policy_bootstrap_template(
                name=name,
                variant=variant or "default-open",
            )
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

    def check_config(
        self,
        *,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> tuple[ConfigCheckWarning, ...]:
        imap_accounts = cast(Mapping[str, IMAPConfig], accounts)
        imap_policies = cast(Mapping[str, IMAPAccessPolicyConfig], policies)
        errors: list[ConfigCheckIssue] = []
        warnings: list[ConfigCheckWarning] = []
        for policy_name, policy in sorted(imap_policies.items()):
            try:
                validate_imap_policy(policy)
            except Exception as exc:
                errors.append(ConfigCheckIssue(message=str(exc), policy=policy_name))
        if errors:
            raise ConfigCheckError(errors)
        for account_name, imap_config in sorted(imap_accounts.items()):
            imap_policy = imap_policies.get(imap_config.policy)
            if imap_policy is None:
                continue
            resolver = IMAPPolicyResolver(
                folder_metadata=imap_config.folders,
                policy=imap_policy,
            )
            source_folder_names: list[str] = []
            configured_folder_names: list[str] = []
            if imap_config.default_folder:
                try:
                    resolver.resolve_folder(imap_config.default_folder)
                except Exception as exc:
                    errors.append(
                        ConfigCheckIssue(
                            message=(
                                "IMAP default_folder is invalid: "
                                f"{imap_config.default_folder}: {exc}"
                            ),
                            account=account_name,
                            policy=imap_config.policy,
                        )
                    )
                    continue
                source_folder_names.append(imap_config.default_folder)
            accessible_configured_folders = 0
            for folder_name in imap_config.folders:
                try:
                    folder = resolver.resolve_folder(folder_name)
                except Exception as exc:
                    errors.append(
                        ConfigCheckIssue(
                            message=(
                                "IMAP configured folder is invalid: "
                                f"{folder_name}: {exc}"
                            ),
                            account=account_name,
                            policy=imap_config.policy,
                        )
                    )
                    continue
                configured_folder_names.append(folder_name)
                source_folder_names.append(folder_name)
                if folder.access.allowed:
                    accessible_configured_folders += 1
            if imap_config.folders and accessible_configured_folders == 0:
                warnings.append(
                    ConfigCheckWarning(
                        message="IMAP account has no accessible configured folders",
                        account=account_name,
                        policy=imap_config.policy,
                    )
                )
            warnings.extend(
                self._save_draft_config_warnings(
                    account_name=account_name,
                    imap_config=imap_config,
                    resolver=resolver,
                    resolved_configured_folders=configured_folder_names,
                )
            )
            if _delete_may_require_trash(
                resolver,
                source_folder_names,
                imap_policy,
            ):
                if not _accessible_trash_folders(resolver, configured_folder_names):
                    errors.append(
                        ConfigCheckIssue(
                            message=_trash_required_message(account_name),
                            account=account_name,
                            policy=imap_config.policy,
                        )
                    )
        if errors:
            raise ConfigCheckError(errors)
        return tuple(warnings)

    def _save_draft_config_warnings(
        self,
        *,
        account_name: str,
        imap_config: IMAPConfig,
        resolver: IMAPPolicyResolver,
        resolved_configured_folders: Sequence[str] | None = None,
    ) -> list[ConfigCheckWarning]:
        warnings: list[ConfigCheckWarning] = []
        draft_folder_names = _draft_folder_names(imap_config)
        if not draft_folder_names:
            if "Drafts" in imap_config.folders:
                warnings.append(
                    ConfigCheckWarning(
                        message=(
                            "save_draft will not select Drafts by default because "
                            "the folder is not marked kind: DRAFTS"
                        ),
                        account=account_name,
                        policy=imap_config.policy,
                    )
                )
            return warnings

        draft_folder_name = _preferred_draft_folder_name(draft_folder_names)
        if (
            resolved_configured_folders is not None
            and draft_folder_name not in resolved_configured_folders
        ):
            return warnings
        if len(draft_folder_names) > 1:
            warnings.append(
                ConfigCheckWarning(
                    message=(
                        "save_draft has multiple configured DRAFTS folders; "
                        f"using {draft_folder_name} by default "
                        f"(configured: {', '.join(sorted(draft_folder_names))})"
                    ),
                    account=account_name,
                    policy=imap_config.policy,
                )
            )

        draft_folder = resolver.resolve_folder(draft_folder_name)
        if not draft_folder.access.allowed:
            warnings.append(
                ConfigCheckWarning(
                    message=(
                        f"save_draft cannot use {draft_folder_name} because "
                        "folder_access denies it"
                    ),
                    account=account_name,
                    policy=imap_config.policy,
                )
            )
        if draft_folder.policy.folder_append == IMAPOperationDecision.deny:
            warnings.append(
                ConfigCheckWarning(
                    message=(
                        f"save_draft cannot append to {draft_folder_name} because "
                        "folder_append is deny"
                    ),
                    account=account_name,
                    policy=imap_config.policy,
                )
            )
        missing_flags = [
            flag.name
            for flag in IMAP_DRAFT_SYSTEM_FLAGS
            if draft_folder.policy.system_flags[flag] != IMAPFlagMode.read_write
        ]
        if missing_flags:
            warnings.append(
                ConfigCheckWarning(
                    message=(
                        f"imap:save_draft cannot set required flags on "
                        f"{draft_folder_name} folder; set "
                        f"{', '.join(missing_flags)} to read_write to allow "
                        "them to be modified"
                    ),
                    account=account_name,
                    policy=imap_config.policy,
                )
            )
        return warnings

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
        runtime_arguments = _operation_arguments(operation, arguments)
        runtime_method = getattr(runtime, operation)
        return runtime_method(**runtime_arguments)

    def check_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
        context: ServicePluginContext,
    ) -> object:
        runtime = context.runtimes.require(self.name, IMAPRuntime)
        return runtime.check_operation(operation, arguments)


def plugin() -> IMAPServicePlugin:
    return IMAPServicePlugin()
