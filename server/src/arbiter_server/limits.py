"""Named resource limits owned by core Arbiter."""

from dataclasses import dataclass


@dataclass
class CoreLimitsConfig:
    max_request_body_bytes: int = 1024 * 1024
