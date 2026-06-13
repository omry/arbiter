from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, cast

from arbiter_server.config import Policy
from hydra.core.config_store import ConfigStore
from omegaconf import MISSING


class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


class IMAPFlagMode(str, Enum):
    hidden = "hidden"
    read_only = "read_only"
    read_write = "read_write"


class IMAPSystemFlag(str, Enum):
    SEEN = r"\Seen"
    FLAGGED = r"\Flagged"
    ANSWERED = r"\Answered"
    DELETED = r"\Deleted"
    DRAFT = r"\Draft"


class IMAPConfirmationAction(str, Enum):
    read = "read"
    search = "search"
    move = "move"
    mark_read = "mark_read"
    delete = "delete"
    folder_append = "folder_append"


class IMAPFolderKind(str, Enum):
    INBOX = "INBOX"
    ALL = "ALL"
    ARCHIVE = "ARCHIVE"
    DRAFTS = "DRAFTS"
    FLAGGED = "FLAGGED"
    JUNK = "JUNK"
    SENT = "SENT"
    TRASH = "TRASH"


class IMAPOperationDecision(str, Enum):
    allow = "allow"
    deny = "deny"


@dataclass
class IMAPFolderConfig:
    description: str = ""
    kind: IMAPFolderKind | None = None


@dataclass
class IMAPConfig(Policy):
    policy: str = "bot"
    description: str = ""
    guidance: str = ""
    host: str = "localhost"
    port: int = 993
    username: str = ""
    password: str = ""
    tls: MailTlsMode = MailTlsMode.implicit
    verify_peer: bool = True
    timeout_seconds: float = 30.0
    default_folder: str | None = None
    folders: dict[str, IMAPFolderConfig] = field(default_factory=dict)


@dataclass
class IMAPSystemFlagsPolicyConfig:
    SEEN: IMAPFlagMode = cast(IMAPFlagMode, MISSING)
    FLAGGED: IMAPFlagMode = cast(IMAPFlagMode, MISSING)
    ANSWERED: IMAPFlagMode = cast(IMAPFlagMode, MISSING)
    DELETED: IMAPFlagMode = cast(IMAPFlagMode, MISSING)
    DRAFT: IMAPFlagMode = cast(IMAPFlagMode, MISSING)


def default_imap_system_flags_policy() -> IMAPSystemFlagsPolicyConfig:
    return IMAPSystemFlagsPolicyConfig(
        SEEN=IMAPFlagMode.read_only,
        FLAGGED=IMAPFlagMode.read_only,
        ANSWERED=IMAPFlagMode.read_only,
        DELETED=IMAPFlagMode.read_only,
        DRAFT=IMAPFlagMode.read_only,
    )


@dataclass
class IMAPFolderAccessRuleConfig:
    allow_exact: str | None = None
    deny_exact: str | None = None
    allow_glob: str | None = None
    deny_glob: str | None = None
    allow_regex: str | None = None
    deny_regex: str | None = None
    allow_kind: IMAPFolderKind | None = None
    deny_kind: IMAPFolderKind | None = None


@dataclass
class IMAPFolderAccessConfig:
    rules: list[IMAPFolderAccessRuleConfig] = field(default_factory=list)


@dataclass
class IMAPMovePolicyConfig:
    allowed: bool = False
    to_exact: Any = None
    to_glob: Any = None
    to_regex: Any = None
    to_kind: Any = None


@dataclass
class IMAPFolderOperationPolicyConfig:
    read: IMAPOperationDecision | None = None
    search: IMAPOperationDecision | None = None
    move: Any = None
    mark_read: IMAPOperationDecision | None = None
    delete: IMAPOperationDecision | None = None
    folder_append: IMAPOperationDecision | None = None
    system_flags: IMAPSystemFlagsPolicyConfig | None = None
    user_flags: dict[str, IMAPFlagMode] | None = None


@dataclass
class IMAPFolderPolicyDefaultsConfig:
    read: IMAPOperationDecision = IMAPOperationDecision.allow
    search: IMAPOperationDecision = IMAPOperationDecision.allow
    move: Any = False
    mark_read: IMAPOperationDecision = IMAPOperationDecision.deny
    delete: IMAPOperationDecision = IMAPOperationDecision.deny
    folder_append: IMAPOperationDecision = IMAPOperationDecision.deny
    system_flags: IMAPSystemFlagsPolicyConfig = field(
        default_factory=default_imap_system_flags_policy
    )
    user_flags: dict[str, IMAPFlagMode] = field(default_factory=dict)


@dataclass
class IMAPAccessPolicyConfig(Policy):
    folder_access: IMAPFolderAccessConfig = field(
        default_factory=IMAPFolderAccessConfig
    )
    operation_defaults: IMAPFolderPolicyDefaultsConfig = field(
        default_factory=IMAPFolderPolicyDefaultsConfig
    )
    folders: dict[str, IMAPFolderOperationPolicyConfig] = field(default_factory=dict)
    confirmation_required: list[IMAPConfirmationAction] = field(default_factory=list)


USER_FLAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SYSTEM_FLAGS_BY_NAME = {flag.name: flag for flag in IMAPSystemFlag}
SYSTEM_FLAGS_BY_VALUE = {flag.value: flag for flag in IMAPSystemFlag}


def resolve_system_flag(flag_name: str) -> IMAPSystemFlag | None:
    return SYSTEM_FLAGS_BY_NAME.get(flag_name) or SYSTEM_FLAGS_BY_VALUE.get(flag_name)


def normalize_imap_flag_name(flag_name: str) -> str:
    system_flag = resolve_system_flag(flag_name)
    return system_flag.value if system_flag is not None else flag_name


def validate_user_flag_name(flag_name: str) -> None:
    if (
        not USER_FLAG_PATTERN.fullmatch(flag_name)
        or resolve_system_flag(flag_name) is not None
    ):
        raise ValueError(
            f"IMAP user flag name {flag_name!r} must be a non-system IMAP atom"
        )


def _resolved_flag_mode(mode: IMAPFlagMode | None) -> IMAPFlagMode:
    return mode if mode is not None and mode != MISSING else IMAPFlagMode.hidden


def _resolved_system_flag_mode(
    system_flags: IMAPSystemFlagsPolicyConfig | None,
    key: str | None,
) -> IMAPFlagMode | None:
    if key is None:
        return None
    if system_flags is None:
        return IMAPFlagMode.hidden
    return _resolved_flag_mode(getattr(system_flags, key))


def resolve_imap_flag_mode(
    policy: IMAPFolderPolicyDefaultsConfig | IMAPFolderOperationPolicyConfig,
    flag_name: str,
) -> IMAPFlagMode:
    system_flag = resolve_system_flag(flag_name)
    system_flag_mode = _resolved_system_flag_mode(
        policy.system_flags,
        system_flag.name if system_flag is not None else None,
    )
    if system_flag_mode is not None:
        return system_flag_mode
    if flag_name.startswith("\\"):
        return IMAPFlagMode.read_only
    user_flags = policy.user_flags or {}
    return user_flags.get(flag_name, IMAPFlagMode.hidden)


def register_configs(config_store: ConfigStore) -> None:
    config_store.store(
        group="arbiter/account/imap",
        name="schema",
        node=IMAPConfig,
        provider="arbiter-imap",
    )
    config_store.store(
        group="arbiter/policy/imap",
        name="schema",
        node=IMAPAccessPolicyConfig,
        provider="arbiter-imap",
    )
