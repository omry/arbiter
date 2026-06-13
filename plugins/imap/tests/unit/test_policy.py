from __future__ import annotations

from dataclasses import replace

import pytest

from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPFlagMode,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
    IMAPFolderConfig,
    IMAPFolderKind,
    IMAPFolderOperationPolicyConfig,
    IMAPFolderPolicyDefaultsConfig,
    IMAPMovePolicyConfig,
    IMAPOperationDecision,
    IMAPSystemFlag,
    IMAPSystemFlagsPolicyConfig,
    default_imap_system_flags_policy,
)
from arbiter_imap.policy import (
    IMAPPolicyResolver,
    destination_matches_move_policy,
)


def test_folder_access_uses_last_matching_ordered_rule() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={
            "Archive.*": IMAPFolderConfig(kind=IMAPFolderKind.ARCHIVE),
        },
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[
                    IMAPFolderAccessRuleConfig(deny_glob="*"),
                    IMAPFolderAccessRuleConfig(allow_kind=IMAPFolderKind.ARCHIVE),
                    IMAPFolderAccessRuleConfig(deny_exact="Archive.Secret"),
                ]
            )
        ),
    )

    archive = resolver.resolve_folder("Archive.2026")
    secret = resolver.resolve_folder("Archive.Secret")

    assert archive.access.allowed is True
    assert [match.decision for match in archive.access.matching_rules] == [
        "deny",
        "allow",
    ]
    assert secret.access.allowed is False
    assert [match.decision for match in secret.access.matching_rules] == [
        "deny",
        "allow",
        "deny",
    ]


def test_metadata_overlay_supports_captures_and_ordered_merge() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={
            "Archives.*": IMAPFolderConfig(kind=IMAPFolderKind.ARCHIVE),
            "Archives.{20??:year}": IMAPFolderConfig(
                description="Archived mail from {year}"
            ),
        },
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            )
        ),
    )

    folder = resolver.resolve_folder("Archives.2026")

    assert folder.metadata.kind is IMAPFolderKind.ARCHIVE
    assert folder.metadata.description == "Archived mail from 2026"


def test_folder_policy_composes_defaults_and_matching_overrides() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={},
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            ),
            operation_defaults=IMAPFolderPolicyDefaultsConfig(
                read=IMAPOperationDecision.allow,
                search=IMAPOperationDecision.allow,
                move=False,
            ),
            folders={
                "INBOX": IMAPFolderOperationPolicyConfig(
                    move=IMAPMovePolicyConfig(
                        allowed=True,
                        to_exact="Archive",
                    ),
                    system_flags=IMAPSystemFlagsPolicyConfig(
                        SEEN=IMAPFlagMode.read_write
                    ),
                    user_flags={"triaged": IMAPFlagMode.read_write},
                )
            },
        ),
    )

    inbox = resolver.resolve_folder("INBOX")
    archive = resolver.resolve_folder("Archive")

    assert inbox.policy.move.allowed is True
    assert destination_matches_move_policy(
        inbox.policy.move,
        destination_folder="Archive",
        destination_metadata=archive.metadata,
    )
    assert inbox.policy.system_flags[IMAPSystemFlag.SEEN] is IMAPFlagMode.read_write
    assert inbox.policy.user_flags["triaged"] is IMAPFlagMode.read_write


def test_system_flag_override_keeps_unspecified_defaults() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={},
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            ),
            operation_defaults=IMAPFolderPolicyDefaultsConfig(
                system_flags=replace(
                    default_imap_system_flags_policy(),
                    FLAGGED=IMAPFlagMode.hidden,
                )
            ),
            folders={
                "INBOX": IMAPFolderOperationPolicyConfig(
                    system_flags=IMAPSystemFlagsPolicyConfig(
                        SEEN=IMAPFlagMode.read_write
                    )
                )
            },
        ),
    )

    inbox = resolver.resolve_folder("INBOX")

    assert inbox.policy.system_flags[IMAPSystemFlag.SEEN] is IMAPFlagMode.read_write
    assert inbox.policy.system_flags[IMAPSystemFlag.FLAGGED] is IMAPFlagMode.hidden


def test_system_flag_override_keeps_previous_matching_override() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={},
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            ),
            folders={
                "Projects.*": IMAPFolderOperationPolicyConfig(
                    system_flags=IMAPSystemFlagsPolicyConfig(
                        SEEN=IMAPFlagMode.read_write
                    )
                ),
                "Projects.Secret": IMAPFolderOperationPolicyConfig(
                    system_flags=IMAPSystemFlagsPolicyConfig(
                        FLAGGED=IMAPFlagMode.hidden
                    )
                ),
            },
        ),
    )

    folder = resolver.resolve_folder("Projects.Secret")

    assert folder.policy.system_flags[IMAPSystemFlag.SEEN] is IMAPFlagMode.read_write
    assert folder.policy.system_flags[IMAPSystemFlag.FLAGGED] is IMAPFlagMode.hidden


def test_folder_access_validation_requires_explicit_baseline() -> None:
    with pytest.raises(ValueError, match="first rule"):
        IMAPPolicyResolver(
            folder_metadata={},
            policy=IMAPAccessPolicyConfig(
                folder_access=IMAPFolderAccessConfig(
                    rules=[IMAPFolderAccessRuleConfig(allow_exact="INBOX")]
                )
            ),
        )
