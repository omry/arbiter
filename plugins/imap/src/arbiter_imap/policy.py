from __future__ import annotations

from dataclasses import dataclass, replace
import fnmatch
import re
from collections.abc import Mapping
from typing import Any

from omegaconf import OmegaConf
from omegaconf.errors import MissingMandatoryValue

from .config import (
    IMAPAccessPolicyConfig,
    IMAPFlagMode,
    IMAPFolderAccessRuleConfig,
    IMAPFolderConfig,
    IMAPFolderKind,
    IMAPFolderOperationPolicyConfig,
    IMAPFolderPolicyDefaultsConfig,
    IMAPMovePolicyConfig,
    IMAPOperationDecision,
    IMAPSystemFlag,
    IMAPSystemFlagsPolicyConfig,
    resolve_system_flag,
    validate_user_flag_name,
)


ACCESS_RULE_KEYS = (
    "allow_exact",
    "deny_exact",
    "allow_glob",
    "deny_glob",
    "allow_regex",
    "deny_regex",
    "allow_kind",
    "deny_kind",
)
OPERATION_POLICY_FIELDS = (
    "read",
    "search",
    "mark_read",
    "delete",
    "folder_append",
)


@dataclass(frozen=True)
class IMAPResolvedFolderMetadata:
    description: str = ""
    kind: IMAPFolderKind | None = None


@dataclass(frozen=True)
class IMAPAccessRuleMatch:
    index: int
    rule: dict[str, str]
    decision: str


@dataclass(frozen=True)
class IMAPFolderAccessDecision:
    allowed: bool
    matching_rules: list[IMAPAccessRuleMatch]


@dataclass(frozen=True)
class IMAPResolvedMovePolicy:
    allowed: bool = False
    broad: bool = False
    to_exact: tuple[str, ...] = ()
    to_glob: tuple[str, ...] = ()
    to_regex: tuple[str, ...] = ()
    to_kind: tuple[IMAPFolderKind, ...] = ()


@dataclass(frozen=True)
class IMAPResolvedFolderPolicy:
    read: IMAPOperationDecision
    search: IMAPOperationDecision
    move: IMAPResolvedMovePolicy
    mark_read: IMAPOperationDecision
    delete: IMAPOperationDecision
    folder_append: IMAPOperationDecision
    system_flags: dict[IMAPSystemFlag, IMAPFlagMode]
    user_flags: dict[str, IMAPFlagMode]

    def operation_summary(self) -> dict[str, object]:
        return {
            **{
                field_name: getattr(self, field_name).value
                for field_name in OPERATION_POLICY_FIELDS
            },
            "move": self.move_summary(),
            "system_flags": {
                flag.name: mode.value for flag, mode in self.system_flags.items()
            },
            "user_flags": {
                name: mode.value
                for name, mode in sorted(self.user_flags.items())
                if mode is not IMAPFlagMode.hidden
            },
        }

    def move_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {"allowed": self.move.allowed}
        if self.move.to_exact:
            summary["to_exact"] = list(self.move.to_exact)
        if self.move.to_glob:
            summary["to_glob"] = list(self.move.to_glob)
        if self.move.to_regex:
            summary["to_regex"] = list(self.move.to_regex)
        if self.move.to_kind:
            summary["to_kind"] = [kind.value for kind in self.move.to_kind]
        return summary


@dataclass(frozen=True)
class IMAPFolderResolution:
    name: str
    metadata: IMAPResolvedFolderMetadata
    access: IMAPFolderAccessDecision
    policy: IMAPResolvedFolderPolicy


class IMAPPolicyResolver:
    def __init__(
        self,
        *,
        folder_metadata: Mapping[str, IMAPFolderConfig],
        policy: IMAPAccessPolicyConfig,
    ) -> None:
        self._folder_metadata = folder_metadata
        self._policy = policy
        validate_imap_policy(policy)

    def resolve_folder(self, folder_name: str) -> IMAPFolderResolution:
        metadata = resolve_folder_metadata(self._folder_metadata, folder_name)
        access = resolve_folder_access(self._policy, folder_name, metadata)
        policy = resolve_folder_policy(self._policy, folder_name)
        return IMAPFolderResolution(
            name=folder_name,
            metadata=metadata,
            access=access,
            policy=policy,
        )

    def accessible(self, folder_name: str) -> bool:
        return self.resolve_folder(folder_name).access.allowed


def validate_imap_policy(policy: IMAPAccessPolicyConfig) -> None:
    rules = policy.folder_access.rules
    if not rules:
        raise ValueError("IMAP policy folder_access.rules must not be empty")
    for index, rule in enumerate(rules, start=1):
        key, value = _rule_key_value(rule)
        if not isinstance(value, IMAPFolderKind):
            if not isinstance(value, str) or not value:
                raise ValueError(
                    "IMAP folder_access rule "
                    f"{index} {key} must be a non-empty string"
                )
        if key.endswith("_regex"):
            try:
                re.compile(str(value))
            except re.error as exc:
                raise ValueError(
                    f"IMAP folder_access rule {index} {key} has invalid regex: {exc}"
                ) from exc
    first_key, first_value = _rule_key_value(rules[0])
    if (first_key, first_value) not in {
        ("allow_glob", "*"),
        ("deny_glob", "*"),
    }:
        raise ValueError(
            "IMAP policy folder_access.rules first rule must be "
            'allow_glob: "*" or deny_glob: "*"'
        )
    for pattern, override in policy.folders.items():
        try:
            _compile_metadata_pattern(pattern)
        except re.error as exc:
            raise ValueError(
                f"IMAP folder policy pattern {pattern!r} is invalid: {exc}"
            ) from exc
        move = override.move
        if isinstance(move, IMAPMovePolicyConfig):
            validate_move_policy(move, context=f"folder policy {pattern!r}")
        _validate_user_flags(
            override.user_flags,
            context=f"folder policy {pattern!r}",
        )
    if not isinstance(policy.operation_defaults.move, bool):
        validate_move_policy(
            _normalize_move_policy(policy.operation_defaults.move),
            context="policy defaults",
        )
    _materialize_system_flags(
        policy.operation_defaults.system_flags,
        context="policy defaults",
    )
    _validate_user_flags(
        policy.operation_defaults.user_flags, context="policy defaults"
    )


def validate_move_policy(move: IMAPMovePolicyConfig, *, context: str) -> None:
    if not move.allowed:
        return
    if any(
        selector is not None
        for selector in (move.to_exact, move.to_glob, move.to_regex, move.to_kind)
    ):
        _compile_regex_selectors(_string_tuple(move.to_regex))
        return
    raise ValueError(
        f"IMAP {context} structured move allowed=true must define at least one to_* selector"
    )


def resolve_folder_metadata(
    folder_metadata: Mapping[str, IMAPFolderConfig],
    folder_name: str,
) -> IMAPResolvedFolderMetadata:
    resolved = IMAPResolvedFolderMetadata()
    for pattern, config in folder_metadata.items():
        captures = _match_metadata_pattern(pattern, folder_name)
        if captures is None:
            continue
        description = _format_description(config.description, captures)
        resolved = IMAPResolvedFolderMetadata(
            description=description if description else resolved.description,
            kind=config.kind if config.kind is not None else resolved.kind,
        )
    return resolved


def resolve_folder_access(
    policy: IMAPAccessPolicyConfig,
    folder_name: str,
    metadata: IMAPResolvedFolderMetadata,
) -> IMAPFolderAccessDecision:
    allowed = False
    matches: list[IMAPAccessRuleMatch] = []
    for index, rule in enumerate(policy.folder_access.rules, start=1):
        key, value = _rule_key_value(rule)
        if not _rule_matches(key, value, folder_name, metadata):
            continue
        allowed = key.startswith("allow_")
        matches.append(
            IMAPAccessRuleMatch(
                index=index,
                rule={key: _rule_value_for_output(value)},
                decision="allow" if allowed else "deny",
            )
        )
    return IMAPFolderAccessDecision(allowed=allowed, matching_rules=matches)


def resolve_folder_policy(
    policy: IMAPAccessPolicyConfig,
    folder_name: str,
) -> IMAPResolvedFolderPolicy:
    effective = _defaults_to_override(policy.operation_defaults)
    for pattern, override in policy.folders.items():
        if _match_metadata_pattern(pattern, folder_name) is None:
            continue
        effective = _merge_policy(effective, override)
    return _resolve_policy(effective)


def destination_matches_move_policy(
    move: IMAPResolvedMovePolicy,
    *,
    destination_folder: str,
    destination_metadata: IMAPResolvedFolderMetadata,
) -> bool:
    if not move.allowed:
        return False
    if move.broad:
        return True
    if destination_folder in move.to_exact:
        return True
    if any(
        fnmatch.fnmatchcase(destination_folder, pattern) for pattern in move.to_glob
    ):
        return True
    if any(re.search(pattern, destination_folder) for pattern in move.to_regex):
        return True
    return destination_metadata.kind in move.to_kind


def flag_mode(policy: IMAPResolvedFolderPolicy, flag_name: str) -> IMAPFlagMode:
    system_flag = resolve_system_flag(flag_name)
    if system_flag is not None:
        return policy.system_flags[system_flag]
    if flag_name.startswith("\\"):
        return IMAPFlagMode.read_only
    return policy.user_flags.get(flag_name, IMAPFlagMode.hidden)


def _defaults_to_override(
    defaults: IMAPFolderPolicyDefaultsConfig,
) -> IMAPFolderOperationPolicyConfig:
    return IMAPFolderOperationPolicyConfig(
        **{
            field_name: getattr(defaults, field_name)
            for field_name in OPERATION_POLICY_FIELDS
        },
        move=defaults.move,
        system_flags=defaults.system_flags,
        user_flags=dict(defaults.user_flags),
    )


def _merge_policy(
    base: IMAPFolderOperationPolicyConfig,
    override: IMAPFolderOperationPolicyConfig,
) -> IMAPFolderOperationPolicyConfig:
    merged = replace(base)
    for field_name in OPERATION_POLICY_FIELDS:
        value = getattr(override, field_name)
        if value is not None:
            merged = replace(merged, **{field_name: value})
    if override.move is not None:
        merged = replace(merged, move=_merge_move_policy(base.move, override.move))
    if override.system_flags is not None:
        if merged.system_flags is None:
            system_flags = _materialize_system_flags(
                override.system_flags,
                context="folder policy",
            )
        else:
            system_flags = _merge_system_flags(
                merged.system_flags, override.system_flags
            )
        merged = replace(merged, system_flags=system_flags)
    if override.user_flags is not None:
        user_flags = dict(merged.user_flags or {})
        user_flags.update(override.user_flags)
        merged = replace(merged, user_flags=user_flags)
    return merged


def _merge_move_policy(
    base: Any,
    override: Any,
) -> Any:
    if isinstance(override, bool):
        return override
    override_policy = _normalize_move_policy(override)
    base_policy = _normalize_move_policy(base)
    return IMAPMovePolicyConfig(
        allowed=override_policy.allowed,
        to_exact=(
            override_policy.to_exact
            if override_policy.to_exact is not None
            else base_policy.to_exact
        ),
        to_glob=(
            override_policy.to_glob
            if override_policy.to_glob is not None
            else base_policy.to_glob
        ),
        to_regex=(
            override_policy.to_regex
            if override_policy.to_regex is not None
            else base_policy.to_regex
        ),
        to_kind=(
            override_policy.to_kind
            if override_policy.to_kind is not None
            else base_policy.to_kind
        ),
    )


def _resolve_policy(
    config: IMAPFolderOperationPolicyConfig,
) -> IMAPResolvedFolderPolicy:
    move = _normalize_move_policy(config.move)
    operation_values = {
        field_name: getattr(config, field_name) or IMAPOperationDecision.deny
        for field_name in OPERATION_POLICY_FIELDS
    }
    if config.system_flags is None:
        raise ValueError("IMAP effective folder policy has no system_flags")
    system_flags = _materialize_system_flags(
        config.system_flags,
        context="effective folder policy",
    )
    return IMAPResolvedFolderPolicy(
        **operation_values,
        move=IMAPResolvedMovePolicy(
            allowed=move.allowed,
            broad=move.allowed
            and not any(
                selector is not None
                for selector in (
                    move.to_exact,
                    move.to_glob,
                    move.to_regex,
                    move.to_kind,
                )
            ),
            to_exact=_string_tuple(move.to_exact),
            to_glob=_string_tuple(move.to_glob),
            to_regex=_string_tuple(move.to_regex),
            to_kind=_kind_tuple(move.to_kind),
        ),
        system_flags={
            flag: getattr(system_flags, flag.name) for flag in IMAPSystemFlag
        },
        user_flags=dict(config.user_flags or {}),
    )


def _normalize_move_policy(
    move: Any,
) -> IMAPMovePolicyConfig:
    if isinstance(move, IMAPMovePolicyConfig):
        return move
    if isinstance(move, Mapping):
        return IMAPMovePolicyConfig(
            allowed=bool(move.get("allowed", False)),
            to_exact=move.get("to_exact"),
            to_glob=move.get("to_glob"),
            to_regex=move.get("to_regex"),
            to_kind=move.get("to_kind"),
        )
    return IMAPMovePolicyConfig(allowed=bool(move))


def _merge_system_flags(
    base: Any,
    override: Any,
) -> IMAPSystemFlagsPolicyConfig:
    return _materialize_system_flags(
        OmegaConf.merge(base, override),
        context="effective folder policy",
    )


def _materialize_system_flags(
    config: Any,
    *,
    context: str,
) -> IMAPSystemFlagsPolicyConfig:
    try:
        system_flags = OmegaConf.to_object(
            OmegaConf.merge(IMAPSystemFlagsPolicyConfig, config)
        )
    except MissingMandatoryValue as exc:
        raise ValueError(f"IMAP {context} has incomplete system_flags") from exc
    if not isinstance(system_flags, IMAPSystemFlagsPolicyConfig):
        raise TypeError("IMAP system_flags must be a structured config")
    return system_flags


def _validate_user_flags(
    user_flags: Mapping[str, IMAPFlagMode] | None,
    *,
    context: str,
) -> None:
    for flag_name in user_flags or {}:
        try:
            validate_user_flag_name(flag_name)
        except ValueError as exc:
            raise ValueError(f"IMAP {context} has invalid user flag: {exc}") from exc


def _compile_regex_selectors(patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        re.compile(pattern)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _kind_tuple(
    value: Any,
) -> tuple[IMAPFolderKind, ...]:
    if value is None:
        return ()
    if isinstance(value, IMAPFolderKind):
        return (value,)
    if isinstance(value, str):
        return (IMAPFolderKind(value),)
    return tuple(
        item if isinstance(item, IMAPFolderKind) else IMAPFolderKind(item)
        for item in value
    )


def _rule_key_value(
    rule: IMAPFolderAccessRuleConfig,
) -> tuple[str, str | IMAPFolderKind]:
    values = [
        (key, getattr(rule, key))
        for key in ACCESS_RULE_KEYS
        if getattr(rule, key) is not None
    ]
    if len(values) != 1:
        raise ValueError(
            "IMAP folder_access rules must set exactly one of "
            + ", ".join(ACCESS_RULE_KEYS)
        )
    return values[0]


def _rule_matches(
    key: str,
    value: str | IMAPFolderKind,
    folder_name: str,
    metadata: IMAPResolvedFolderMetadata,
) -> bool:
    if key.endswith("_exact"):
        return folder_name == value
    if key.endswith("_glob"):
        return fnmatch.fnmatchcase(folder_name, str(value))
    if key.endswith("_regex"):
        return re.search(str(value), folder_name) is not None
    if key.endswith("_kind"):
        return metadata.kind == value
    raise AssertionError(f"unknown IMAP folder access rule key: {key}")


def _rule_value_for_output(value: str | IMAPFolderKind) -> str:
    return value.value if isinstance(value, IMAPFolderKind) else value


def _compile_metadata_pattern(pattern: str) -> re.Pattern[str]:
    regex_parts: list[str] = []
    index = 0
    positional_index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            regex_parts.append(f"(?P<p{positional_index}>.*?)")
            positional_index += 1
            index += 1
            continue
        if char == "{":
            end_index = pattern.find("}", index + 1)
            if end_index < 0:
                raise re.error("unclosed capture block")
            block = pattern[index + 1 : end_index]
            if ":" in block:
                matcher, name = block.rsplit(":", 1)
            else:
                matcher, name = "*", block
            regex_parts.append(f"(?P<{name}>{_glob_to_regex(matcher)})")
            index = end_index + 1
            continue
        regex_parts.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(regex_parts) + "$")


def _match_metadata_pattern(
    pattern: str,
    folder_name: str,
) -> dict[str, str] | None:
    match = _compile_metadata_pattern(pattern).match(folder_name)
    if match is None:
        return None
    captures = {
        key.removeprefix("p"): value
        for key, value in match.groupdict().items()
        if value is not None
    }
    return captures


def _glob_to_regex(pattern: str) -> str:
    translated = fnmatch.translate(pattern)
    if translated.startswith("(?s:") and translated.endswith(")\\Z"):
        return translated[4:-3]
    return translated


def _format_description(description: str, captures: Mapping[str, str]) -> str:
    resolved = description
    for name, value in captures.items():
        resolved = resolved.replace("{" + name + "}", value)
    return resolved
