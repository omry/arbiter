from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hmac
import json
import os
import re
import secrets
from time import monotonic, sleep

from arbiter_server.authority import _publish
from arbiter_server.errors import OperationError

from .state import StateStore, check_private, reference_key
from .transport import ProviderClient, check_response, decode_json, failure, token_text


@dataclass(frozen=True)
class Binding:
    schema_version: int
    store_uuid: str
    provider_id: str = field(repr=False)
    token: str = field(repr=False)


def identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 512
        or any(ord(c) < 32 for c in value)
    ):
        raise failure("upstream_protocol_error", "invalid_identity")
    return value


def participant(value: object, *, quoted: bool = False) -> str:
    prefixes = ("user:", "agent:") if quoted else ("user:",)
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 256
        or not any(value.startswith(p) and len(value) > len(p) for p in prefixes)
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise ValueError("invalid participant")
    return value


def _timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("invalid timestamp")


def _validate_update_value(value: dict) -> None:
    payloads = {"text", "image", "audio", "video", "document", "sticker", "reaction"}
    for contact in value["contacts"]:
        participant(contact["wa_id"])
        if "profile" in contact:
            if not isinstance(contact["profile"], dict) or not isinstance(
                contact["profile"]["name"], str
            ):
                raise ValueError("invalid contact profile")
    for status in value["statuses"]:
        identity(status["id"])
        participant(status["recipient_id"])
        _timestamp(status["timestamp"])
        if status["status"] not in ("delivered", "read"):
            raise ValueError("unsupported status")
    for message in value["messages"]:
        identity(message["id"])
        participant(message["from"])
        _timestamp(message["timestamp"])
        kind = message["type"]
        if kind not in payloads or payloads.intersection(message) != {kind}:
            raise ValueError("invalid message payload selection")
        payload = message[kind]
        if not isinstance(payload, dict):
            raise ValueError("invalid message payload")
        if "context" in message:
            context = message["context"]
            if not isinstance(context, dict):
                raise ValueError("invalid quoted context")
            identity(context["id"])
            participant(context["from"], quoted=True)
        if kind == "text":
            if not isinstance(payload["body"], str):
                raise ValueError("invalid text")
        elif kind == "reaction":
            identity(payload["message_id"])
            if not isinstance(payload["emoji"], str):
                raise ValueError("invalid reaction")
        else:
            identity(payload["id"])
            identity(payload["mime_type"])
            digest = payload["sha256"]
            if (
                not isinstance(digest, str)
                or len(base64.b64decode(digest, validate=True)) != 32
            ):
                raise ValueError("invalid media digest")
            for key in ("caption", "filename"):
                if key in payload and not isinstance(payload[key], str):
                    raise ValueError("invalid media metadata")
            for key in ("voice", "animated"):
                if key in payload and type(payload[key]) is not bool:
                    raise ValueError("invalid media flag")
            if kind == "sticker" and payload.get("animated") is not False:
                raise ValueError("unsupported incoming sticker")


class BindingStore:
    def __init__(self, state: StateStore):
        self.state = state

    def snapshot(self, reference: str) -> Binding:
        directory = self.state.directory("bindings")
        path = directory / (reference_key(reference) + ".json")
        if not path.exists() and not path.is_symlink():
            raise failure("plugin_error", "account_not_enrolled") from None
        return self._read_record(path)

    def optional(self, reference: str) -> Binding | None:
        directory = self.state.directory("bindings")
        path = directory / (reference_key(reference) + ".json")
        # A dangling symlink is unsafe, not an absent binding.
        if not path.exists() and not path.is_symlink():
            return None
        return self.snapshot(reference)

    def bound_identities(self) -> dict[str, str]:
        result: dict[str, str] = {}
        directory = self.state.directory("bindings")
        # Records are hashed references. Read them with the exact same validator.
        for path in directory.iterdir():
            if re.fullmatch(r"\.pending-[a-f0-9]{32}", path.name):
                self._read_record(path)
                continue
            if not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                raise failure("plugin_error", "binding_unavailable")
            binding = self._read_record(path)
            if binding.provider_id in result:
                raise failure("plugin_error", "duplicate_provider_identity")
            result[binding.provider_id] = path.name
        return result

    def _read_record(self, path) -> Binding:
        try:
            check_private(path)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                stat, named = os.fstat(handle.fileno()), path.lstat()
                if (stat.st_dev, stat.st_ino) != (named.st_dev, named.st_ino):
                    raise ValueError("binding identity drift")
                data = handle.read(16385)
            if len(data) > 16384:
                raise ValueError("oversized binding")
            record = decode_json(data)
            if set(record) != {"schema_version", "store_uuid", "provider_id", "token"}:
                raise ValueError("invalid binding")
            if (
                type(record["schema_version"]) is not int
                or record["schema_version"] != 1
            ):
                raise ValueError("binding version")
            result = Binding(**record)
            identity(result.provider_id)
            token_text(result.token)
            with self.state.database() as db:
                if (
                    result.store_uuid
                    != db.execute("SELECT store_uuid FROM store").fetchone()[0]
                ):
                    raise ValueError("binding store mismatch")
                if (
                    db.execute(
                        "SELECT 1 FROM agents WHERE provider_id=?",
                        (result.provider_id,),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("missing agent")
            return result
        except (OSError, ValueError, OperationError):
            raise failure("plugin_error", "binding_unavailable") from None

    def publish(
        self, reference: str, provider_id: str, token: str, *, previous: Binding | None
    ) -> Binding:
        with self.state.lock("root-binding"):
            current = self.optional(reference)
            if current != previous:
                raise failure("conflict", "binding_changed")
            occupied = self.bound_identities().get(provider_id)
            filename = reference_key(reference) + ".json"
            if occupied is not None and occupied != filename:
                raise failure("conflict", "provider_already_bound")
            with self.state.database() as db:
                store_uuid = db.execute("SELECT store_uuid FROM store").fetchone()[0]
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR IGNORE INTO agents(provider_id) VALUES(?)",
                    (provider_id,),
                )
                db.commit()
            binding = Binding(1, store_uuid, identity(provider_id), token_text(token))
            data = json.dumps(
                {
                    "schema_version": 1,
                    "store_uuid": store_uuid,
                    "provider_id": provider_id,
                    "token": token,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            if len(data) > 16384:
                raise failure("validation_error", "invalid_credential")
            directory = self.state.directory("bindings")
            pending = directory / (".pending-" + secrets.token_hex(16))
            try:
                fd = os.open(
                    pending,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(fd, "wb") as handle:
                    check_private(pending)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Validate the complete protected record before its activation point.
                if self._read_record(pending) != binding:
                    raise ValueError("publication validation failed")
                _publish(pending, directory / filename)
                return self.snapshot(reference)
            except (OSError, ValueError):
                # Leave possible publication/temp evidence for operator inspection.
                raise failure("plugin_error", "binding_publication_uncertain") from None


def update_page(response, offset: int) -> tuple[str | None, list[dict], int]:
    check_response(response)
    if response.status == 204:
        if response.body:
            raise failure("upstream_protocol_error", "invalid_response")
        return None, [], offset
    try:
        data = decode_json(response.body)
        if data.get("object") != "whatsapp_agent_platform":
            raise ValueError
        entries = data["entry"]
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError
        provider_id = identity(entries[0]["id"])
        changes = entries[0]["changes"]
        if (
            not isinstance(changes, list)
            or len(changes) != 1
            or changes[0]["field"] != "messages"
        ):
            raise ValueError
        value = changes[0]["value"]
        if value["messaging_product"] != "whatsapp":
            raise ValueError
        for key in ("contacts", "messages", "statuses"):
            if not isinstance(value[key], list) or any(
                not isinstance(v, dict) for v in value[key]
            ):
                raise ValueError
        next_offset = data["next_offset"]
        if type(next_offset) is not int or not offset < next_offset < 2**63:
            raise ValueError
        _validate_update_value(value)
        return provider_id, value["messages"], next_offset
    except (KeyError, TypeError, ValueError, IndexError):
        raise failure("upstream_protocol_error", "invalid_response") from None


class AdministrativePoller:
    """One owner for all request starts, pacing and retry budgets."""

    def __init__(
        self, client: ProviderClient, token: str, *, clock=monotonic, wait=sleep
    ):
        self.client, self.token = client, token
        self.clock, self.wait = clock, wait
        self.next_start = 0.0
        self.backoff = 0
        self.starts = 0

    def page(self, offset: int, *, timeout: int, end: float, maximum: int):
        while True:
            delay = max(0.0, self.next_start - self.clock())
            if self.starts >= maximum or self.clock() + delay >= end:
                raise failure("upstream_unavailable", "enrollment_budget_exhausted")
            if delay:
                self.wait(delay)
            if self.starts >= maximum or self.clock() >= end:
                raise failure("upstream_unavailable", "enrollment_budget_exhausted")
            started = self.clock()
            self.starts += 1
            self.next_start = started + 4.1
            response = self.client.request(
                "GET",
                "/agent/v1/updates",
                self.token,
                query={"offset": offset, "limit": 100, "timeout": timeout},
                deadline=min(end, started + 30),
                response_limit=4 * 1024 * 1024,
            )
            if self.clock() >= end:
                raise failure("upstream_unavailable", "enrollment_budget_exhausted")
            if response.status != 429:
                self.backoff = 0
                return update_page(response, offset)
            pause = (4.1, 8.2, 16.4, 32.8, 60)[min(self.backoff, 4)]
            self.backoff += 1
            self.next_start = max(self.next_start, self.clock() + pause)


class AccountAdministrator:
    def __init__(
        self,
        store: BindingStore,
        client: ProviderClient,
        *,
        clock=monotonic,
        wait=sleep,
        challenge_factory=None,
    ):
        self.store, self.client = store, client
        self.clock, self.wait = clock, wait
        self.challenge_factory = challenge_factory or (lambda: secrets.token_hex(16))

    def run(
        self, action: str, reference: str, secret_input, challenge_output
    ) -> dict[str, str]:
        if action not in {"enroll", "replace"}:
            raise failure("validation_error", "unsupported_administration")
        state = self.store.state
        # Create only protected directories. Existing unsafe objects are rejected.
        state.root(create=True)
        state.directory("bindings", create=True)
        state.directory("locks", create=True)
        with state.lock(reference_key(reference) + ".poll"):
            with state.lock("root-binding"):
                with state.database(initialize=True):
                    pass
                previous = self.store.optional(reference)
                self.store.bound_identities()
                if action == "enroll" and previous is not None:
                    return {"status": "ready", "action": action}
                if action == "replace" and previous is None:
                    raise failure("plugin_error", "account_not_enrolled")
            candidate_token = token_text(secret_input())
            poller = AdministrativePoller(
                self.client, candidate_token, clock=self.clock, wait=self.wait
            )
            offset = 0
            candidate_id: str | None = None
            end = self.clock() + 1500
            while True:
                provider_id, _, next_offset = poller.page(
                    offset, timeout=0, end=end, maximum=300
                )
                if provider_id is not None:
                    if candidate_id is not None and provider_id != candidate_id:
                        raise failure("upstream_protocol_error", "identity_changed")
                    candidate_id = provider_id
                    with state.lock("root-binding"):
                        bound = self.store.bound_identities().get(provider_id)
                    if bound is not None and (
                        previous is None or previous.provider_id != provider_id
                    ):
                        raise failure("conflict", "provider_already_bound")
                    if previous is not None and previous.provider_id != provider_id:
                        raise failure(
                            "upstream_rejected", "replacement_identity_mismatch"
                        )
                if provider_id is None:
                    break
                offset = next_offset
            if action == "replace":
                if previous is None or candidate_id != previous.provider_id:
                    raise failure("upstream_rejected", "replacement_identity_unproved")
            else:
                challenge = "Arbiter enrollment " + self.challenge_factory()
                challenge_output(challenge)
                poller.starts = 0
                end = self.clock() + 180
                while True:
                    provider_id, messages, next_offset = poller.page(
                        offset, timeout=15, end=end, maximum=180
                    )
                    if provider_id is not None:
                        if candidate_id is not None and candidate_id != provider_id:
                            raise failure("upstream_protocol_error", "identity_changed")
                        candidate_id = provider_id
                        with state.lock("root-binding"):
                            if provider_id in self.store.bound_identities():
                                raise failure("conflict", "provider_already_bound")
                        if any(
                            m["type"] == "text"
                            and hmac.compare_digest(
                                m["text"]["body"].encode("utf-8"),
                                challenge.encode("utf-8"),
                            )
                            for m in messages
                        ):
                            break
                    offset = next_offset
            if candidate_id is None:
                raise failure("upstream_protocol_error", "identity_unproved")
            self.store.publish(
                reference, candidate_id, candidate_token, previous=previous
            )
            return {"status": "ready", "action": action}
