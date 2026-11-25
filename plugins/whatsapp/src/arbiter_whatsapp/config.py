from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
import re
from typing import Any, cast

from hydra.core.config_store import ConfigStore
from omegaconf import OmegaConf

from arbiter_server.services import ConfigCheckError, ConfigCheckIssue


@dataclass
class WhatsAppAccount:
    credential: Any = "whatsapp/default"
    policy: Any = "default"


@dataclass
class WhatsAppPolicyConfig:
    # Preserve raw values during Hydra composition. Authorization never coerces.
    updates: Any = True
    send_messages: Any = True
    read_receipts: Any = True
    typing: Any = True
    media_ingress: Any = True
    media_download: Any = True
    media_egress: Any = True
    media_delete: Any = True


@dataclass(frozen=True)
class WhatsAppPolicy:
    updates: bool = True
    send_messages: bool = True
    read_receipts: bool = True
    typing: bool = True
    media_ingress: bool = True
    media_download: bool = True
    media_egress: bool = True
    media_delete: bool = True


EFFECTS = {
    "receive_updates": ("updates",),
    "send_message": ("send_messages",),
    "mark_read": ("read_receipts",),
    "typing": ("typing", "read_receipts"),
    "get_media": ("media_ingress",),
    "download_media": ("media_ingress", "media_download"),
    "upload_media": ("media_egress",),
    "delete_media": ("media_delete",),
}


def plain(value: object) -> dict[str, Any]:
    if OmegaConf.is_config(value):
        value = OmegaConf.to_container(value, resolve=True)
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise ValueError("configuration must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("configuration keys must be strings")
    return dict(cast(Mapping[str, Any], value))


def reference(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 256
        or re.fullmatch(r"whatsapp/[A-Za-z0-9_-]+", value) is None
    ):
        raise ValueError("invalid WhatsApp credential reference")
    return value


def validate_config(
    accounts: Mapping[str, object], policies: Mapping[str, object]
) -> tuple[dict[str, WhatsAppAccount], dict[str, WhatsAppPolicy]]:
    try:
        typed_policies: dict[str, WhatsAppPolicy] = {}
        known = {f.name for f in fields(WhatsAppPolicy)}
        for name, raw in policies.items():
            values = plain(raw)
            if set(values) - known or any(type(v) is not bool for v in values.values()):
                raise ValueError("policy fields must be known exact booleans")
            typed_policies[name] = WhatsAppPolicy(**values)
        typed_accounts: dict[str, WhatsAppAccount] = {}
        seen: set[str] = set()
        for name, raw in accounts.items():
            values = plain(raw)
            if set(values) - {"credential", "policy"}:
                raise ValueError("unknown WhatsApp account fields")
            account = WhatsAppAccount(**values)
            ref = reference(account.credential)
            if (
                not isinstance(account.policy, str)
                or account.policy not in typed_policies
                or ref in seen
            ):
                raise ValueError("missing policy or duplicate credential reference")
            seen.add(ref)
            typed_accounts[name] = account
        return typed_accounts, typed_policies
    except (ValueError, TypeError):
        raise ConfigCheckError(
            [ConfigCheckIssue("Invalid WhatsApp account or policy configuration.")]
        ) from None


def register_configs(config_store: ConfigStore) -> None:
    for kind, schema in [
        ("account", WhatsAppAccount),
        ("policy", WhatsAppPolicyConfig),
    ]:
        for name in ("schema", "example"):
            config_store.store(
                group=f"arbiter/{kind}/whatsapp",
                name=name,
                node=schema,
                provider="arbiter-whatsapp",
            )
