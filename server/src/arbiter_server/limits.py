"""Named resource limits owned by core Arbiter."""

from dataclasses import dataclass, field
import math

DEFAULT_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_ARTIFACT_STORAGE_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_ARTIFACT_COUNT = 2000
DEFAULT_MAX_BLOCKING_WORKERS = 8


@dataclass
class NativeTransportLimitsConfig:
    max_connections: int = 16
    max_handshakes: int = 4
    handshake_timeout_seconds: float = 10.0
    max_handshake_bytes: int = 256 * 1024
    max_noise_frame_bytes: int = 65535
    max_headers_bytes: int = 32 * 1024
    max_websocket_message_bytes: int = 64 * 1024
    max_buffered_bytes: int = 256 * 1024
    max_control_frames_per_second: int = 32
    max_control_bytes_per_second: int = 4096

    def __post_init__(self) -> None:
        for name in (
            "max_connections",
            "max_handshakes",
            "max_handshake_bytes",
            "max_noise_frame_bytes",
            "max_headers_bytes",
            "max_websocket_message_bytes",
            "max_buffered_bytes",
            "max_control_frames_per_second",
            "max_control_bytes_per_second",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"native_transport.{name} must be a positive integer")
        seconds = self.handshake_timeout_seconds
        if (
            type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or seconds <= 0
            or self.max_noise_frame_bytes > 65535
        ):
            raise ValueError("Invalid native transport deadline or Noise frame limit")


@dataclass
class CoreLimitsConfig:
    max_request_body_bytes: int = 1024 * 1024
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES
    max_artifact_storage_bytes: int = DEFAULT_MAX_ARTIFACT_STORAGE_BYTES
    max_artifact_count: int = DEFAULT_MAX_ARTIFACT_COUNT
    max_blocking_workers: int = DEFAULT_MAX_BLOCKING_WORKERS
    max_pending_enrollments: int = 20
    native_transport: NativeTransportLimitsConfig = field(
        default_factory=NativeTransportLimitsConfig
    )
