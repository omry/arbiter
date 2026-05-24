from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from hydra.core.config_store import ConfigStore
from omegaconf import OmegaConf


class AccountSensitivityTier(str, Enum):
    standard = "standard"
    sensitive = "sensitive"


class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


class ImapFlagMode(str, Enum):
    hidden = "hidden"
    read_only = "read_only"
    read_write = "read_write"


@dataclass
class ServerConfig:
    name: str = "mail-sentry"
    transport: str = "streamable-http"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    stateless_http: bool = True
    json_response: bool = True


@dataclass
class SmtpLimitsConfig:
    max_messages_per_minute: int | None = None
    max_recipients_per_message: int | None = None


@dataclass
class SmtpIdempotencyConfig:
    expiration_days: int = 7


@dataclass
class RecipientPolicyConfig:
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)


@dataclass
class SmtpConfig:
    host: str = "localhost"
    port: int = 587
    authenticate: bool = False
    username: str = ""
    password: str = ""
    from_email: str = "agent@example.com"
    from_name: str = "Mail Sentry"
    tls: MailTlsMode = MailTlsMode.starttls
    verify_peer: bool = True
    timeout_seconds: float = 30.0
    limits: SmtpLimitsConfig = field(default_factory=SmtpLimitsConfig)
    idempotency: SmtpIdempotencyConfig = field(default_factory=SmtpIdempotencyConfig)
    recipient_policy: RecipientPolicyConfig = field(
        default_factory=RecipientPolicyConfig
    )

    def __post_init__(self) -> None:
        self.tls = _coerce_tls_mode(self.tls, "smtp config tls")
        validate_smtp_config(self)


@dataclass
class ImapFolderConfig:
    description: str = ""


@dataclass
class ImapConfig:
    host: str = "localhost"
    port: int = 993
    username: str = ""
    password: str = ""
    tls: MailTlsMode = MailTlsMode.implicit
    verify_peer: bool = True
    timeout_seconds: float = 30.0
    default_folder: str | None = None
    folders: dict[str, ImapFolderConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tls = _coerce_tls_mode(self.tls, "imap config tls")
        validate_imap_config(self)


@dataclass
class SmtpAuditConfig:
    enabled: bool = True
    retention_days: int = 365
    store_message_metadata: bool = True
    store_message_body: bool = False


@dataclass
class ImapAuditConfig:
    enabled: bool = True
    retention_days: int = 365
    store_message_metadata: bool = True
    store_message_body: bool = False
    audit_read_access: bool = False
    audit_search_queries: bool = False
    audit_message_state_changes: bool = True
    audit_message_moves: bool = True
    audit_message_deletes: bool = True


@dataclass
class ImapSystemFlagsPolicyConfig:
    seen: ImapFlagMode = ImapFlagMode.read_only
    flagged: ImapFlagMode = ImapFlagMode.read_only
    answered: ImapFlagMode = ImapFlagMode.read_only
    deleted: ImapFlagMode = ImapFlagMode.read_only
    draft: ImapFlagMode = ImapFlagMode.read_only

    def __post_init__(self) -> None:
        self.seen = _coerce_imap_flag_mode(self.seen, "imap system_flags.seen")
        self.flagged = _coerce_imap_flag_mode(self.flagged, "imap system_flags.flagged")
        self.answered = _coerce_imap_flag_mode(
            self.answered, "imap system_flags.answered"
        )
        self.deleted = _coerce_imap_flag_mode(self.deleted, "imap system_flags.deleted")
        self.draft = _coerce_imap_flag_mode(self.draft, "imap system_flags.draft")


@dataclass
class ImapAccessPolicyConfig:
    allow_read: bool = True
    allow_search: bool = True
    allow_move: bool = True
    allow_delete: bool = True
    system_flags: ImapSystemFlagsPolicyConfig = field(
        default_factory=ImapSystemFlagsPolicyConfig
    )
    user_flags: dict[str, ImapFlagMode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.user_flags = {
            flag: _coerce_imap_flag_mode(mode, f"imap user_flags.{flag}")
            for flag, mode in self.user_flags.items()
        }
        validate_imap_access_policy(self)


@dataclass
class AccountAccessProfileConfig:
    allow_smtp_send: bool = True
    imap: ImapAccessPolicyConfig = field(default_factory=ImapAccessPolicyConfig)
    smtp_audit: SmtpAuditConfig = field(default_factory=SmtpAuditConfig)
    imap_audit: ImapAuditConfig = field(default_factory=ImapAuditConfig)


@dataclass
class AccountConfig:
    description: str = ""
    account_access_profile: str = "bot"
    sensitivity_tier: AccountSensitivityTier = AccountSensitivityTier.standard
    smtp: SmtpConfig | None = None
    imap: ImapConfig | None = None


def _default_accounts() -> dict[str, AccountConfig]:
    return {
        "primary": AccountConfig(
            description="Bot-owned account for automated email tasks.",
            account_access_profile="bot",
            smtp=SmtpConfig(),
        )
    }


def _default_access_profiles() -> dict[str, AccountAccessProfileConfig]:
    return {"bot": AccountAccessProfileConfig()}


@dataclass
class MailConfig:
    accounts: dict[str, AccountConfig] = field(default_factory=_default_accounts)
    account_access_profiles: dict[str, AccountAccessProfileConfig] = field(
        default_factory=_default_access_profiles
    )


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    mail: MailConfig = field(default_factory=MailConfig)

    def __post_init__(self) -> None:
        validate_app_config(self)


class ServerConfigLike(Protocol):
    name: str
    transport: str
    host: str
    port: int
    path: str
    stateless_http: bool
    json_response: bool


class SmtpConfigLike(Protocol):
    host: str
    port: int
    authenticate: bool
    username: str
    password: str
    from_email: str
    from_name: str
    tls: MailTlsMode
    verify_peer: bool
    timeout_seconds: float


class ImapConfigLike(Protocol):
    host: str
    port: int
    username: str
    password: str
    tls: MailTlsMode
    verify_peer: bool
    timeout_seconds: float
    default_folder: str | None
    folders: dict[str, ImapFolderConfig]


class AccountConfigLike(Protocol):
    description: str
    account_access_profile: str
    sensitivity_tier: AccountSensitivityTier
    smtp: SmtpConfigLike | None
    imap: ImapConfigLike | None


class AccountAccessProfileConfigLike(Protocol):
    allow_smtp_send: bool
    imap: "ImapAccessPolicyConfigLike"


class ImapSystemFlagsPolicyConfigLike(Protocol):
    seen: ImapFlagMode
    flagged: ImapFlagMode
    answered: ImapFlagMode
    deleted: ImapFlagMode
    draft: ImapFlagMode


class ImapAccessPolicyConfigLike(Protocol):
    allow_read: bool
    allow_search: bool
    allow_move: bool
    allow_delete: bool
    system_flags: ImapSystemFlagsPolicyConfigLike
    user_flags: Mapping[str, ImapFlagMode]


class MailConfigLike(Protocol):
    accounts: Mapping[str, AccountConfigLike]
    account_access_profiles: Mapping[str, AccountAccessProfileConfigLike]


class AppConfigLike(Protocol):
    server: ServerConfigLike
    mail: MailConfigLike


SYSTEM_FLAG_NAME_MAP = {
    "\\Seen": "seen",
    "\\Flagged": "flagged",
    "\\Answered": "answered",
    "\\Deleted": "deleted",
    "\\Draft": "draft",
}


def _coerce_tls_mode(value: MailTlsMode | str, context: str) -> MailTlsMode:
    if isinstance(value, MailTlsMode):
        return value

    if isinstance(value, str):
        try:
            return MailTlsMode(value)
        except ValueError as exc:
            raise ValueError(
                f"{context} must be one of: none, starttls, implicit"
            ) from exc

    raise ValueError(f"{context} must be one of: none, starttls, implicit")


def _coerce_imap_flag_mode(value: ImapFlagMode | str, context: str) -> ImapFlagMode:
    if isinstance(value, ImapFlagMode):
        return value

    if isinstance(value, str):
        try:
            return ImapFlagMode(value)
        except ValueError as exc:
            raise ValueError(
                f"{context} must be one of: hidden, read_only, read_write"
            ) from exc

    raise ValueError(f"{context} must be one of: hidden, read_only, read_write")


def validate_smtp_config(config: SmtpConfigLike) -> None:
    _coerce_tls_mode(config.tls, "smtp config tls")

    has_username = bool(config.username)
    has_password = bool(config.password)

    if config.authenticate:
        if not (has_username and has_password):
            raise ValueError(
                "smtp config requires username and password together when authenticate is true"
            )
    elif has_username or has_password:
        raise ValueError(
            "smtp config requires username and password to be unset when authenticate is false"
        )


def validate_imap_config(config: ImapConfigLike) -> None:
    _coerce_tls_mode(config.tls, "imap config tls")

    has_username = bool(config.username)
    has_password = bool(config.password)

    if has_username != has_password:
        raise ValueError("imap config requires username and password together")

    if config.default_folder and config.default_folder not in config.folders:
        raise ValueError("imap config default_folder must match a configured folder")


def validate_imap_access_policy(config: ImapAccessPolicyConfig) -> None:
    if config.allow_search and not config.allow_read:
        raise ValueError("imap access policy allow_search requires allow_read")

    if config.allow_move and not config.allow_read:
        raise ValueError("imap access policy allow_move requires allow_read")

    if config.allow_delete and not config.allow_read:
        raise ValueError("imap access policy allow_delete requires allow_read")


def resolve_system_flag_key(flag_name: str) -> str | None:
    return SYSTEM_FLAG_NAME_MAP.get(flag_name)


def resolve_imap_flag_mode(
    policy: ImapAccessPolicyConfig,
    flag_name: str,
) -> ImapFlagMode:
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
        return ImapFlagMode.read_only
    return policy.user_flags.get(flag_name, ImapFlagMode.hidden)


def validate_app_config(config: AppConfig) -> None:
    if not config.mail.accounts:
        raise ValueError("mail config requires at least one account")

    if not config.mail.account_access_profiles:
        raise ValueError("mail config requires at least one account access profile")

    for profile in config.mail.account_access_profiles.values():
        validate_imap_access_policy(profile.imap)

    for account_name, account in config.mail.accounts.items():
        if account.account_access_profile not in config.mail.account_access_profiles:
            raise ValueError(
                f"mail account {account_name} references an unknown account_access_profile"
            )

        if account.smtp is None and account.imap is None:
            raise ValueError(
                f"mail account {account_name} must enable smtp, imap, or both"
            )

        if account.smtp is not None:
            validate_smtp_config(account.smtp)

        if account.imap is not None:
            validate_imap_config(account.imap)


_CONFIG_SCHEMA_NAME = "mail_sentry_app_config_schema"
_CONFIG_REGISTERED = False
_RESOLVERS_REGISTERED = False


def _read_secret_file(path: str) -> str:
    secret_path = Path(path).expanduser()
    try:
        return secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ValueError(f"failed to read secret file: {secret_path}") from exc


def _register_resolvers() -> None:
    global _RESOLVERS_REGISTERED
    if _RESOLVERS_REGISTERED:
        return

    if not OmegaConf.has_resolver("secret_file"):
        OmegaConf.register_new_resolver(
            "secret_file",
            _read_secret_file,
            use_cache=False,
        )
    _RESOLVERS_REGISTERED = True


def register_configs() -> None:
    global _CONFIG_REGISTERED
    _register_resolvers()
    if _CONFIG_REGISTERED:
        return

    cs = ConfigStore.instance()
    cs.store(name=_CONFIG_SCHEMA_NAME, node=AppConfig)
    _CONFIG_REGISTERED = True
