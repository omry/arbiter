from __future__ import annotations

from dataclasses import dataclass

from arbiter_server.errors import OperationError
from arbiter_server.services import OperationDescriptor

from .messages import bounded_string
from .transport import check_response, decode_json, failure


@dataclass(frozen=True)
class MarkReadInput:
    account: str
    message_id: str


@dataclass(frozen=True)
class TypingInput:
    account: str
    message_id: str
    active: bool


MARK_READ = OperationDescriptor(
    "mark_read", "Mark a provider message as read.", MarkReadInput
)
TYPING = OperationDescriptor(
    "typing",
    "Start typing and mark the message read. Explicit cancellation is unsupported.",
    TypingInput,
)


def check_status(runtime, operation, account, message_id, active=None):
    if operation not in {"mark_read", "typing"}:
        raise failure("validation_error", "unknown_operation")
    if not isinstance(account, str) or not account:
        raise failure("validation_error", "unknown_account")
    bounded_string(message_id, 512)
    if operation == "typing" and type(active) is not bool:
        raise failure("validation_error", "invalid_typing_state")
    binding = runtime.authorize(operation, account)
    if operation == "typing" and not active:
        raise failure("upstream_rejected", "typing_reset_unsupported")
    return binding


def submit_status(runtime, operation, account, message_id, active=None):
    binding = check_status(runtime, operation, account, message_id, active)
    payload: dict[str, object] = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    if operation == "typing":
        payload["typing_indicator"] = {"type": "text"}
    response = runtime.client.request(
        "POST",
        "/agent/v1/statuses",
        binding.token,
        query={},
        json_body=payload,
        deadline=runtime.clock() + 30,
        response_limit=16384,
    )
    try:
        check_response(response)
        if (
            response.status != 200
            or decode_json(response.body).get("success") is not True
        ):
            raise failure("upstream_protocol_error", "invalid_status_response")
    except OperationError as error:
        # A received response does not prove that a rejected/malformed status
        # acknowledgement left the provider without an effect.
        error.replay = "unsafe"
        raise
    return {"status": "accepted"}
