from __future__ import annotations

from dataclasses import replace
from typing import cast

from omegaconf import OmegaConf
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
    _match_metadata_pattern,
    destination_matches_move_policy,
    validate_imap_policy,
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


def test_metadata_patterns_treat_dot_as_segment_delimiter() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={
            "Archives.{range}.{year}": IMAPFolderConfig(
                description="Archived mail from {year}",
                kind=IMAPFolderKind.ARCHIVE,
            ),
            "Archives.{year}": IMAPFolderConfig(
                description="Archived mail from {year}",
                kind=IMAPFolderKind.ARCHIVE,
            ),
            "Everything.{**:suffix}": IMAPFolderConfig(
                description="Captured {suffix}",
            ),
        },
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            )
        ),
    )

    yearly = resolver.resolve_folder("Archives.2026")
    ranged = resolver.resolve_folder("Archives.2020-2029.2026")
    deeper = resolver.resolve_folder("Archives.2020-2029.Q1.2026")
    explicit_cross_dot = resolver.resolve_folder("Everything.a.b.c")

    assert yearly.metadata.description == "Archived mail from 2026"
    assert ranged.metadata.description == "Archived mail from 2026"
    assert deeper.metadata.description == ""
    assert explicit_cross_dot.metadata.description == "Captured a.b.c"


def test_metadata_patterns_support_optional_prefix_capture() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={
            "Archives.{**:prefix?}.{year}": IMAPFolderConfig(
                description="Archived mail from {year}",
                kind=IMAPFolderKind.ARCHIVE,
            ),
        },
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            )
        ),
    )

    yearly = resolver.resolve_folder("Archives.2026")
    ranged = resolver.resolve_folder("Archives.2020-2029.2026")
    deeper = resolver.resolve_folder("Archives.customer.range.2026")
    missing_year = resolver.resolve_folder("Archives")

    assert yearly.metadata.description == "Archived mail from 2026"
    assert yearly.metadata.kind is IMAPFolderKind.ARCHIVE
    assert ranged.metadata.description == "Archived mail from 2026"
    assert ranged.metadata.kind is IMAPFolderKind.ARCHIVE
    assert deeper.metadata.description == "Archived mail from 2026"
    assert deeper.metadata.kind is IMAPFolderKind.ARCHIVE
    assert missing_year.metadata.description == ""
    assert missing_year.metadata.kind is None


def test_metadata_patterns_preserve_named_captures_starting_with_p() -> None:
    assert _match_metadata_pattern(
        "Archives.{**:prefix?}.{year}",
        "Archives.customer.range.2026",
    ) == {"prefix": "customer.range", "year": "2026"}
    assert _match_metadata_pattern(
        "Archives.{**:prefix?}.{year}",
        "Archives.2026",
    ) == {"year": "2026"}


def test_metadata_patterns_support_character_classes() -> None:
    resolver = IMAPPolicyResolver(
        folder_metadata={
            "Archives.{[0-9][0-9][0-9][0-9]:year}": IMAPFolderConfig(
                description="Archived mail from {year}",
                kind=IMAPFolderKind.ARCHIVE,
            ),
        },
        policy=IMAPAccessPolicyConfig(
            folder_access=IMAPFolderAccessConfig(
                rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
            )
        ),
    )

    year = resolver.resolve_folder("Archives.2026")
    non_year = resolver.resolve_folder("Archives.abcd")

    assert year.metadata.description == "Archived mail from 2026"
    assert year.metadata.kind is IMAPFolderKind.ARCHIVE
    assert non_year.metadata.description == ""
    assert non_year.metadata.kind is None


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


def test_policy_validation_rejects_invalid_folder_system_flag_keys() -> None:
    policy = IMAPAccessPolicyConfig(
        folder_access=IMAPFolderAccessConfig(
            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
        ),
        folders={
            "INBOX": IMAPFolderOperationPolicyConfig(
                system_flags=cast(
                    IMAPSystemFlagsPolicyConfig,
                    OmegaConf.create({"seen": "read_write"}),
                )
            )
        },
    )

    with pytest.raises(
        ValueError,
        match="IMAP folder policy 'INBOX' has invalid system_flags",
    ):
        validate_imap_policy(policy)


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
