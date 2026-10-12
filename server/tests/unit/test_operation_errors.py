from __future__ import annotations

import json
from typing import Any, cast

import pytest

from arbiter_server.errors import (
    OperationError,
    PluginPolicyError,
    PluginValidationError,
)


def test_domain_code_requires_plugin_and_generic_codes_are_validated():
    with pytest.raises(ValueError, match="domain_code requires"):
        OperationError("plugin_error", "safe", domain_code="failure")
    with pytest.raises(ValueError, match="plugin requires domain_code"):
        OperationError("plugin_error", "safe", plugin="imap")
    with pytest.raises(ValueError, match="unknown generic"):
        OperationError("smtp_error", "safe")


def test_operation_error_supports_server_plugin_and_normalized_shapes():
    server_error = OperationError("internal_error", "safe")
    assert server_error.to_dict()["error"] == {
        "code": "internal_error",
        "message": "safe",
        "retry": {"transient": "unknown", "replay": "unknown"},
    }

    plugin_error = OperationError(
        "plugin_error",
        "safe",
        plugin="imap",
        domain_code="connection_failed",
    )
    assert plugin_error.to_dict()["error"]["plugin"] == "imap"
    assert plugin_error.to_dict()["error"]["domain_code"] == "connection_failed"

    normalized_error = OperationError(
        "internal_error",
        "safe",
        plugin="imap",
    )
    assert normalized_error.to_dict()["error"] == {
        "code": "internal_error",
        "message": "safe",
        "plugin": "imap",
        "retry": {"transient": "unknown", "replay": "unknown"},
    }


def test_public_validation_never_serializes_private_diagnostic():
    error = PluginValidationError("imap", "invalid message: secret-body")
    assert "secret-body" in str(error)  # Compatibility for direct Python callers.
    assert "secret-body" not in str(error.to_dict())
    assert error.to_dict()["error"]["retry"] == {"transient": "no", "replay": "safe"}


def test_operation_error_surrogates_have_json_wire_representation():
    surrogate = "\ud800"
    error = OperationError(
        "plugin_error",
        f"Public café {surrogate}",
        plugin=f"echo-{surrogate}",
        domain_code=f"remote-{surrogate}",
        details={"summary": f"Details café {surrogate}"},
    )

    wire = json.dumps(
        error.to_dict(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    assert r"\ud800" in wire
    assert r"\u00e9" in wire


@pytest.mark.parametrize(
    ("code", "transient"),
    [("policy_denied", "no"), ("rate_limited", "yes")],
)
def test_plugin_policy_error_supports_only_policy_classifications(code, transient):
    error = PluginPolicyError(
        "imap",
        "policy diagnostic",
        domain_code="quota_exceeded",
        code=code,
    )
    assert error.to_dict()["error"] == {
        "code": code,
        "message": "Configured plugin policy prevents this operation.",
        "plugin": "imap",
        "domain_code": "quota_exceeded",
        "retry": {"transient": transient, "replay": "safe"},
    }


def test_specialized_error_helpers_reject_unrelated_generic_codes():
    with pytest.raises(ValueError, match="PluginPolicyError supports"):
        PluginPolicyError(
            "imap",
            "policy diagnostic",
            domain_code="quota_exceeded",
            code=cast(Any, "upstream_timeout"),
        )
    with pytest.raises(ValueError, match="PluginValidationError supports"):
        PluginValidationError(
            "imap",
            "validation diagnostic",
            code=cast(Any, "upstream_timeout"),
        )


@pytest.mark.parametrize(
    ("code", "message", "retry"),
    [
        (
            "validation_error",
            "Invalid plugin operation arguments.",
            {"transient": "no", "replay": "safe"},
        ),
        (
            "conflict",
            "Operation conflicts with current plugin state.",
            {"transient": "unknown", "replay": "unknown"},
        ),
        (
            "not_found",
            "Requested plugin resource was not found.",
            {"transient": "no", "replay": "safe"},
        ),
    ],
)
def test_plugin_validation_error_preserves_supported_variants(code, message, retry):
    error = PluginValidationError("imap", "validation diagnostic", code=code)
    assert error.to_dict()["error"] == {
        "code": code,
        "message": message,
        "plugin": "imap",
        "domain_code": "invalid_arguments",
        "retry": retry,
    }
