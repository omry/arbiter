from __future__ import annotations

from dataclasses import dataclass, field

from arbiter_server.services import OperationDescriptor

from .credentials import update_page
from .state import reference_key
from .transport import decode_json, failure


@dataclass(frozen=True)
class ReceiveUpdatesInput:
    account: str = field(metadata={"description": "Configured WhatsApp account."})
    offset: int | None = field(
        default=None,
        metadata={
            "description": "Exact provider cursor. Required initially and for replay.",
            "minimum": 0,
            "maximum": 2**63 - 1,
        },
    )
    replay: bool = field(
        default=False,
        metadata={"description": "Read history without replacing normal continuation."},
    )


RECEIVE_UPDATES = OperationDescriptor(
    "receive_updates",
    "Receive projected updates and commit their sources before returning. "
    "Replay requires an explicit offset and preserves normal continuation.",
    ReceiveUpdatesInput,
)


def validate_arguments(account, offset, replay) -> None:
    if not isinstance(account, str) or not account:
        raise failure("validation_error", "unknown_account")
    if offset is not None and (type(offset) is not int or not 0 <= offset < 2**63):
        raise failure("validation_error", "invalid_cursor")
    if type(replay) is not bool:
        raise failure("validation_error", "invalid_replay")
    if replay and offset is None:
        raise failure("validation_error", "cursor_required")


def _bounded(value: str, maximum: int = 512) -> str:
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("oversized field")
    return value


def project_page(
    response, requested: int, provider_id: str, media_ingress: bool
) -> tuple[list[dict], list[tuple[str, str, str, str]], int]:
    # Reuse the complete envelope and typed-payload validator used by enrollment.
    # The second bounded decode supplies contacts/statuses, which its scanner
    # deliberately does not return. Never return the unprojected provider object.
    observed_id, messages, next_offset = update_page(response, requested)
    if observed_id is None:
        return [], [], next_offset
    if observed_id != provider_id:
        raise failure("upstream_protocol_error", "identity_changed")
    try:
        value = decode_json(response.body)["entry"][0]["changes"][0]["value"]
        if any(len(value[key]) > 100 for key in ("contacts", "messages", "statuses")):
            raise ValueError("oversized page")
        contacts = []
        for contact in value["contacts"]:
            projected = {"wa_id": contact["wa_id"]}
            if "profile" in contact:
                projected["profile"] = {
                    "name": _bounded(contact["profile"]["name"], 4 * 1024 * 1024)
                }
            contacts.append(projected)
        statuses = [
            {
                "id": status["id"],
                "recipient_id": status["recipient_id"],
                "timestamp": _bounded(status["timestamp"]),
                "status": status["status"],
            }
            for status in value["statuses"]
        ]
        projected_messages = []
        sources = []
        for message in messages:
            kind = message["type"]
            projected = {
                "id": message["id"],
                "from": message["from"],
                "timestamp": _bounded(message["timestamp"]),
                "type": kind,
            }
            payload = message[kind]
            if kind == "text":
                projected[kind] = {"body": _bounded(payload["body"], 4 * 1024 * 1024)}
            elif kind == "reaction":
                projected[kind] = {
                    "message_id": payload["message_id"],
                    "emoji": _bounded(payload["emoji"], 4 * 1024 * 1024),
                }
            else:
                # Validate even withheld input, then discard every usable handle
                # and optional metadata when ingress is denied.
                fields = {"id", "mime_type", "sha256"}
                if kind in {"image", "video", "document"}:
                    fields.add("caption")
                if kind == "document":
                    fields.add("filename")
                if kind == "audio":
                    fields.add("voice")
                if kind == "sticker":
                    fields.add("animated")
                media = {key: payload[key] for key in fields if key in payload}
                for key in ("caption", "filename"):
                    if key in media:
                        _bounded(media[key], 4 * 1024 * 1024)
                projected[kind] = media if media_ingress else {"withheld": True}
            if "context" in message:
                projected["context"] = {
                    "id": message["context"]["id"],
                    "from": message["context"]["from"],
                }
            projected_messages.append(projected)
            sources.append((message["id"], message["from"], message["timestamp"], kind))
        update = {
            "object": "whatsapp_agent_platform",
            "entry": [
                {
                    "id": provider_id,
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "contacts": contacts,
                                "messages": projected_messages,
                                "statuses": statuses,
                            },
                        }
                    ],
                }
            ],
        }
        return [update], sources, next_offset
    except (KeyError, TypeError, ValueError, IndexError):
        raise failure("upstream_protocol_error", "invalid_response") from None


def check_receive(runtime, account: str, offset: int | None, replay: bool):
    validate_arguments(account, offset, replay)
    binding = runtime.authorize("receive_updates", account)
    runtime.state.poll_offset(binding.provider_id, offset, replay)
    return {"operation": "whatsapp:receive_updates", "allowed": True}


def receive_updates(runtime, account: str, offset: int | None = None, replay=False):
    validate_arguments(account, offset, replay)
    runtime.authorize("receive_updates", account)
    selected, policy = runtime.account(account)
    with runtime.state.lock(reference_key(selected.credential) + ".poll"):
        with runtime.state.lock("root-binding"):
            runtime.bindings.bound_identities()
            binding = runtime.bindings.snapshot(selected.credential)
            requested = runtime.state.poll_offset(binding.provider_id, offset, replay)
        response = runtime.client.request(
            "GET",
            "/agent/v1/updates",
            binding.token,
            query={"offset": requested, "limit": 100, "timeout": 15},
            deadline=runtime.clock() + 30,
            response_limit=4 * 1024 * 1024,
        )
        updates, sources, next_offset = project_page(
            response, requested, binding.provider_id, policy.media_ingress
        )
        runtime.state.commit_poll(
            binding.provider_id, requested, next_offset, sources, replay
        )
        return {"updates": updates, "next_offset": next_offset, "replay": replay}
