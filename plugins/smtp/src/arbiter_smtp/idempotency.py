from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
from pathlib import Path

from diskcache import Cache  # type: ignore[import-untyped]


@dataclass(frozen=True)
class SMTPIdempotencyResult:
    message_id: str
    recipient_count: int
    sent_copy: dict[str, object] | None = None
    sent_copy_message_bytes: bytes | None = None


@dataclass(frozen=True)
class SMTPIdempotencyRecord:
    payload_hash: str
    result: SMTPIdempotencyResult | None = None


class SMTPIdempotencyStore:
    def __init__(self, cache_dir: str) -> None:
        self._cache = Cache(str(Path(cache_dir)))

    def get(self, key: str) -> SMTPIdempotencyRecord | None:
        raw_record = self._cache.get(key)
        if raw_record is None:
            return None
        if not isinstance(raw_record, str):
            raise ValueError("SMTP idempotency cache contains an invalid record")
        return _decode_record(raw_record)

    def add_pending(
        self,
        key: str,
        *,
        payload_hash: str,
        expire_seconds: int,
    ) -> bool:
        record = SMTPIdempotencyRecord(payload_hash=payload_hash)
        return bool(self._cache.add(key, _encode_record(record), expire=expire_seconds))

    def store_success(
        self,
        key: str,
        *,
        payload_hash: str,
        result: SMTPIdempotencyResult,
        expire_seconds: int,
    ) -> None:
        record = SMTPIdempotencyRecord(payload_hash=payload_hash, result=result)
        self._cache.set(key, _encode_record(record), expire=expire_seconds)

    def delete(self, key: str) -> None:
        self._cache.delete(key)


def _encode_record(record: SMTPIdempotencyRecord) -> str:
    return json.dumps(
        {
            "payload_hash": record.payload_hash,
            "result": (
                None
                if record.result is None
                else {
                    "message_id": record.result.message_id,
                    "recipient_count": record.result.recipient_count,
                    "sent_copy": record.result.sent_copy,
                    "sent_copy_message": (
                        None
                        if record.result.sent_copy_message_bytes is None
                        else base64.b64encode(
                            record.result.sent_copy_message_bytes
                        ).decode("ascii")
                    ),
                }
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_record(raw_record: str) -> SMTPIdempotencyRecord:
    try:
        data = json.loads(raw_record)
        payload_hash = data["payload_hash"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("SMTP idempotency cache contains an invalid record") from exc
    if not isinstance(payload_hash, str):
        raise ValueError("SMTP idempotency cache contains an invalid payload hash")

    raw_result = data.get("result")
    if raw_result is None:
        return SMTPIdempotencyRecord(payload_hash=payload_hash)
    if not isinstance(raw_result, dict):
        raise ValueError("SMTP idempotency cache contains an invalid result")
    message_id = raw_result.get("message_id")
    recipient_count = raw_result.get("recipient_count")
    sent_copy = raw_result.get("sent_copy")
    sent_copy_message = raw_result.get("sent_copy_message")
    if not isinstance(message_id, str) or not isinstance(recipient_count, int):
        raise ValueError("SMTP idempotency cache contains an invalid result")
    if sent_copy is not None and not isinstance(sent_copy, dict):
        raise ValueError("SMTP idempotency cache contains an invalid sent_copy result")
    if sent_copy_message is not None and not isinstance(sent_copy_message, str):
        raise ValueError("SMTP idempotency cache contains an invalid sent_copy message")
    sent_copy_message_bytes: bytes | None = None
    if sent_copy_message is not None:
        try:
            sent_copy_message_bytes = base64.b64decode(
                sent_copy_message,
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "SMTP idempotency cache contains an invalid sent_copy message"
            ) from exc
    return SMTPIdempotencyRecord(
        payload_hash=payload_hash,
        result=SMTPIdempotencyResult(
            message_id=message_id,
            recipient_count=recipient_count,
            sent_copy=sent_copy,
            sent_copy_message_bytes=sent_copy_message_bytes,
        ),
    )
