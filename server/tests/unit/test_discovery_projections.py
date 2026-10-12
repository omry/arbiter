import json
from omegaconf import OmegaConf
import pytest

from arbiter_imap import IMAPServicePlugin
from arbiter_imap.config import (
    IMAPConfig,
    IMAPAccessPolicyConfig,
    IMAPFolderOperationPolicyConfig,
    IMAPSystemFlagsPolicyConfig,
    IMAPFlagMode,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
)
from arbiter_smtp import SMTPServicePlugin
from arbiter_smtp.config import SMTPConfig, SMTPServicePolicyConfig
from arbiter_server.main import _jsonable
from arbiter_imap.policy import validate_imap_policy


@pytest.mark.parametrize(
    "plugin,account,policy,account_fields,policy_fields",
    [
        (
            SMTPServicePlugin(),
            SMTPConfig(),
            SMTPServicePolicyConfig(),
            {"from_email", "from_name", "sent_copy"},
            {"limits", "idempotency", "recipient_policy", "sent_copy"},
        ),
        (
            IMAPServicePlugin(),
            IMAPConfig(),
            IMAPAccessPolicyConfig(),
            {"default_folder", "folders"},
            {"folder_access", "operation_defaults", "folders"},
        ),
    ],
)
def test_mail_projections_ignore_unknown_fields_and_keep_public_policy(
    plugin, account, policy, account_fields, policy_fields
):
    account_cfg = OmegaConf.structured(account)
    policy_cfg = OmegaConf.structured(policy)
    OmegaConf.set_struct(account_cfg, False)
    OmegaConf.set_struct(policy_cfg, False)
    account_cfg.access = "${oc.env:DBB_PRIVATE_VARIABLE_DOES_NOT_EXIST}"
    policy_cfg.access = "${oc.env:DBB_PRIVATE_VARIABLE_DOES_NOT_EXIST}"
    for name in policy_fields:
        node = policy_cfg[name]
        if OmegaConf.is_dict(node) and OmegaConf.get_type(node) is not dict:
            OmegaConf.set_struct(node, False)
            node.access = "${oc.env:DBB_PRIVATE_VARIABLE_DOES_NOT_EXIST}"

    account_projection = plugin.public_account_config(account_cfg)
    policy_projection = plugin.public_policy_config(policy_cfg)
    assert set(account_projection) == account_fields
    assert set(policy_projection) == policy_fields
    assert "access" not in str(account_projection)
    assert "access" not in str(policy_projection).replace("folder_access", "")


def test_imap_projection_filters_nested_move_fields_and_serializes_selectors():
    policy = OmegaConf.structured(IMAPAccessPolicyConfig())
    policy.operation_defaults.move = {
        "allowed": True,
        "to_exact": ["Archive"],
        "to_kind": ["TRASH"],
        "access": "${oc.env:DBB_PRIVATE_VARIABLE_DOES_NOT_EXIST}",
    }
    projection = IMAPServicePlugin().public_policy_config(policy)
    wire = json.dumps(_jsonable(projection))
    assert '"to_exact": ["Archive"]' in wire
    assert '"to_kind": ["TRASH"]' in wire
    assert "DBB_PRIVATE" not in wire
    assert '"access"' not in wire


@pytest.mark.parametrize("structured", [False, True])
def test_imap_projection_preserves_partial_flag_overrides(structured):
    policy = IMAPAccessPolicyConfig(
        folders={
            "Drafts": IMAPFolderOperationPolicyConfig(
                system_flags=IMAPSystemFlagsPolicyConfig(SEEN=IMAPFlagMode.read_write)
            )
        }
    )
    projection = IMAPServicePlugin().public_policy_config(
        OmegaConf.structured(policy) if structured else policy
    )
    wire = json.loads(json.dumps(_jsonable(projection)))
    assert wire["folders"]["Drafts"]["system_flags"] == {"SEEN": "read_write"}


@pytest.mark.parametrize(
    "move,allowed",
    [
        ("yes", True),
        (1, True),
        (0, False),
        ("", False),
        ({"to_exact": ["Archive"]}, False),
    ],
)
def test_imap_projection_matches_enforcement_for_scalar_and_mapping_moves(
    move, allowed
):
    policy = IMAPAccessPolicyConfig(
        folder_access=IMAPFolderAccessConfig(
            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
        ),
        folders={"INBOX": IMAPFolderOperationPolicyConfig(move=move)},
    )
    validate_imap_policy(policy)
    projection = IMAPServicePlugin().public_policy_config(OmegaConf.structured(policy))
    wire = json.loads(json.dumps(_jsonable(projection)))
    assert wire["folders"]["INBOX"]["move"]["allowed"] is allowed
