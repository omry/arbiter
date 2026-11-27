import itertools
import json

import pytest

from arbiter_server.errors import OperationError
from arbiter_whatsapp.config import WhatsAppPolicy
from arbiter_whatsapp.statuses import check_status, submit_status
from arbiter_whatsapp.transport import ProviderResponse, failure
from plugins.whatsapp.tests.unit.test_transport import endpoint
from plugins.whatsapp.tests.unit.test_updates import configured, state_rows


def invoke(catalog, operation="mark_read", **changes):
    arguments: dict[str, object] = {"account": "first", "message_id": "wamid.source"}
    if operation == "typing":
        arguments["active"] = True
    return catalog.invoke_operation("whatsapp:" + operation, {**arguments, **changes})


@pytest.mark.parametrize("operation", ["mark_read", "typing"])
@pytest.mark.parametrize("read,typing,send", itertools.product([False, True], repeat=3))
def test_status_effect_permissions_are_independent(
    configured, operation, read, typing, send
):
    runtime, provider, catalog = configured
    runtime.policies["default"] = WhatsAppPolicy(
        read_receipts=read, typing=typing, send_messages=send
    )
    allowed = read and (operation == "mark_read" or typing)
    arguments: dict[str, object] = {"account": "first", "message_id": "wamid.source"}
    if operation == "typing":
        arguments["active"] = True
    if not allowed:
        for call in [catalog.check_operation, catalog.invoke_operation]:
            with pytest.raises(OperationError) as error:
                call("whatsapp:" + operation, arguments)
            assert error.value.code == "policy_denied"
        assert provider.calls == []
        return
    before = state_rows(runtime)
    catalog.check_operation("whatsapp:" + operation, arguments)
    assert provider.calls == []
    provider.responses.append(ProviderResponse(200, b'{"success":true}'))
    assert invoke(catalog, operation) == {"status": "accepted"}
    assert state_rows(runtime) == before
    assert len(provider.calls) == 1
    method, path, token, options = provider.calls[0]
    assert (method, path, token) == ("POST", "/agent/v1/statuses", "credential-canary")
    payload: dict[str, object] = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.source",
    }
    if operation == "typing":
        payload["typing_indicator"] = {"type": "text"}
    assert options["json_body"] == payload


def test_typing_false_is_unsupported_without_submission(configured):
    runtime, provider, catalog = configured
    for call in [catalog.check_operation, catalog.invoke_operation]:
        with pytest.raises(OperationError) as error:
            call(
                "whatsapp:typing",
                {"account": "first", "message_id": "wamid.source", "active": False},
            )
        assert (error.value.code, error.value.domain_code) == (
            "upstream_rejected",
            "typing_reset_unsupported",
        )
        assert error.value.transient == "no" and error.value.replay == "safe"
    assert provider.calls == []


@pytest.mark.parametrize("operation", ["mark_read", "typing"])
@pytest.mark.parametrize(
    "changes",
    [
        {"account": "unknown"},
        {"account": ""},
        {"message_id": ""},
        {"message_id": "x" * 513},
        {"message_id": "wamid.\ninvalid"},
        {"message_id": "x" * 511 + "é"},
    ],
)
def test_invalid_status_input_never_submits(configured, operation, changes):
    _, provider, catalog = configured
    with pytest.raises(OperationError):
        invoke(catalog, operation, **changes)
    assert provider.calls == []


@pytest.mark.parametrize("active", [None, 0, 1, "yes", [], {}])
def test_direct_typing_requires_boolean(configured, active):
    runtime, provider, _ = configured
    with pytest.raises(OperationError) as error:
        check_status(runtime, "typing", "first", "wamid.source", active)
    assert error.value.domain_code == "invalid_typing_state"
    assert provider.calls == []


def test_unknown_status_operation_never_submits(configured):
    runtime, provider, _ = configured
    with pytest.raises(OperationError) as error:
        submit_status(runtime, "send_message", "first", "wamid.source")
    assert error.value.domain_code == "unknown_operation"
    assert provider.calls == []


@pytest.mark.parametrize(
    "response",
    [
        ProviderResponse(200, b'{"success":false}'),
        ProviderResponse(200, b'{"success":1}'),
        ProviderResponse(200, b"{}"),
        ProviderResponse(200, b"[]"),
        ProviderResponse(200, b'{"success":true,"success":false}'),
        ProviderResponse(200, b"token-canary"),
        ProviderResponse(204),
        ProviderResponse(302, b"token-canary"),
        ProviderResponse(401, b"token-canary"),
        ProviderResponse(429, b"token-canary"),
        ProviderResponse(500, b"token-canary"),
    ],
)
def test_only_confirmed_status_acknowledgement_succeeds(configured, response, capsys):
    runtime, provider, catalog = configured
    provider.responses.append(response)
    with pytest.raises(OperationError) as error:
        invoke(catalog)
    assert error.value.replay == "unsafe"
    assert "canary" not in str(error.value)
    assert len(provider.calls) == 1 and provider.responses == []
    assert "canary" not in str(capsys.readouterr())


def test_status_transport_failure_is_not_retried(configured):
    runtime, provider, _ = configured
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise failure("upstream_timeout", "transport_timeout", replay="unsafe")

    runtime.client.request = fail
    with pytest.raises(OperationError) as error:
        submit_status(runtime, "mark_read", "first", "wamid.source")
    assert error.value.replay == "unsafe" and len(calls) == 1
    assert provider.calls == []


@pytest.mark.parametrize("operation", ["mark_read", "typing"])
def test_actual_status_transport_serializes_one_catalog_request(
    configured, endpoint, operation
):
    runtime, provider, catalog = configured
    client, observed, responses = endpoint
    runtime.client = client
    responses.append((200, b'{"success":true}', {}))
    assert invoke(catalog, operation) == {"status": "accepted"}
    assert len(observed) == 1
    path, authorization, body = observed[0]
    assert path == "/agent/v1/statuses" and authorization == "Bearer credential-canary"
    payload = json.loads(body)
    assert payload == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.source",
        **({"typing_indicator": {"type": "text"}} if operation == "typing" else {}),
    }
    assert provider.calls == []
