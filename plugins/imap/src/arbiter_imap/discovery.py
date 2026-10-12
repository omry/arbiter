"""Explicit public projections; never serialize entire account or policy objects."""

from collections.abc import Mapping, Sequence
from typing import cast
from omegaconf import MISSING, OmegaConf

from .config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFolderOperationPolicyConfig,
    IMAPFolderPolicyDefaultsConfig,
    IMAPMovePolicyConfig,
    IMAPSystemFlag,
    IMAPSystemFlagsPolicyConfig,
)


def public_account_config(config: object) -> dict[str, object]:
    account = cast(IMAPConfig, config)
    return {
        "default_folder": account.default_folder,
        "folders": {
            name: {"description": folder.description, "kind": folder.kind}
            for name, folder in account.folders.items()
        },
    }


def _public_selector(value: object) -> object:
    return (
        list(value)
        if isinstance(value, Sequence) and not isinstance(value, str)
        else value
    )


def _system_flags(
    config: IMAPSystemFlagsPolicyConfig | None,
) -> dict[str, object] | None:
    if config is None:
        return None
    result: dict[str, object] = {}
    for flag in IMAPSystemFlag:
        if OmegaConf.is_config(config) and OmegaConf.is_missing(config, flag.name):
            continue
        mode = getattr(config, flag.name)
        if mode != MISSING:
            result[flag.name] = mode
    return result


def _operation_policy(
    config: IMAPFolderOperationPolicyConfig | IMAPFolderPolicyDefaultsConfig,
) -> dict[str, object]:
    move = config.move
    if move is not None and not isinstance(move, bool):
        # The schema also accepts a mapping for move selectors.
        if isinstance(move, Mapping):
            move = {
                name: _public_selector(move.get(name))
                for name in ("allowed", "to_exact", "to_glob", "to_regex", "to_kind")
            }
        else:
            move_config = cast(IMAPMovePolicyConfig, move)
            move = {
                "allowed": move_config.allowed,
                "to_exact": _public_selector(move_config.to_exact),
                "to_glob": _public_selector(move_config.to_glob),
                "to_regex": _public_selector(move_config.to_regex),
                "to_kind": _public_selector(move_config.to_kind),
            }
    return {
        "read": config.read,
        "search": config.search,
        "move": move,
        "mark_read": config.mark_read,
        "delete": config.delete,
        "folder_append": config.folder_append,
        "system_flags": _system_flags(config.system_flags),
        "user_flags": (
            dict(config.user_flags) if config.user_flags is not None else None
        ),
    }


def public_policy_config(config: object) -> dict[str, object]:
    policy = cast(IMAPAccessPolicyConfig, config)
    return {
        "folder_access": {
            "rules": [
                {
                    name: getattr(rule, name)
                    for name in (
                        "allow_exact",
                        "deny_exact",
                        "allow_glob",
                        "deny_glob",
                        "allow_regex",
                        "deny_regex",
                        "allow_kind",
                        "deny_kind",
                    )
                }
                for rule in policy.folder_access.rules
            ]
        },
        "operation_defaults": _operation_policy(policy.operation_defaults),
        "folders": {
            name: _operation_policy(folder) for name, folder in policy.folders.items()
        },
    }
