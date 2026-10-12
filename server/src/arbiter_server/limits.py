"""Named resource limits owned by core Arbiter."""

from dataclasses import dataclass

DEFAULT_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024


@dataclass
class CoreLimitsConfig:
    max_request_body_bytes: int = 1024 * 1024
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES
