from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import warnings

from PIL import Image, UnidentifiedImageError

from arbiter_server.artifacts import ArtifactError, PluginArtifactStore
from arbiter_server.errors import OperationError
from arbiter_server.services import OperationDescriptor

from .transport import MediaUpload, check_response, decode_json, failure


# Decimal provider limits. The artifact store also applies its configured cap.
MIME_LIMITS = {
    "image/jpeg": 5_000_000,
    "image/png": 5_000_000,
    "image/webp": 500_000,
    **dict.fromkeys(
        (
            "video/mp4",
            "video/3gpp",
            "audio/aac",
            "audio/mp4",
            "audio/mpeg",
            "audio/amr",
            "audio/ogg",
            "audio/opus",
            "application/pdf",
            "text/plain",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/octet-stream",
        ),
        16_000_000,
    ),
}


@dataclass(frozen=True)
class ArtifactInput:
    id: str
    url: str


@dataclass(frozen=True)
class UploadMediaInput:
    account: str
    content: ArtifactInput
    media_type: str


UPLOAD_MEDIA = OperationDescriptor(
    "upload_media",
    "Consume an existing Arbiter artifact and upload it once.",
    UploadMediaInput,
)


def check_upload(runtime, account, content, media_type):
    if not isinstance(account, str) or not account:
        raise failure("validation_error", "unknown_account")
    if isinstance(content, ArtifactInput):
        content = {"id": content.id, "url": content.url}
    if (
        not isinstance(content, dict)
        or set(content) != {"id", "url"}
        or not all(isinstance(content[k], str) and content[k] for k in content)
        or not isinstance(media_type, str)
        or media_type not in MIME_LIMITS
    ):
        raise failure("validation_error", "invalid_media")
    binding = runtime.authorize("upload_media", account)
    if not isinstance(runtime.artifacts, PluginArtifactStore):
        raise failure("plugin_error", "artifact_store_unavailable")
    return binding, content


def _webp_dimensions(content: bytes) -> tuple[int, int]:
    # Pillow's WebP Image.open constructs a native canvas decoder. Check the
    # container's first-chunk dimensions before that allocation, without
    # altering global Pillow limits. Pillow still validates the full structure.
    header = content[:30]
    if len(header) < 30 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        raise ValueError
    chunk = header[12:16]
    if chunk == b"VP8X":
        return (
            int.from_bytes(header[24:27], "little") + 1,
            int.from_bytes(header[27:30], "little") + 1,
        )
    if chunk == b"VP8L" and header[20] == 0x2F:
        bits = int.from_bytes(header[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and header[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(header[26:28], "little") & 0x3FFF,
            int.from_bytes(header[28:30], "little") & 0x3FFF,
        )
    raise ValueError


def validate_image(content, media_type):
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        return
    expected = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[
        media_type
    ]
    try:
        if media_type == "image/webp":
            width, height = _webp_dimensions(content)
            if not (1 <= width <= 4096 and 1 <= height <= 4096):
                raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # Header/structural validation only: do not allocate decoded pixels or
            # change Pillow's process-wide decompression-bomb configuration.
            with Image.open(BytesIO(content), formats=[expected]) as image:
                width, height = image.size
                if width < 1 or height < 1 or image.mode not in {"RGB", "RGBA"}:
                    raise ValueError
                if media_type == "image/webp":
                    if width > 4096 or height > 4096:
                        raise ValueError
                elif width * height > 25_000_000:
                    raise ValueError
                if media_type == "image/png" and content[24] != 8:
                    raise ValueError
                image.verify()
    except (
        OSError,
        ValueError,
        SyntaxError,
        IndexError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise failure("validation_error", "invalid_media_content") from None


def upload_media(runtime, account, content, media_type):
    binding, content = check_upload(runtime, account, content, media_type)
    try:
        artifact = runtime.artifacts.read_once(
            content["id"],
            content["url"],
            allowed_content_types={media_type},
            max_bytes=MIME_LIMITS[media_type],
        )
    except ArtifactError:
        raise failure("validation_error", "artifact_unavailable") from None
    except OSError:
        raise failure("plugin_error", "artifact_unavailable") from None
    # From here on, the input capability has been consumed even if no request
    # is submitted. Replay requires a fresh artifact, never a hidden retry.
    try:
        validate_image(artifact.content, media_type)
        response = runtime.client.request(
            "POST",
            "/agent/v1/media",
            binding.token,
            query={},
            multipart=MediaUpload(artifact.content, media_type),
            deadline=runtime.clock() + 30,
            response_limit=16384,
        )
        check_response(response)
        media_id = decode_json(response.body).get("id")
        if (
            response.status != 200
            or not isinstance(media_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", media_id) is None
        ):
            raise failure("upstream_protocol_error", "invalid_media_response")
        return {"media_id": media_id}
    except OperationError as error:
        error.replay = "unsafe"
        raise
