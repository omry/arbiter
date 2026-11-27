from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import unicodedata

from arbiter_server.errors import OperationError
from arbiter_server.services import OperationDescriptor

from .state import reference_key
from .transport import WriteNotSubmitted, decode_json, failure


@dataclass(frozen=True)
class Text:
    body: str
    preview_url: bool = False


@dataclass(frozen=True)
class QuoteContext:
    message_id: str


@dataclass(frozen=True)
class Message:
    type: str
    text: Text | None = None
    context: QuoteContext | None = None


@dataclass(frozen=True)
class SendMessageInput:
    account: str
    participant_id: str
    message: Message
    reply_to_message_id: str | None = None


SEND_MESSAGE = OperationDescriptor(
    "send_message",
    "Send a text message. Automation must supply an observed reply_to_message_id. "
    "Identical keyed calls replay the stored outcome; unknown delivery must not be resent.",
    SendMessageInput,
)


def bounded_string(value, maximum, *, controls=False):
    try:
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > maximum
            or (not controls and any(unicodedata.category(c) == "Cc" for c in value))
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise failure("validation_error", "invalid_message") from None
    return value


def payload_for(participant_id, message):
    participant_id = bounded_string(participant_id, 256)
    if not participant_id.startswith("user:") or participant_id == "user:":
        raise failure("validation_error", "invalid_participant")
    if not isinstance(message, Mapping) or set(message) - {"type", "text", "context"}:
        raise failure("validation_error", "invalid_message")
    text = message.get("text")
    if (
        message.get("type") != "text"
        or not isinstance(text, Mapping)
        or set(text) - {"body", "preview_url"}
    ):
        raise failure("validation_error", "invalid_message")
    body = bounded_string(text.get("body"), 4096 * 4, controls=True)
    preview = text.get("preview_url", False)
    if len(body) > 4096 or type(preview) is not bool:
        raise failure("validation_error", "invalid_message")
    payload = {
        "messaging_product": "whatsapp",
        "to": participant_id,
        "type": "text",
        "text": {"body": body, "preview_url": preview},
    }
    context = message.get("context")
    if context is not None:
        if not isinstance(context, Mapping) or set(context) != {"message_id"}:
            raise failure("validation_error", "invalid_message")
        payload["context"] = {"message_id": bounded_string(context["message_id"], 512)}
    return payload


def check_send(runtime, account, participant_id, message, reply_to_message_id=None):
    if not isinstance(account, str) or not account:
        raise failure("validation_error", "unknown_account")
    payload = payload_for(participant_id, message)
    if reply_to_message_id is not None:
        bounded_string(reply_to_message_id, 512)
    binding = runtime.authorize("send_message", account)
    if reply_to_message_id is not None:
        with runtime.state.database() as db:
            source = db.execute(
                "SELECT participant FROM sources WHERE provider_id=? AND message_id=?",
                (binding.provider_id, reply_to_message_id),
            ).fetchone()
        if source != (participant_id,):
            raise failure("validation_error", "unknown_reply_source")
    return payload, binding


def accepted_result(response, participant):
    try:
        data = decode_json(response.body)
        if (
            data.get("messaging_product") != "whatsapp"
            or not isinstance(data.get("contacts"), list)
            or len(data["contacts"]) != 1
            or not isinstance(data.get("messages"), list)
            or len(data["messages"]) != 1
            or data["contacts"][0]["input"] != participant
            or data["contacts"][0]["wa_id"] != participant
        ):
            raise ValueError
        message_id = bounded_string(data["messages"][0]["id"], 512)
        return {"delivery": "accepted", "provider_message_id": message_id}
    except (OperationError, KeyError, TypeError, ValueError, IndexError):
        return None


def _provider_error_code(response):
    try:
        error = decode_json(response.body).get("error")
    except OperationError:
        return None
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    if type(code) is int and code in {100, 131005, 131016}:
        return code
    return None


def refused_result(response):
    if 400 <= response.status < 500:
        if response.status == 401:
            return failure("upstream_auth_failed", "credential_rejected").to_dict()[
                "error"
            ]
        provider_code = _provider_error_code(response)
        if response.status == 400 and provider_code == 100:
            return failure("upstream_auth_failed", "credential_rejected").to_dict()[
                "error"
            ]
        if response.status == 403 and provider_code == 131005:
            return failure("upstream_rejected", "message_refused").to_dict()["error"]
        if response.status == 429:
            return failure("rate_limited", "provider_rate_limited").to_dict()["error"]
        return failure("upstream_rejected", "message_refused").to_dict()["error"]
    if response.status == 503:
        if _provider_error_code(response) == 131016:
            return failure(
                "upstream_unavailable", "message_refused", transient="yes"
            ).to_dict()["error"]
    return None


def replay_result(state, result):
    if state == "refused":
        if not isinstance(result, dict):
            raise failure("plugin_error", "private_state_unavailable")
        raise OperationError(
            result["code"],
            result["message"],
            plugin="whatsapp",
            domain_code=result["domain_code"],
            transient=result["retry"]["transient"],
            replay=result["retry"]["replay"],
        )
    if state == "accepted":
        if not isinstance(result, dict) or result.get("delivery") != "accepted":
            raise failure("plugin_error", "private_state_unavailable")
        return {**result, "idempotency_replayed": True}
    return {
        "delivery": "unknown",
        "provider_message_id": None,
        "idempotency_replayed": True,
    }


def send_message(runtime, account, participant_id, message, reply_to_message_id=None):
    payload, binding = check_send(
        runtime, account, participant_id, message, reply_to_message_id
    )
    selected, _ = runtime.account(account)
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    keyed = reply_to_message_id is not None
    try:
        with runtime.state.lock(reference_key(selected.credential) + ".send"):
            if keyed:
                previous = runtime.state.reserve_reply(
                    binding.provider_id, reply_to_message_id, participant_id, digest
                )
                if previous is not None:
                    return replay_result(*previous)
            outcome = {"delivery": "unknown", "provider_message_id": None}
            state = "unknown"
            refused = None
            try:
                response = runtime.client.request(
                    "POST",
                    "/agent/v1/messages",
                    binding.token,
                    query={},
                    json_body=payload,
                    deadline=runtime.clock() + 30,
                    response_limit=4 * 1024 * 1024,
                )
            except WriteNotSubmitted:
                if keyed:
                    runtime.state.finish_reply(
                        binding.provider_id, reply_to_message_id, None, None
                    )
                raise
            except OperationError:
                # Any transport failure without proof of non-submission is uncertain.
                pass
            else:
                if 200 <= response.status < 300:
                    accepted = accepted_result(response, participant_id)
                    if accepted is not None:
                        outcome, state = accepted, "accepted"
                else:
                    refused = refused_result(response)
                    if refused is not None:
                        state = "refused"
            if keyed:
                try:
                    runtime.state.finish_reply(
                        binding.provider_id,
                        reply_to_message_id,
                        state,
                        refused if refused is not None else outcome,
                    )
                except OperationError:
                    if state == "accepted":
                        raise OperationError(
                            "partial_failure",
                            "WhatsApp accepted the message, but its result could not be recorded.",
                            plugin="whatsapp",
                            domain_code="reply_result_not_recorded",
                            details=outcome,
                            transient="no",
                            replay="unsafe",
                        ) from None
                    raise failure(
                        "plugin_error", "reply_result_not_recorded", replay="unsafe"
                    ) from None
            if refused is not None:
                return replay_result("refused", refused)
            return {**outcome, "idempotency_replayed": False}
    except OperationError as error:
        if error.domain_code == "enrollment_in_progress":
            raise failure("conflict", "reply_in_progress", transient="yes") from None
        raise
