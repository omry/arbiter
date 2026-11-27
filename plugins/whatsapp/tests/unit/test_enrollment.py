import io
import json
import os
import subprocess
from collections.abc import Callable, Iterable

import pytest
from omegaconf import OmegaConf

from arbiter_server.errors import OperationError
from arbiter_server.services import (
    ConfigCheckError,
    RuntimeRegistry,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp import WhatsAppRuntime, WhatsAppServicePlugin
from arbiter_whatsapp.config import (
    WhatsAppAccount,
    WhatsAppPolicyConfig,
    validate_config,
)
from arbiter_whatsapp.credentials import (
    AccountAdministrator,
    AdministrativePoller,
    BindingStore,
)
from arbiter_whatsapp.state import StateStore, reference_key
from arbiter_whatsapp.transport import ProviderClient, ProviderResponse


class Clock:
    def __init__(self):
        self.time = 0.0

    def now(self):
        return self.time

    def wait(self, seconds):
        self.time += seconds


def page(offset=1, *, provider="provider-1", text=None, participant="user:123"):
    messages = (
        []
        if text is None
        else [
            {
                "id": "message-1",
                "from": participant,
                "timestamp": "123",
                "type": "text",
                "text": {"body": text},
            }
        ]
    )
    return ProviderResponse(
        200,
        json.dumps(
            {
                "object": "whatsapp_agent_platform",
                "entry": [
                    {
                        "id": provider,
                        "changes": [
                            {
                                "field": "messages",
                                "value": {
                                    "messaging_product": "whatsapp",
                                    "contacts": [],
                                    "messages": messages,
                                    "statuses": [],
                                },
                            }
                        ],
                    }
                ],
                "next_offset": offset,
            }
        ).encode(),
    )


class Provider(ProviderClient):
    def __init__(
        self,
        clock,
        responses: Iterable[ProviderResponse | Callable[[], ProviderResponse]],
    ):
        self.clock, self.responses = clock, list(responses)
        self.calls = []

    def request(self, method, path, credential, **kwargs) -> ProviderResponse:
        assert method == "GET" and path == "/agent/v1/updates"
        self.calls.append((self.clock.now(), dict(kwargs["query"])))
        response = self.responses.pop(0) if self.responses else ProviderResponse(204)
        return response() if callable(response) else response


@pytest.fixture(autouse=True)
def operator_prepared_windows_parent(tmp_path):
    if os.name == "nt":
        from arbiter_server.file_protection.windows import _windows_file_security

        owner = _windows_file_security(tmp_path).owner_sid
        assert owner is not None
        subprocess.run(
            [
                "icacls",
                str(tmp_path),
                "/inheritance:r",
                "/remove:g",
                "*S-1-3-4",
                "/grant:r",
                f"*{owner}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )


def administrator(tmp_path, responses, *, challenge_factory=lambda: "a" * 32):
    clock = Clock()
    provider = Provider(clock, responses)
    store = BindingStore(
        StateStore(PluginStorage(plugin_name="whatsapp", root=tmp_path))
    )
    admin = AccountAdministrator(
        store,
        provider,
        clock=clock.now,
        wait=clock.wait,
        challenge_factory=challenge_factory,
    )
    return admin, store, provider, clock


@pytest.mark.parametrize("participant", ["user:123", "user:opaque-creator", "user:α"])
def test_first_empty_scan_retains_zero_and_proof_is_terminal_only(
    tmp_path, capsys, participant
):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path,
        [
            ProviderResponse(204),
            lambda: page(text=challenge[-1], participant=participant),
        ],
    )
    result = admin.run(
        "enroll", "whatsapp/first", lambda: "synthetic-token", challenge.append
    )
    assert result == {"status": "ready", "action": "enroll"}
    assert [v[1]["offset"] for v in provider.calls] == [0, 0]
    assert provider.calls[1][0] - provider.calls[0][0] >= 4.1
    assert store.snapshot("whatsapp/first").provider_id == "provider-1"
    with store.state.database() as db:
        assert db.execute("SELECT normal_offset FROM agents").fetchone() == (None,)
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone() == (0,)
    assert capsys.readouterr() == ("", "")


def test_existing_enrollment_is_idempotent_without_reading_secret(tmp_path):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    calls = len(provider.calls)

    def forbidden():
        pytest.fail("unexpected credential prompt")

    admin.run(
        "enroll", "whatsapp/first", forbidden, lambda _: pytest.fail("new challenge")
    )
    assert len(provider.calls) == calls


@pytest.mark.parametrize(
    "reply",
    [
        "old challenge",
        "Arbiter enrollment " + "b" * 32,
        "Arbiter enrollment " + "a" * 32 + " ",
    ],
)
def test_old_or_inexact_challenge_never_activates(tmp_path, reply):
    admin, store, provider, clock = administrator(
        tmp_path, [ProviderResponse(204), page(text=reply)]
    )
    with pytest.raises(OperationError) as error:
        admin.run("enroll", "whatsapp/first", lambda: "synthetic", lambda _: None)
    assert error.value.domain_code == "enrollment_budget_exhausted"
    assert store.optional("whatsapp/first") is None
    assert clock.now() < 180 + 4.1
    assert len(provider.calls) <= 181


@pytest.mark.parametrize(
    "participant",
    ["group:123", "agent:123", "user:", "user:123\n", "user:123\x7f", "user:123\x85"],
)
def test_wrong_participant_never_proves_enrollment(tmp_path, participant):
    admin, store, _, _ = administrator(
        tmp_path,
        [
            ProviderResponse(204),
            page(text="Arbiter enrollment " + "a" * 32, participant=participant),
        ],
    )
    with pytest.raises(OperationError):
        admin.run("enroll", "whatsapp/first", lambda: "synthetic", lambda _: None)
    assert store.optional("whatsapp/first") is None


def test_scan_identity_change_and_nonprogress_reject_before_challenge(tmp_path):
    for responses in ([page(1), page(2, provider="other")], [page(1), page(1)]):
        admin, store, _, _ = administrator(tmp_path, responses)
        with pytest.raises(OperationError):
            admin.run(
                "enroll",
                "whatsapp/first",
                lambda: "synthetic",
                lambda _: pytest.fail("challenge after malformed scan"),
            )
        assert store.optional("whatsapp/first") is None


def test_scan_request_budget_has_no_hidden_retry(tmp_path):
    admin, store, provider, _ = administrator(
        tmp_path, [page(i) for i in range(1, 302)]
    )
    with pytest.raises(OperationError) as error:
        admin.run(
            "enroll",
            "whatsapp/first",
            lambda: "synthetic",
            lambda _: pytest.fail("unbounded scan"),
        )
    assert error.value.domain_code == "enrollment_budget_exhausted"
    assert len(provider.calls) == 300
    assert store.optional("whatsapp/first") is None


def test_429_backoff_retains_exact_offset_and_phase_pacing(tmp_path):
    challenge = []
    responses = [
        ProviderResponse(429),
        ProviderResponse(429),
        page(7),
        ProviderResponse(204),
        lambda: page(8, text=challenge[-1]),
    ]
    admin, _, provider, _ = administrator(tmp_path, responses)
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    assert [c[1]["offset"] for c in provider.calls] == [0, 0, 0, 7, 7]
    starts = [c[0] for c in provider.calls]
    assert starts[1] - starts[0] >= 4.1
    assert starts[2] - starts[1] >= 8.2
    assert starts[-1] - starts[-2] >= 4.1
    assert all(c[1]["limit"] == 100 for c in provider.calls)


def test_every_rolling_minute_has_at_most_fifteen_requests():
    clock = Clock()
    provider = Provider(clock, [ProviderResponse(204)] * 60)
    poller = AdministrativePoller(
        provider, "synthetic", clock=clock.now, wait=clock.wait
    )
    for _ in range(60):
        poller.page(0, timeout=0, end=1000, maximum=60)
    starts = [c[0] for c in provider.calls]
    assert all(sum(start <= t <= start + 60 for t in starts) <= 15 for start in starts)
    with pytest.raises(OperationError):
        poller.page(0, timeout=0, end=1000, maximum=60)
    assert len(provider.calls) == 60


def test_429_retries_share_original_time_and_attempt_budgets():
    clock = Clock()
    provider = Provider(clock, [ProviderResponse(429)] * 20)
    poller = AdministrativePoller(
        provider, "synthetic", clock=clock.now, wait=clock.wait
    )
    with pytest.raises(OperationError):
        poller.page(0, timeout=0, end=20, maximum=20)
    assert len(provider.calls) == 3
    assert all(c[1]["offset"] == 0 for c in provider.calls)


def test_replaced_poll_returns_without_retry_or_activation(tmp_path):
    admin, store, provider, _ = administrator(tmp_path, [ProviderResponse(409)])
    with pytest.raises(OperationError) as error:
        admin.run("enroll", "whatsapp/first", lambda: "synthetic", lambda _: None)
    assert error.value.domain_code == "poll_replaced"
    assert len(provider.calls) == 1
    assert store.optional("whatsapp/first") is None


def test_replacement_proof_is_same_identity_and_preserves_normal_state(tmp_path):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "old", challenge.append)
    with store.state.database() as db:
        db.execute("UPDATE agents SET normal_offset=123")
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    before = path.read_bytes()
    for responses in ([page(provider="other")], [ProviderResponse(204)]):
        provider.responses = list(responses)
        with pytest.raises(OperationError):
            admin.run(
                "replace",
                "whatsapp/first",
                lambda: "new",
                lambda _: pytest.fail("replacement challenge"),
            )
        assert path.read_bytes() == before
    provider.responses = [page(), ProviderResponse(204)]
    admin.run(
        "replace",
        "whatsapp/first",
        lambda: "new",
        lambda _: pytest.fail("replacement challenge"),
    )
    assert store.snapshot("whatsapp/first").token == "new"
    with store.state.database() as db:
        assert db.execute("SELECT normal_offset FROM agents").fetchone() == (123,)


def test_duplicate_provider_stops_candidate_scan_immediately(tmp_path):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    provider.responses = [page(), page(2)]
    before = len(provider.calls)
    with pytest.raises(OperationError) as error:
        admin.run(
            "enroll",
            "whatsapp/alias",
            lambda: "synthetic",
            lambda _: pytest.fail("duplicate challenge"),
        )
    assert error.value.domain_code == "provider_already_bound"
    assert len(provider.calls) == before + 1
    assert store.optional("whatsapp/alias") is None


@pytest.mark.parametrize("bad", [False, "false", 0, None, [], {}])
def test_policy_never_coerces_nonboolean_values(bad):
    if type(bad) is bool:
        assert (
            validate_config(
                {"first": {"credential": "whatsapp/first"}},
                {"default": {"updates": bad}},
            )[1]["default"].updates
            is False
        )
    else:
        with pytest.raises(ConfigCheckError):
            validate_config(
                {"first": {"credential": "whatsapp/first"}},
                {"default": {"updates": bad}},
            )
    raw = OmegaConf.structured(WhatsAppPolicyConfig(updates="false"))
    assert raw.updates == "false"
    with pytest.raises(ConfigCheckError):
        validate_config({}, {"default": raw})


@pytest.mark.parametrize(
    "accounts,policies",
    [
        ({"x": {"credential": "../../token"}}, {"default": {}}),
        ({"x": {"credential": "whatsapp/x"}}, {}),
        (
            {"x": {"credential": "whatsapp/x"}, "y": {"credential": "whatsapp/x"}},
            {"default": {}},
        ),
        ({}, {"default": {"unknown": True}}),
        ({"x": {"token": "synthetic"}}, {"default": {}}),
    ],
)
def test_invalid_config_is_safe_and_unavailable(accounts, policies):
    with pytest.raises(ConfigCheckError) as error:
        validate_config(accounts, policies)
    assert "synthetic" not in str(error.value)


def test_unimplemented_operations_reject_without_storage_or_provider_effect(tmp_path):
    clock = Clock()
    provider = Provider(clock, [])
    context = ServiceRuntimeContext(
        dependencies={
            "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
            "whatsapp_client": provider,
        }
    )
    runtime = WhatsAppRuntime(
        {"first": {"credential": "whatsapp/first"}},
        {"default": {"updates": False}},
        context,
    )
    with pytest.raises(OperationError) as error:
        runtime.authorize("receive_updates", "first")
    assert error.value.code == "policy_denied"
    with pytest.raises(OperationError):
        runtime.authorize("unknown", "first")
    assert not provider.calls and not list(tmp_path.iterdir())
    plugin = WhatsAppServicePlugin()
    assert plugin.describe_operations(ServicePluginContext(RuntimeRegistry({}))) == ()
    with pytest.raises(OperationError):
        plugin.invoke_operation("send_message", {}, None)
    with pytest.raises(OperationError):
        plugin.check_operation("send_message", {}, None)


def test_live_readiness_preserves_cursor_and_redacts_upstream_failures(tmp_path):
    challenge = []
    admin, store, provider, clock = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "readiness-canary", challenge.append)
    runtime = WhatsAppRuntime(
        {"first": WhatsAppAccount(credential="whatsapp/first")},
        {"default": WhatsAppPolicyConfig()},
        ServiceRuntimeContext(
            dependencies={
                "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
                "whatsapp_client": provider,
                "whatsapp_clock": clock.now,
                "whatsapp_wait": clock.wait,
            }
        ),
    )
    progress = []
    provider.responses = [ProviderResponse(204)]
    assert runtime.test_accounts(progress=progress.append) == {
        "first": {"status": "ok"}
    }
    assert progress == ["first"]
    assert (
        runtime.authorize("send_message", "first", media=True).provider_id
        == "provider-1"
    )
    for response in [
        ProviderResponse(401, b"readiness-canary"),
        page(provider="foreign"),
    ]:
        provider.responses = [response]
        result = runtime.test_accounts()
        assert result["first"]["status"] == "failed"
        assert "readiness-canary" not in json.dumps(result)
        assert "foreign" not in json.dumps(result)
    with store.state.database() as db:
        assert db.execute("SELECT normal_offset FROM agents").fetchone() == (None,)
    with pytest.raises(OperationError) as error:
        runtime.account("unknown")
    assert error.value.domain_code == "unknown_account"


@pytest.mark.parametrize("table", ["replies", "sources"])
def test_readiness_rejects_missing_safety_table_before_provider_io(tmp_path, table):
    challenge = []
    admin, store, provider, clock = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    database = store.state.path / "state.sqlite"
    with store.state.database() as db:
        db.execute(f"DROP TABLE {table}")
    damaged = database.read_bytes()
    provider.calls.clear()
    runtime = WhatsAppRuntime(
        {"first": WhatsAppAccount(credential="whatsapp/first")},
        {"default": WhatsAppPolicyConfig()},
        ServiceRuntimeContext(
            dependencies={
                "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
                "whatsapp_client": provider,
                "whatsapp_clock": clock.now,
                "whatsapp_wait": clock.wait,
            }
        ),
    )

    result = runtime.test_accounts()

    assert result["first"]["status"] == "failed"
    assert provider.calls == []
    assert database.read_bytes() == damaged


@pytest.mark.parametrize("table", ["replies", "sources"])
def test_existing_enrollment_reopen_fails_without_rebuilding_state(tmp_path, table):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    database = store.state.path / "state.sqlite"
    with store.state.database() as db:
        db.execute(f"DROP TABLE {table}")
    damaged = database.read_bytes()
    provider.calls.clear()

    with pytest.raises(OperationError):
        admin.run(
            "enroll",
            "whatsapp/first",
            lambda: pytest.fail("read a credential for an existing enrollment"),
            lambda _: pytest.fail("created a new challenge for an existing enrollment"),
        )

    assert provider.calls == []
    assert database.read_bytes() == damaged


def test_readiness_rejects_duplicate_bindings_before_provider_io(tmp_path):
    challenge = []
    admin, store, provider, clock = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    directory = store.state.path / "bindings"
    original = directory / (reference_key("whatsapp/first") + ".json")
    duplicate = directory / (reference_key("whatsapp/alias") + ".json")
    original_bytes = original.read_bytes()
    duplicate.write_bytes(original_bytes)
    duplicate.chmod(0o600)
    duplicate_bytes = duplicate.read_bytes()
    provider.calls.clear()
    runtime = WhatsAppRuntime(
        {"first": WhatsAppAccount(credential="whatsapp/first")},
        {"default": WhatsAppPolicyConfig()},
        ServiceRuntimeContext(
            dependencies={
                "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
                "whatsapp_client": provider,
                "whatsapp_clock": clock.now,
                "whatsapp_wait": clock.wait,
            }
        ),
    )

    result = runtime.test_accounts()

    assert result["first"]["status"] == "failed"
    assert provider.calls == []
    assert original.read_bytes() == original_bytes
    assert duplicate.read_bytes() == duplicate_bytes


def test_distinct_bindings_are_ready_with_root_lock_released_before_transport(tmp_path):
    challenge = []
    admin, store, provider, clock = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "first-token", challenge.append)
    store.publish("whatsapp/second", "provider-2", "second-token", previous=None)
    provider.calls.clear()
    provider.responses = [ProviderResponse(204), ProviderResponse(204)]
    original_request = provider.request
    lock_checks = []

    def request(method, path, credential, **kwargs):
        with store.state.lock("root-binding"):
            lock_checks.append(True)
        return original_request(method, path, credential, **kwargs)

    provider.request = request
    runtime = WhatsAppRuntime(
        {
            "first": WhatsAppAccount(credential="whatsapp/first"),
            "second": WhatsAppAccount(credential="whatsapp/second"),
        },
        {"default": WhatsAppPolicyConfig()},
        ServiceRuntimeContext(
            dependencies={
                "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
                "whatsapp_client": provider,
                "whatsapp_clock": clock.now,
                "whatsapp_wait": clock.wait,
            }
        ),
    )

    assert runtime.test_accounts() == {
        "first": {"status": "ok"},
        "second": {"status": "ok"},
    }
    assert len(provider.calls) == 2
    assert lock_checks == [True, True]


def test_plugin_config_projection_and_missing_dependencies(tmp_path):
    plugin = WhatsAppServicePlugin()
    assert plugin.public_account_config(WhatsAppAccount()) == {
        "credential": "whatsapp/default",
        "policy": "default",
    }
    assert plugin.public_policy_config(WhatsAppPolicyConfig())["updates"] is True
    assert (
        plugin.describe_capability(ServicePluginContext(RuntimeRegistry({}))).name
        == "whatsapp"
    )
    assert plugin.bootstrap_config(kind="unsupported", name="first") is None
    plugin.check_config(accounts={}, policies={"default": WhatsAppPolicyConfig()})
    with pytest.raises(OperationError):
        plugin.build_runtime({}, {}, ServiceRuntimeContext())
    with pytest.raises(ConfigCheckError):
        validate_config({}, {"default": {1: True}})
    with pytest.raises(ConfigCheckError):
        validate_config({}, {"default": "invalid"})


def test_unknown_action_and_replacement_without_binding(tmp_path):
    admin, store, provider, _ = administrator(tmp_path, [])
    with pytest.raises(OperationError) as error:
        admin.run(
            "replace",
            "whatsapp/first",
            lambda: pytest.fail("unbound replacement credential"),
            lambda _: None,
        )
    assert error.value.domain_code == "account_not_enrolled"
    assert not provider.calls
    with pytest.raises(OperationError):
        admin.run(
            "unknown",
            "whatsapp/first",
            lambda: pytest.fail("unknown action"),
            lambda _: None,
        )


@pytest.mark.parametrize(
    "malformed",
    ["object", "entry", "changes", "product", "contacts", "timestamp", "text"],
)
def test_malformed_enrollment_page_never_issues_a_challenge(tmp_path, malformed):
    response = page(text="untrusted")
    value = json.loads(response.body)
    message_value = value["entry"][0]["changes"][0]["value"]
    if malformed == "object":
        value["object"] = "other"
    elif malformed == "entry":
        value["entry"] = []
    elif malformed == "changes":
        value["entry"][0]["changes"] = []
    elif malformed == "product":
        message_value["messaging_product"] = "other"
    elif malformed == "contacts":
        message_value["contacts"] = "invalid"
    elif malformed == "timestamp":
        message_value["messages"][0]["timestamp"] = 123
    else:
        message_value["messages"][0]["text"]["body"] = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(200, json.dumps(value).encode())]
    )
    with pytest.raises(OperationError) as error:
        admin.run(
            "enroll",
            "whatsapp/first",
            lambda: "synthetic",
            lambda _: pytest.fail("challenge after malformed page"),
        )
    assert error.value.code == "upstream_protocol_error"
    assert len(provider.calls) == 1 and store.optional("whatsapp/first") is None


def test_unbound_readiness_and_missing_directories_have_no_provider_effect(tmp_path):
    clock = Clock()
    provider = Provider(clock, [])
    runtime = WhatsAppRuntime(
        {"first": {"credential": "whatsapp/first"}},
        {"default": {}},
        ServiceRuntimeContext(
            dependencies={
                "plugin_storage": PluginStorage(plugin_name="whatsapp", root=tmp_path),
                "whatsapp_client": provider,
            }
        ),
    )
    assert runtime.test_accounts()["first"]["status"] == "failed"
    assert not provider.calls and not list(tmp_path.iterdir())
    runtime.state.root(create=True)
    with pytest.raises(OperationError):
        runtime.state.directory("bindings")


def test_identity_cannot_change_during_challenge(tmp_path):
    challenge = []
    admin, store, _, _ = administrator(
        tmp_path,
        [
            page(),
            ProviderResponse(204),
            lambda: page(2, provider="foreign", text=challenge[-1]),
        ],
    )
    with pytest.raises(OperationError) as error:
        admin.run("enroll", "whatsapp/first", lambda: "synthetic", challenge.append)
    assert error.value.domain_code == "identity_changed"
    assert store.optional("whatsapp/first") is None


@pytest.mark.parametrize(
    "damage",
    [
        "missing-payload",
        "wrong-payload",
        "two-payloads",
        "media-id",
        "mime",
        "digest",
        "caption",
        "voice",
        "animated",
        "reaction",
        "context",
        "contact",
        "profile",
        "status-id",
        "status-recipient",
        "status-time",
        "status-kind",
    ],
)
def test_malformed_known_fields_cannot_authorize_replacement(tmp_path, damage):
    challenge = []
    admin, store, provider, _ = administrator(
        tmp_path, [ProviderResponse(204), lambda: page(text=challenge[-1])]
    )
    admin.run("enroll", "whatsapp/first", lambda: "old-token", challenge.append)
    path = store.state.path / "bindings" / (reference_key("whatsapp/first") + ".json")
    before = path.read_bytes()
    data = json.loads(page(text="irrelevant").body)
    value = data["entry"][0]["changes"][0]["value"]
    message = value["messages"][0]
    media = {
        "id": "media-id",
        "mime_type": "image/jpeg",
        "sha256": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    if damage in {
        "missing-payload",
        "wrong-payload",
        "two-payloads",
        "media-id",
        "mime",
        "digest",
        "caption",
        "voice",
        "animated",
    }:
        message.pop("text")
        message["type"] = "sticker" if damage == "animated" else "image"
        message[message["type"]] = dict(media)
        payload = message[message["type"]]
        if damage == "missing-payload":
            message.pop(message["type"])
        elif damage == "wrong-payload":
            message[message["type"]] = []
        elif damage == "two-payloads":
            message["text"] = {"body": "extra"}
        elif damage == "media-id":
            payload["id"] = None
        elif damage == "mime":
            payload["mime_type"] = []
        elif damage == "digest":
            payload["sha256"] = "invalid"
        elif damage == "caption":
            payload["caption"] = []
        elif damage == "voice":
            payload["voice"] = 1
        else:
            payload["animated"] = True
    elif damage == "reaction":
        message.pop("text")
        message["type"] = "reaction"
        message["reaction"] = {"message_id": "source", "emoji": []}
    elif damage == "context":
        message["context"] = {"id": "quoted", "from": "group:1"}
    elif damage in {"contact", "profile"}:
        contact: dict[str, object] = {"wa_id": "user:opaque"}
        value["contacts"] = [contact]
        if damage == "contact":
            contact["wa_id"] = 1
        else:
            contact["profile"] = {"name": []}
    else:
        status: dict[str, object] = {
            "id": "message-id",
            "recipient_id": "user:opaque",
            "timestamp": "123",
            "status": "read",
        }
        value["statuses"] = [status]
        status[
            {
                "status-id": "id",
                "status-recipient": "recipient_id",
                "status-time": "timestamp",
                "status-kind": "status",
            }[damage]
        ] = None
    provider.responses = [
        ProviderResponse(200, json.dumps(data).encode()),
        ProviderResponse(204),
    ]
    with pytest.raises(OperationError) as error:
        admin.run(
            "replace",
            "whatsapp/first",
            lambda: "candidate-token",
            lambda _: pytest.fail("replacement challenge"),
        )
    assert error.value.code == "upstream_protocol_error"
    assert path.read_bytes() == before
    assert store.snapshot("whatsapp/first").token == "old-token"
    with store.state.database() as db:
        assert db.execute("SELECT normal_offset FROM agents").fetchone() == (None,)


@pytest.mark.parametrize(
    "kind", ["text", "image", "audio", "video", "document", "sticker", "reaction"]
)
def test_valid_known_payloads_and_additive_fields_preserve_opaque_identity(kind):
    from arbiter_whatsapp.credentials import update_page

    data = json.loads(page(text="text", participant="user:opaque").body)
    value = data["entry"][0]["changes"][0]["value"]
    message = value["messages"][0]
    message.pop("text")
    message["type"] = kind
    payload: dict[str, object] = {
        "id": "media-id",
        "mime_type": "application/octet-stream",
        "sha256": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    if kind == "text":
        payload = {"body": "text"}
    elif kind == "reaction":
        payload = {"message_id": "quoted", "emoji": ""}
    elif kind == "sticker":
        payload["animated"] = False
    elif kind == "audio":
        payload["voice"] = True
    elif kind == "document":
        payload.update(filename="file.txt", caption="caption")
    message[kind] = payload
    message["context"] = {"id": "quoted", "from": "agent:opaque"}
    message["future_additive_field"] = {"unknown": True}
    value["contacts"] = [{"wa_id": "user:opaque", "profile": {"name": "Creator"}}]
    value["statuses"] = [
        {
            "id": "outbound",
            "recipient_id": "user:opaque",
            "timestamp": "123",
            "status": "read",
        }
    ]
    provider_id, messages, offset = update_page(
        ProviderResponse(200, json.dumps(data).encode()), 0
    )
    assert provider_id == "provider-1" and offset == 1
    assert messages[0]["from"] == "user:opaque"


def test_delayed_wait_or_response_cannot_start_or_accept_after_budget():
    clock = Clock()
    provider = Provider(clock, [ProviderResponse(204)])

    def late_wait(seconds):
        clock.time += seconds + 100

    poller = AdministrativePoller(
        provider, "synthetic", clock=clock.now, wait=late_wait
    )
    poller.next_start = 4.1
    with pytest.raises(OperationError):
        poller.page(0, timeout=0, end=30, maximum=10)
    assert not provider.calls

    def delayed_response():
        clock.time += 100
        return ProviderResponse(204)

    provider.responses = [delayed_response]
    poller.next_start = 0
    with pytest.raises(OperationError):
        poller.page(0, timeout=0, end=clock.time + 30, maximum=10)
    assert len(provider.calls) == 1


def test_real_cli_bootstrap_activation_enrollment_and_reopen(
    tmp_path, monkeypatch, capsys
):
    import arbiter_server.config as config_module
    import arbiter_server.plugins as plugins_module
    import arbiter_server.main as cli
    from importlib.metadata import EntryPoint, EntryPoints

    existing = list(plugins_module.entry_points().select(group="arbiter.services"))
    points = EntryPoints(
        [
            *existing,
            EntryPoint(
                name="whatsapp",
                value="arbiter_whatsapp:plugin",
                group="arbiter.services",
            ),
        ]
    )
    monkeypatch.setattr(config_module, "entry_points", lambda: points)
    monkeypatch.setattr(plugins_module, "entry_points", lambda: points)
    conf = tmp_path / "conf"
    base = ["--config-dir", str(conf)]
    assert cli.main([*base, "bootstrap", "--server"]) == 0
    assert (
        cli.main([*base, "bootstrap", "--plugin", "whatsapp", "--account", "first"])
        == 0
    )
    assert (
        cli.main(
            [*base, "config", "activate", "--plugin", "whatsapp", "--account", "first"]
        )
        == 0
    )
    clock = Clock()
    terminal = io.StringIO()

    class Terminal:
        def __enter__(self):
            return terminal

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(cli, "_open_operator_terminal", lambda: Terminal())
    provider = Provider(
        clock, [ProviderResponse(204), lambda: page(text=terminal.getvalue().strip())]
    )
    context_factory = cli._service_runtime_context
    state_root = tmp_path / "state"

    def context(cfg, plugin, dependencies=None):
        return context_factory(
            cfg,
            plugin,
            {
                "whatsapp_client": provider,
                "whatsapp_clock": clock.now,
                "whatsapp_wait": clock.wait,
                **(dependencies or {}),
            },
        )

    monkeypatch.setattr(cli, "_service_runtime_context", context)
    cfg_file = conf / "arbiter-server.yaml"
    config = OmegaConf.load(cfg_file)
    OmegaConf.update(
        config, "arbiter.storage.plugin_data_dir", state_root.as_posix(), force_add=True
    )
    cfg_file.write_text(OmegaConf.to_yaml(config))
    assert cli.main([*base, "env", "bootstrap"]) == 0
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret-canary\n"))
    command = [
        *base,
        "config",
        "activate",
        "--plugin",
        "whatsapp",
        "--account",
        "first",
        "--enroll",
        "--token-stdin",
    ]

    def interrupt():
        raise KeyboardInterrupt

    provider.responses = [ProviderResponse(204), interrupt]
    saved_account = conf / "arbiter" / "account" / "whatsapp" / "first.yaml"
    saved_bytes = saved_account.read_bytes()
    assert cli.main(command) == 1
    bindings = BindingStore(
        StateStore(PluginStorage(plugin_name="whatsapp", root=state_root))
    )
    assert bindings.optional("whatsapp/first") is None
    assert saved_account.read_bytes() == saved_bytes
    old_challenge = terminal.getvalue().strip()
    terminal.seek(0)
    terminal.truncate(0)
    provider.responses = [
        ProviderResponse(204),
        lambda: page(text=terminal.getvalue().strip()),
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret-canary\n"))
    assert cli.main(command) == 0
    assert terminal.getvalue().strip() != old_challenge
    binding = BindingStore(
        StateStore(PluginStorage(plugin_name="whatsapp", root=state_root))
    ).snapshot("whatsapp/first")
    assert binding.token == "secret-canary"
    calls = len(provider.calls)
    monkeypatch.setattr(
        cli,
        "_open_operator_terminal",
        lambda: pytest.fail("terminal for already-enrolled account"),
    )
    assert cli.main(command) == 0
    assert len(provider.calls) == calls
    provider.responses = [page(), ProviderResponse(204)]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("replacement-canary\n"))
    replacement = [
        *base,
        "config",
        "activate",
        "--plugin",
        "whatsapp",
        "--account",
        "first",
        "--replace-credential",
        "--token-stdin",
    ]
    assert cli.main(replacement) == 0
    assert bindings.snapshot("whatsapp/first").token == "replacement-canary"
    assert bindings.snapshot("whatsapp/first").store_uuid == binding.store_uuid
    captured = capsys.readouterr()
    assert "secret-canary" not in captured.out + captured.err
    assert "replacement-canary" not in captured.out + captured.err
    assert "Arbiter enrollment" not in captured.out + captured.err
    assert "account ready: whatsapp/first" in captured.out
