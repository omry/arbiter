import json
import threading
from typing import Any

import pytest

from arbiter_server.errors import OperationError
from arbiter_whatsapp.config import WhatsAppPolicy
from arbiter_whatsapp.messages import send_message
from arbiter_whatsapp.state import reference_key
from arbiter_whatsapp.transport import ProviderResponse, WriteNotSubmitted, failure
from plugins.whatsapp.tests.unit.test_updates import configured


def success(participant="user:creator", message_id="wamid.sent"):
    return ProviderResponse(
        200,
        json.dumps(
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": participant, "wa_id": participant}],
                "messages": [{"id": message_id}],
            }
        ).encode(),
    )


def observe(runtime):
    runtime.state.commit_poll(
        "provider-1", 0, 1, [("inbound-1", "user:creator", "123", "text")], False
    )


def arguments(**changes):
    return {
        "account": "first",
        "participant_id": "user:creator",
        "message": {"type": "text", "text": {"body": "Hello"}},
        "reply_to_message_id": "inbound-1",
        **changes,
    }


def invoke(catalog, **changes):
    return catalog.invoke_operation("whatsapp:send_message", arguments(**changes))


def test_catalog_send_and_canonical_replay(configured):
    runtime, provider, catalog = configured
    observe(runtime)
    provider.responses.append(success())
    first = invoke(catalog)
    assert first == {
        "delivery": "accepted",
        "provider_message_id": "wamid.sent",
        "idempotency_replayed": False,
    }
    replay = invoke(
        catalog,
        message={
            "text": {"preview_url": False, "body": "Hello"},
            "context": None,
            "type": "text",
        },
    )
    assert replay == {**first, "idempotency_replayed": True}
    assert len(provider.calls) == 1
    assert provider.calls[0][3]["json_body"] == {
        "messaging_product": "whatsapp",
        "to": "user:creator",
        "type": "text",
        "text": {"body": "Hello", "preview_url": False},
    }
    changes: list[dict[str, Any]] = [
        {"message": {"type": "text", "text": {"body": "Changed"}}},
        {"message": {"type": "text", "text": {"body": "Hello", "preview_url": True}}},
        {
            "message": {
                "type": "text",
                "text": {"body": "Hello"},
                "context": {"message_id": "quoted"},
            }
        },
    ]
    for change in changes:
        with pytest.raises(OperationError) as error:
            invoke(catalog, **change)
        assert error.value.domain_code == "reply_payload_conflict"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"participant_id": "123"},
        {"participant_id": "user:"},
        {"participant_id": "user:" + "x" * 252},
        {"participant_id": "user:a\n"},
        {"reply_to_message_id": "missing"},
        {"participant_id": "user:other"},
        {"message": {"type": "reaction", "text": {"body": "Hello"}}},
        {"message": {"type": "text", "text": {"body": ""}}},
        {"message": {"type": "text", "text": {"body": "x" * 4097}}},
        {"message": {"type": "text", "text": {"body": "hello", "unknown": "canary"}}},
        {
            "message": {
                "type": "text",
                "text": {"body": "hello"},
                "context": {"unknown": "canary"},
            }
        },
        {"message": {"type": "text", "text": {"body": "hello"}, "unknown": "canary"}},
    ],
)
def test_invalid_catalog_arguments_never_submit(configured, changes):
    runtime, provider, catalog = configured
    observe(runtime)
    with pytest.raises(OperationError):
        invoke(catalog, **changes)
    assert provider.calls == []
    with runtime.state.database() as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone() == (0,)


def test_preflight_and_denied_send_have_no_effect(configured):
    runtime, provider, catalog = configured
    observe(runtime)
    catalog.check_operation("whatsapp:send_message", arguments())
    assert provider.calls == []
    with runtime.state.database() as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone() == (0,)
    runtime.policies["default"] = WhatsAppPolicy(send_messages=False)
    for operation in [catalog.invoke_operation, catalog.check_operation]:
        with pytest.raises(OperationError) as error:
            operation("whatsapp:send_message", arguments())
        assert error.value.code == "policy_denied"
    assert provider.calls == []


@pytest.mark.parametrize(
    "response",
    [
        ProviderResponse(500, b"credential-canary"),
        ProviderResponse(
            500,
            b'{"error":{"code":100,"message":"token-canary"}}',
        ),
        ProviderResponse(
            502,
            b'{"error":{"code":131016,"message":"maintenance-canary"}}',
        ),
        ProviderResponse(302),
        ProviderResponse(200, b"bad"),
        success("user:wrong"),
        ProviderResponse(
            200, b'{"messaging_product":"whatsapp","contacts":[],"messages":[]}'
        ),
    ],
)
def test_uncertain_response_is_durable_and_never_resent(configured, response):
    runtime, provider, catalog = configured
    observe(runtime)
    provider.responses.append(response)
    assert invoke(catalog) == {
        "delivery": "unknown",
        "provider_message_id": None,
        "idempotency_replayed": False,
    }
    assert invoke(catalog)["idempotency_replayed"] is True
    assert len(provider.calls) == 1
    with runtime.state.database() as db:
        assert db.execute("SELECT state FROM replies").fetchone() == ("unknown",)


@pytest.mark.parametrize(
    "response, expected_code, expected_domain, expected_transient",
    [
        pytest.param(
            ProviderResponse(
                400,
                b'{"error":{"code":100,"message":"credential-canary"}}',
            ),
            "upstream_auth_failed",
            "credential_rejected",
            "no",
            id="invalid-token",
        ),
        pytest.param(
            ProviderResponse(
                403,
                b'{"error":{"code":131005,"message":"creator-canary"}}',
            ),
            "upstream_rejected",
            "message_refused",
            "no",
            id="creator-only-recipient",
        ),
        pytest.param(
            ProviderResponse(
                400,
                b'{"error":{"code":131009,"message":"ordinary-canary"}}',
            ),
            "upstream_rejected",
            "message_refused",
            "no",
            id="ordinary-invalid-message",
        ),
        pytest.param(
            ProviderResponse(
                401,
                b'{"error":{"code":190,"message":"authorization-canary"}}',
            ),
            "upstream_auth_failed",
            "credential_rejected",
            "no",
            id="malformed-authorization",
        ),
        pytest.param(
            ProviderResponse(400, b'{"error":'),
            "upstream_rejected",
            "message_refused",
            "no",
            id="malformed-body",
        ),
        pytest.param(
            ProviderResponse(
                400,
                b'{"error":{"code":"100","message":"string-code-canary"}}',
            ),
            "upstream_rejected",
            "message_refused",
            "no",
            id="non-integer-code",
        ),
        pytest.param(
            ProviderResponse(429, b'{"error":{"message":"rate-canary"}}'),
            "rate_limited",
            "provider_rate_limited",
            "no",
            id="rate-limited",
        ),
        pytest.param(
            ProviderResponse(
                503,
                b'{"error":{"code":131016,"message":"maintenance-canary"}}',
            ),
            "upstream_unavailable",
            "message_refused",
            "yes",
            id="documented-temporary-refusal",
        ),
    ],
)
def test_definitive_refusal_is_classified_and_replayed_without_submission(
    configured, response, expected_code, expected_domain, expected_transient
):
    runtime, provider, catalog = configured
    observe(runtime)
    provider.responses.append(response)
    with pytest.raises(OperationError) as first:
        invoke(catalog)
    with pytest.raises(OperationError) as second:
        invoke(catalog)
    assert first.value.to_dict() == second.value.to_dict()
    assert first.value.code == expected_code
    assert first.value.domain_code == expected_domain
    assert first.value.transient == expected_transient
    assert first.value.message == "WhatsApp operation could not complete safely."
    assert "canary" not in json.dumps(first.value.to_dict())
    assert len(provider.calls) == 1
    with runtime.state.database() as db:
        assert db.execute("SELECT state FROM replies").fetchone() == ("refused",)


def test_non_submission_releases_new_reservation(configured, monkeypatch):
    runtime, provider, catalog = configured
    observe(runtime)

    def offline(*args, **kwargs):
        raise WriteNotSubmitted(
            "upstream_unavailable",
            "Safe failure.",
            plugin="whatsapp",
            domain_code="transport_failed",
            replay="safe",
        )

    monkeypatch.setattr(provider, "request", offline)
    with pytest.raises(WriteNotSubmitted):
        invoke(catalog)
    with runtime.state.database() as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone() == (0,)


def test_transport_loss_and_abandoned_pending_never_resubmit(configured, monkeypatch):
    runtime, provider, catalog = configured
    observe(runtime)
    calls = []

    def lost(*args, **kwargs):
        calls.append(1)
        raise failure("upstream_timeout", "transport_timeout", replay="unsafe")

    monkeypatch.setattr(provider, "request", lost)
    assert invoke(catalog)["delivery"] == "unknown"
    assert invoke(catalog)["delivery"] == "unknown"
    assert calls == [1]
    with runtime.state.database() as db:
        db.execute("UPDATE replies SET state='pending',result=NULL")
    assert invoke(catalog)["idempotency_replayed"] is True
    assert calls == [1]


def test_known_acceptance_plus_storage_failure_is_partial_and_unsafe(
    configured, monkeypatch
):
    runtime, provider, catalog = configured
    observe(runtime)
    provider.responses.append(success())
    original = runtime.state.finish_reply

    def broken(*args):
        raise failure("plugin_error", "private_state_unavailable")

    monkeypatch.setattr(runtime.state, "finish_reply", broken)
    with pytest.raises(OperationError) as error:
        invoke(catalog)
    assert error.value.code == "partial_failure" and error.value.replay == "unsafe"
    assert error.value.details["provider_message_id"] == "wamid.sent"
    assert "credential-canary" not in json.dumps(error.value.to_dict())
    monkeypatch.setattr(runtime.state, "finish_reply", original)
    assert invoke(catalog)["delivery"] == "unknown"
    assert len(provider.calls) == 1


def test_concurrent_same_source_conflicts_then_replays(configured, monkeypatch):
    runtime, provider, catalog = configured
    observe(runtime)
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return success()

    monkeypatch.setattr(provider, "request", held)
    results = []
    worker = threading.Thread(target=lambda: results.append(invoke(catalog)))
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(OperationError) as error:
            invoke(catalog)
        assert error.value.domain_code == "reply_in_progress"
    finally:
        release.set()
        worker.join(5)
    assert results[0]["delivery"] == "accepted"
    assert invoke(catalog)["idempotency_replayed"] is True


def test_proactive_send_does_not_create_reply_reservation(configured):
    runtime, provider, catalog = configured
    provider.responses.extend([success(), success()])
    assert invoke(catalog, reply_to_message_id=None)["delivery"] == "accepted"
    assert invoke(catalog, reply_to_message_id=None)["idempotency_replayed"] is False
    assert len(provider.calls) == 2
    with runtime.state.database() as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone() == (0,)


@pytest.mark.parametrize("control", ["\x00", "\x1f", "\x7f", "\x80", "\x85", "\x9f"])
@pytest.mark.parametrize("field", ["participant", "quote", "source"])
def test_all_unicode_controls_in_identifiers_fail_before_submission(
    configured, control, field
):
    runtime, provider, catalog = configured
    observe(runtime)
    changes = {
        "participant": {"participant_id": "user:creator" + control},
        "quote": {
            "message": {
                "type": "text",
                "text": {"body": "Hello"},
                "context": {"message_id": "quote" + control},
            }
        },
        "source": {"reply_to_message_id": "inbound-1" + control},
    }
    with pytest.raises(OperationError):
        invoke(catalog, **changes[field])
    assert provider.calls == []
    from arbiter_whatsapp.messages import payload_for

    if field == "participant":
        with pytest.raises(OperationError):
            payload_for(
                "user:creator" + control, {"type": "text", "text": {"body": "ok"}}
            )
