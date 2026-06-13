from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from arbiter_server.decisions import (
    AllowDecision,
    AndDecision,
    Decision,
    DecisionResult,
    DenyDecision,
)

from .config import (
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderKind,
    IMAPOperationDecision,
    IMAPSystemFlag,
    resolve_system_flag,
)
from .policy import (
    IMAPFolderResolution,
    IMAPPolicyResolver,
    IMAPResolvedFolderPolicy,
    destination_matches_move_policy,
    flag_mode,
)


CHECK_OPERATION_POLICY_FIELDS = {
    "list_messages": "read",
    "get_message": "read",
    "get_attachment": "read",
    "get_message_flags": "read",
    "search_messages": "search",
    "mark_message_read": "mark_read",
    "delete_message": "delete",
    "append_message": "folder_append",
}


@dataclass(frozen=True)
class _PreparedMove:
    imap_config: IMAPConfig
    source: IMAPFolderResolution
    destination: IMAPFolderResolution
    uid: str
    decision: DecisionResult


@dataclass(frozen=True)
class _PreparedMarkRead:
    imap_config: IMAPConfig
    folder: IMAPFolderResolution
    uid: str
    read: bool
    decision: DecisionResult


@dataclass(frozen=True)
class _PreparedDelete:
    imap_config: IMAPConfig
    folder: IMAPFolderResolution
    uid: str
    permanent: bool
    decision: DecisionResult


@dataclass(frozen=True)
class _PreparedFlagUpdate:
    imap_config: IMAPConfig
    folder: IMAPFolderResolution
    uid: str
    add_flags: tuple[str, ...]
    remove_flags: tuple[str, ...]
    decision: DecisionResult


@dataclass(frozen=True)
class _PreparedAppend:
    imap_config: IMAPConfig
    folder: IMAPFolderResolution
    message_bytes: bytes | None
    flags: tuple[str, ...]
    decision: DecisionResult


class _IMAPRuntimePolicyMixin:
    def _resolve_context(
        self,
        tool_name: str,
        account_name: str,
        folder: str | None,
    ) -> tuple[IMAPConfig, IMAPFolderResolution]:
        raise NotImplementedError

    def _resolve_named_folder(
        self,
        tool_name: str,
        account_name: str,
        imap_config: IMAPConfig,
        folder: str,
    ) -> IMAPFolderResolution:
        raise NotImplementedError

    def _resolver(self, imap_config: IMAPConfig) -> IMAPPolicyResolver:
        raise NotImplementedError

    def _normalize_message_uid(self, message_id: str) -> str:
        raise NotImplementedError

    def _normalize_flags(self, flags: Sequence[str]) -> tuple[str, ...]:
        raise NotImplementedError

    def _check_move_message(
        self,
        operation_id: str,
        account: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        prepared = self._prepare_move_message(
            account=account,
            message_id=cast(str, arguments.get("message_id")),
            destination_folder=cast(str, arguments.get("destination_folder")),
            folder=cast(str | None, arguments.get("folder")),
        )
        return self._decision_check_result(operation_id, prepared.decision)

    def _check_update_message_flags(
        self,
        operation_id: str,
        account: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            prepared = self._prepare_update_message_flags(
                account=account,
                message_id=cast(str, arguments.get("message_id")),
                folder=cast(str | None, arguments.get("folder")),
                add_flags=cast(Sequence[str], arguments.get("add_flags", ())),
                remove_flags=cast(Sequence[str], arguments.get("remove_flags", ())),
            )
            return self._decision_check_result(operation_id, prepared.decision)
        except Exception as exc:
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": str(exc),
            }

    def _check_append_message(
        self,
        operation_id: str,
        account: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            prepared = self._prepare_append_message(
                account,
                folder=cast(str | None, arguments.get("folder")),
                message=cast(str | None, arguments.get("message")),
                flags=cast(
                    Sequence[str],
                    arguments.get("flags", (IMAPSystemFlag.SEEN.name,)),
                ),
                tool_name="append_message",
            )
            return self._decision_check_result(operation_id, prepared.decision)
        except Exception as exc:
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": str(exc),
            }

    def _decision_check_result(
        self,
        operation_id: str,
        decision: DecisionResult,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "operation": operation_id,
            "allowed": decision.allowed,
            "evidence": decision.evidence,
        }
        if not decision.allowed:
            result["why_not"] = decision.why_not or "operation is not allowed"
            if decision.failed_gate is not None:
                result["failed_gate"] = decision.failed_gate
        return result

    def _operation_decision_for_check(
        self,
        operation: str,
        folder: IMAPFolderResolution,
    ) -> IMAPOperationDecision:
        field_name = CHECK_OPERATION_POLICY_FIELDS.get(operation)
        if field_name is None:
            return IMAPOperationDecision.allow
        return cast(IMAPOperationDecision, getattr(folder.policy, field_name))

    def _folder_check_evidence(self, folder: IMAPFolderResolution) -> dict[str, object]:
        return {
            "name": folder.name,
            "access": "allowed" if folder.access.allowed else "denied",
            "kind": (
                folder.metadata.kind.value if folder.metadata.kind is not None else None
            ),
            "operations": folder.policy.operation_summary(),
            "access_rules": self._access_rules_for_output(folder),
        }

    def _access_rules_for_output(
        self,
        folder: IMAPFolderResolution,
    ) -> list[dict[str, object]]:
        return [
            {
                "index": match.index,
                "rule": match.rule,
                "decision": match.decision,
            }
            for match in folder.access.matching_rules
        ]

    def _folder_access_decision(
        self,
        *,
        account: str,
        tool_name: str,
        folder: IMAPFolderResolution,
        evidence_key: str,
        failed_gate: str,
        why_not: str | None = None,
    ) -> Decision:
        evidence = {evidence_key: self._folder_check_evidence(folder)}
        if folder.access.allowed:
            return AllowDecision(evidence)
        return DenyDecision(
            why_not or f"{tool_name} folder is not accessible for account: {account}",
            evidence,
            failed_gate=failed_gate,
        )

    def _operation_allowed_decision(
        self,
        *,
        account: str,
        tool_name: str,
        decision: IMAPOperationDecision,
        evidence: Mapping[str, object] | None = None,
        operation_label: str | None = None,
        failed_gate: str,
    ) -> Decision:
        if decision is not IMAPOperationDecision.deny:
            return AllowDecision(evidence or {})
        label = operation_label or tool_name
        return DenyDecision(
            f"{label} is not allowed for account: {account}",
            evidence or {},
            failed_gate=failed_gate,
        )

    def _folder_operation_decision(
        self,
        account: str,
        tool_name: str,
        folder: IMAPFolderResolution,
        operation_decision: IMAPOperationDecision,
        *,
        operation_label: str | None = None,
        access_why_not: str | None = None,
    ) -> Decision:
        return AndDecision(
            self._folder_access_decision(
                account=account,
                tool_name=tool_name,
                folder=folder,
                evidence_key="folder",
                failed_gate="folder_access",
                why_not=access_why_not
                or f"{tool_name} folder is not accessible for account: {account}",
            ),
            self._operation_allowed_decision(
                account=account,
                tool_name=tool_name,
                decision=operation_decision,
                operation_label=operation_label,
                failed_gate=operation_label or tool_name,
            ),
        )

    def _flags_read_write_decision(
        self,
        *,
        tool_name: str,
        folder: IMAPFolderResolution,
        flags: Sequence[str],
        flag_action: str,
        failed_gate: str,
    ) -> Decision:
        for flag in flags:
            if flag_mode(folder.policy, flag) is not IMAPFlagMode.read_write:
                return DenyDecision(
                    f"{tool_name} requires read_write access to every {flag_action} flag",
                    failed_gate=failed_gate,
                )
        return AllowDecision()

    def _seen_read_write_decision(
        self,
        *,
        account: str,
        folder: IMAPFolderResolution,
    ) -> Decision:
        if flag_mode(folder.policy, IMAPSystemFlag.SEEN) is IMAPFlagMode.read_write:
            return AllowDecision()
        return DenyDecision(
            f"mark_message_read requires read_write access to the SEEN flag for account: {account}",
            failed_gate="SEEN",
        )

    def _move_message_policy_decision(
        self,
        *,
        account: str,
        source: IMAPFolderResolution,
        destination: IMAPFolderResolution,
    ) -> Decision:
        evidence = {
            "source_folder": self._folder_check_evidence(source),
            "destination_folder": self._folder_check_evidence(destination),
        }
        if destination.metadata.kind is IMAPFolderKind.TRASH:
            return self._operation_allowed_decision(
                account=account,
                tool_name="move_message",
                decision=source.policy.delete,
                evidence=evidence,
                operation_label="delete",
                failed_gate="delete",
            )
        if destination_matches_move_policy(
            source.policy.move,
            destination_folder=destination.name,
            destination_metadata=destination.metadata,
        ):
            return AllowDecision(evidence)
        return DenyDecision(
            "move_message destination folder is not allowed by source folder policy",
            evidence,
            failed_gate="destination_selector",
        )

    def _configured_trash_decision(
        self,
        *,
        account: str,
        imap_config: IMAPConfig,
    ) -> Decision:
        resolver = self._resolver(imap_config)
        for folder_name in sorted(imap_config.folders):
            folder = resolver.resolve_folder(folder_name)
            if folder.metadata.kind is IMAPFolderKind.TRASH and folder.access.allowed:
                return AllowDecision(
                    {"trash_folder": self._folder_check_evidence(folder)}
                )
        return DenyDecision(
            "delete_message requires an accessible TRASH folder for account: "
            f"{account}",
            failed_gate="trash_folder",
        )

    def _prepare_move_message(
        self,
        *,
        account: str,
        message_id: str,
        destination_folder: str,
        folder: str | None,
    ) -> _PreparedMove:
        imap_config, source = self._resolve_context("move_message", account, folder)
        uid = self._normalize_message_uid(message_id)
        destination = self._resolve_named_folder(
            "move_message",
            account,
            imap_config,
            destination_folder,
        )
        decision = AndDecision(
            self._folder_access_decision(
                account=account,
                tool_name="move_message",
                folder=source,
                evidence_key="source_folder",
                failed_gate="source_access",
                why_not="source folder is not accessible for account",
            ),
            self._folder_access_decision(
                account=account,
                tool_name="move_message",
                folder=destination,
                evidence_key="destination_folder",
                failed_gate="destination_access",
                why_not="destination folder is not accessible for account",
            ),
            self._move_message_policy_decision(
                account=account,
                source=source,
                destination=destination,
            ),
        ).evaluate()
        return _PreparedMove(
            imap_config=imap_config,
            source=source,
            destination=destination,
            uid=uid,
            decision=decision,
        )

    def _prepare_mark_message_read(
        self,
        *,
        account: str,
        message_id: str,
        folder: str | None,
        read: bool,
    ) -> _PreparedMarkRead:
        imap_config, folder_resolution = self._resolve_context(
            "mark_message_read", account, folder
        )
        uid = self._normalize_message_uid(message_id)
        decision = AndDecision(
            self._folder_operation_decision(
                account,
                "mark_message_read",
                folder_resolution,
                folder_resolution.policy.mark_read,
            ),
            self._seen_read_write_decision(
                account=account,
                folder=folder_resolution,
            ),
        ).evaluate()
        return _PreparedMarkRead(
            imap_config=imap_config,
            folder=folder_resolution,
            uid=uid,
            read=read,
            decision=decision,
        )

    def _prepare_delete_message(
        self,
        *,
        account: str,
        message_id: str,
        folder: str | None,
        permanent: bool,
    ) -> _PreparedDelete:
        imap_config, folder_resolution = self._resolve_context(
            "delete_message", account, folder
        )
        uid = self._normalize_message_uid(message_id)
        decisions: list[Decision] = [
            self._folder_operation_decision(
                account,
                "delete_message",
                folder_resolution,
                folder_resolution.policy.delete,
            )
        ]
        if not permanent:
            decisions.append(
                self._configured_trash_decision(
                    account=account,
                    imap_config=imap_config,
                )
            )
        return _PreparedDelete(
            imap_config=imap_config,
            folder=folder_resolution,
            uid=uid,
            permanent=permanent,
            decision=AndDecision(*decisions).evaluate(),
        )

    def _prepare_update_message_flags(
        self,
        *,
        account: str,
        message_id: str,
        folder: str | None,
        add_flags: Sequence[str],
        remove_flags: Sequence[str],
    ) -> _PreparedFlagUpdate:
        imap_config, folder_resolution = self._resolve_context(
            "update_message_flags", account, folder
        )
        normalized_add, normalized_remove = self._validate_update_message_flags_policy(
            add_flags=add_flags,
            remove_flags=remove_flags,
        )
        uid = self._normalize_message_uid(message_id)
        decision = AndDecision(
            self._folder_access_decision(
                account=account,
                tool_name="update_message_flags",
                folder=folder_resolution,
                evidence_key="folder",
                failed_gate="folder_access",
            ),
            self._flags_read_write_decision(
                tool_name="update_message_flags",
                folder=folder_resolution,
                flags=(*normalized_add, *normalized_remove),
                flag_action="changed",
                failed_gate="flags",
            ),
        ).evaluate()
        return _PreparedFlagUpdate(
            imap_config=imap_config,
            folder=folder_resolution,
            uid=uid,
            add_flags=normalized_add,
            remove_flags=normalized_remove,
            decision=decision,
        )

    def _prepare_append_message(
        self,
        account: str,
        *,
        folder: str | None,
        flags: Sequence[str],
        tool_name: str,
        message: str | None = None,
        message_bytes: bytes | None = None,
        require_message: bool = True,
    ) -> _PreparedAppend:
        if require_message and message_bytes is None and not message:
            raise ValueError(f"{tool_name} requires a non-empty message")
        imap_config, folder_resolution = self._resolve_context(
            tool_name, account, folder
        )
        normalized_flags = self._normalize_flags(flags)
        decision = AndDecision(
            self._folder_operation_decision(
                account,
                tool_name,
                folder_resolution,
                folder_resolution.policy.folder_append,
                operation_label="folder_append",
            ),
            self._flags_read_write_decision(
                tool_name=tool_name,
                folder=folder_resolution,
                flags=normalized_flags,
                flag_action="appended",
                failed_gate="flags",
            ),
        ).evaluate()
        resolved_message_bytes = (
            message_bytes
            if message_bytes is not None
            else message.encode("utf-8") if message is not None else None
        )
        return _PreparedAppend(
            imap_config=imap_config,
            folder=folder_resolution,
            message_bytes=resolved_message_bytes,
            flags=normalized_flags,
            decision=decision,
        )

    def _validate_update_message_flags_policy(
        self,
        *,
        add_flags: Sequence[str],
        remove_flags: Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        normalized_add = self._normalize_flags(add_flags)
        normalized_remove = self._normalize_flags(remove_flags)
        if not normalized_add and not normalized_remove:
            raise ValueError("update_message_flags requires add_flags or remove_flags")
        return normalized_add, normalized_remove

    def _require_accessible(
        self,
        account: str,
        tool_name: str,
        folder: IMAPFolderResolution,
    ) -> None:
        if not folder.access.allowed:
            raise ValueError(
                f"{tool_name} folder is not accessible for account: {account}"
            )

    def _require_operation(
        self,
        account: str,
        tool_name: str,
        folder: IMAPFolderResolution,
        decision: IMAPOperationDecision,
        *,
        operation_label: str | None = None,
    ) -> None:
        if decision is IMAPOperationDecision.deny:
            label = operation_label or tool_name
            raise ValueError(f"{label} is not allowed for account: {account}")

    def _visible_flags(
        self,
        imap_policy: IMAPResolvedFolderPolicy,
        flags: list[str],
    ) -> list[str]:
        visible_flags: list[str] = []
        for flag in flags:
            mode = flag_mode(imap_policy, flag)
            if mode is IMAPFlagMode.hidden:
                continue
            system_flag = resolve_system_flag(flag)
            visible_flags.append(system_flag.name if system_flag is not None else flag)
        return visible_flags
