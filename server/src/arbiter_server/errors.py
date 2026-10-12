"""Public operation errors. Plugins choose classifications; HTTP only renders them."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import json
import traceback
from typing import Literal


ERROR_STATUS = {
    "validation_error": 422,
    "not_found": 404,
    "policy_denied": 403,
    "rate_limited": 429,
    "conflict": 409,
    "upstream_auth_failed": 500,
    "upstream_timeout": 504,
    "upstream_unavailable": 503,
    "upstream_rejected": 422,
    "upstream_protocol_error": 502,
    "partial_failure": 500,
    "plugin_error": 500,
    "internal_error": 500,
}
Transient = Literal["yes", "no", "unknown"]
Replay = Literal["safe", "unsafe", "unknown", "same_idempotency_key"]


class OperationError(Exception):
    """An explicitly classified error with deliberately public-safe fields."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        plugin: str | None = None,
        domain_code: str | None = None,
        details: Mapping[str, object] | None = None,
        transient: Transient = "unknown",
        replay: Replay = "unknown",
    ) -> None:
        if code not in ERROR_STATUS:
            raise ValueError("unknown generic operation error code")
        if domain_code is not None and not plugin:
            raise ValueError("domain_code requires a plugin")
        if plugin is not None and domain_code is None and code != "internal_error":
            raise ValueError("plugin requires domain_code")
        if transient not in {"yes", "no", "unknown"}:
            raise ValueError("invalid transient guidance")
        if replay not in {"safe", "unsafe", "unknown", "same_idempotency_key"}:
            raise ValueError("invalid replay guidance")
        super().__init__(message)
        self.code = code
        self.message = message
        self.plugin = plugin
        self.domain_code = domain_code
        self.details = json.loads(json.dumps(dict(details or {}), allow_nan=False))
        self.transient = transient
        self.replay = replay

    def to_dict(self) -> dict[str, dict[str, object]]:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.plugin is not None:
            error["plugin"] = self.plugin
        if self.domain_code is not None:
            error["domain_code"] = self.domain_code
        if self.details:
            error["details"] = self.details
        error["retry"] = {"transient": self.transient, "replay": self.replay}
        return {"error": error}


class PluginValidationError(OperationError, ValueError):
    """Expected validation with a private diagnostic and a safe public message."""

    def __init__(
        self,
        plugin: str,
        diagnostic: str,
        *,
        code: Literal["validation_error", "conflict", "not_found"] = "validation_error",
        domain_code: str = "invalid_arguments",
    ) -> None:
        if code not in {"validation_error", "conflict", "not_found"}:
            raise ValueError(
                "PluginValidationError supports validation_error, conflict, and not_found"
            )
        super().__init__(
            code,
            {
                "validation_error": "Invalid plugin operation arguments.",
                "conflict": "Operation conflicts with current plugin state.",
                "not_found": "Requested plugin resource was not found.",
            }[code],
            plugin=plugin,
            domain_code=domain_code,
            transient="unknown" if code == "conflict" else "no",
            replay="unknown" if code == "conflict" else "safe",
        )
        # Preserve direct Python callers' validation diagnostics. Never serialize
        # exception text; the HTTP boundary renders only the public fields.
        self.args = (diagnostic,)


class OperationValidationError(OperationError, ValueError):
    def __init__(self, diagnostic: str, *, fields: list[str] | None = None) -> None:
        super().__init__(
            "validation_error",
            "Invalid operation arguments.",
            details={"fields": fields} if fields else None,
            transient="no",
            replay="safe",
        )
        self.args = (diagnostic,)


class PluginPolicyError(OperationError, ValueError):
    def __init__(
        self,
        plugin: str,
        diagnostic: str,
        *,
        domain_code: str,
        code: Literal["policy_denied", "rate_limited"] = "policy_denied",
    ) -> None:
        if code not in {"policy_denied", "rate_limited"}:
            raise ValueError(
                "PluginPolicyError supports policy_denied and rate_limited"
            )
        super().__init__(
            code,
            "Configured plugin policy prevents this operation.",
            plugin=plugin,
            domain_code=domain_code,
            transient="yes" if code == "rate_limited" else "no",
            replay="safe",
        )
        self.args = (diagnostic,)


def log_unexpected(logger: logging.Logger, exc: BaseException) -> None:
    """Retain stack locations without exception text, source text, or locals."""
    frames = [
        (frame.filename, frame.lineno, frame.name)
        for frame in traceback.extract_tb(exc.__traceback__)
    ]
    logger.error(
        "Operation failed unexpectedly: %s; stack=%s", type(exc).__name__, frames
    )
