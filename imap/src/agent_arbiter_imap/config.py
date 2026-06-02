from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_arbiter.config import Policy
from hydra.core.config_store import ConfigStore


class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


class IMAPFlagMode(str, Enum):
    hidden = "hidden"
    read_only = "read_only"
    read_write = "read_write"


class IMAPConfirmationAction(str, Enum):
    read = "read"
    search = "search"
    move = "move"
    mark_read = "mark_read"
    delete = "delete"


@dataclass
class IMAPFolderConfig:
    description: str = ""


@dataclass
class IMAPConfig(Policy):
    policy: str = "bot"
    description: str = ""
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
    seen: IMAPFlagMode = IMAPFlagMode.read_only
    flagged: IMAPFlagMode = IMAPFlagMode.read_only
    answered: IMAPFlagMode = IMAPFlagMode.read_only
    deleted: IMAPFlagMode = IMAPFlagMode.read_only
    draft: IMAPFlagMode = IMAPFlagMode.read_only


@dataclass
class IMAPAccessPolicyConfig(Policy):
    allow_read: bool = True
    allow_search: bool = True
    allow_move: bool = True
    allow_delete: bool = True
    confirmation_required: list[IMAPConfirmationAction] = field(default_factory=list)
    system_flags: IMAPSystemFlagsPolicyConfig = field(
        default_factory=IMAPSystemFlagsPolicyConfig
    )
    user_flags: dict[str, IMAPFlagMode] = field(default_factory=dict)


SYSTEM_FLAG_NAME_MAP = {
    "\\Seen": "seen",
    "\\Flagged": "flagged",
    "\\Answered": "answered",
    "\\Deleted": "deleted",
    "\\Draft": "draft",
}


def resolve_system_flag_key(flag_name: str) -> str | None:
    return SYSTEM_FLAG_NAME_MAP.get(flag_name)


def resolve_imap_flag_mode(
    policy: IMAPAccessPolicyConfig,
    flag_name: str,
) -> IMAPFlagMode:
    system_flag_key = resolve_system_flag_key(flag_name)
    if system_flag_key == "seen":
        return policy.system_flags.seen
    if system_flag_key == "flagged":
        return policy.system_flags.flagged
    if system_flag_key == "answered":
        return policy.system_flags.answered
    if system_flag_key == "deleted":
        return policy.system_flags.deleted
    if system_flag_key == "draft":
        return policy.system_flags.draft
    if flag_name.startswith("\\"):
        return IMAPFlagMode.read_only
    return policy.user_flags.get(flag_name, IMAPFlagMode.hidden)


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
