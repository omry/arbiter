import base64
import json
import os
import subprocess
import sys
import time

import pytest

from arbiter_server.errors import OperationError
from arbiter_server.services import (
    OperationCatalog,
    RuntimeRegistry,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp import WhatsAppRuntime, WhatsAppServicePlugin
from arbiter_whatsapp.credentials import BindingStore
from arbiter_whatsapp.state import StateStore
from arbiter_whatsapp.transport import ProviderResponse
from arbiter_whatsapp.updates import project_page, receive_updates


def envelope(offset=17, *, messages=None, statuses=None, provider="provider-1") -> dict:
    return {
        "object": "whatsapp_agent_platform",
        "entry": [
            {
                "id": provider,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [
                                {
                                    "wa_id": "user:creator",
                                    "profile": {"name": "Creator"},
                                }
                            ],
                            "messages": [message()] if messages is None else messages,
                            "statuses": [] if statuses is None else statuses,
                        },
                    }
                ],
            }
        ],
        "next_offset": offset,
    }


def message(kind="text", *, id="inbound-1") -> dict:
    payload: dict = {"body": "Hello"}
    if kind == "reaction":
        payload = {"message_id": "outbound-1", "emoji": ""}
    elif kind != "text":
        payload = {
            "id": "media-1",
            "mime_type": "image/jpeg",
            "sha256": base64.b64encode(bytes(32)).decode(),
        }
        if kind in ("image", "video", "document"):
            payload["caption"] = "private caption"
        if kind == "document":
            payload["filename"] = "private.pdf"
        if kind == "audio":
            payload["voice"] = True
        if kind == "sticker":
            payload["animated"] = False
    return {
        "id": id,
        "from": "user:creator",
        "timestamp": "123",
        "type": kind,
        kind: payload,
    }


def response(data):
    return ProviderResponse(200, json.dumps(data).encode())


class Provider:
    def __init__(self):
        self.responses = []
        self.calls = []

    def request(self, method, path, credential, **kwargs):
        self.calls.append((method, path, credential, kwargs))
        return self.responses.pop(0)


@pytest.fixture
def configured(tmp_path):
    if os.name == "nt":
        from arbiter_server.file_protection.windows import _windows_file_security

        owner = _windows_file_security(tmp_path).owner_sid
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
    storage = PluginStorage(plugin_name="whatsapp", root=tmp_path)
    state = StateStore(storage)
    state.directory("bindings", create=True)
    state.directory("locks", create=True)
    with state.database(initialize=True):
        pass
    bindings = BindingStore(state)
    bindings.publish("whatsapp/first", "provider-1", "credential-canary", previous=None)
    provider = Provider()
    runtime = WhatsAppRuntime(
        {"first": {"credential": "whatsapp/first"}},
        {"default": {}},
        ServiceRuntimeContext(
            dependencies={"plugin_storage": storage, "whatsapp_client": provider}
        ),
    )
    context = ServicePluginContext(RuntimeRegistry({"whatsapp": runtime}))
    catalog = OperationCatalog(
        [WhatsAppServicePlugin()],
        context,
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )
    return runtime, provider, catalog


def state_rows(runtime):
    with runtime.state.database() as db:
        return (
            db.execute("SELECT normal_offset FROM agents").fetchone()[0],
            db.execute(
                "SELECT message_id,participant,timestamp,message_type FROM sources ORDER BY message_id"
            ).fetchall(),
        )


def poll(catalog, **kwargs):
    return catalog.invoke_operation(
        "whatsapp:receive_updates", {"account": "first", **kwargs}
    )


def test_catalog_initial_cursor_saved_continuation_and_empty_zero(configured):
    runtime, provider, catalog = configured
    with pytest.raises(OperationError) as error:
        poll(catalog)
    assert error.value.domain_code == "cursor_required"
    assert not provider.calls
    provider.responses = [
        ProviderResponse(204),
        response(envelope(2**53 + 11)),
        ProviderResponse(204),
    ]
    assert poll(catalog, offset=0) == {"updates": [], "next_offset": 0, "replay": False}
    assert poll(catalog)["next_offset"] == 2**53 + 11
    assert poll(catalog)["next_offset"] == 2**53 + 11
    assert [c[3]["query"] for c in provider.calls] == [
        {"offset": 0, "limit": 100, "timeout": 15},
        {"offset": 0, "limit": 100, "timeout": 15},
        {"offset": 2**53 + 11, "limit": 100, "timeout": 15},
    ]
    assert state_rows(runtime) == (
        2**53 + 11,
        [("inbound-1", "user:creator", "123", "text")],
    )


def test_exact_maximum_and_normal_mismatch(configured):
    runtime, provider, catalog = configured
    provider.responses = [response(envelope(2**63 - 1)), ProviderResponse(204)]
    assert poll(catalog, offset=0)["next_offset"] == 2**63 - 1
    with pytest.raises(OperationError) as error:
        poll(catalog, offset=2**63 - 2)
    assert error.value.domain_code == "cursor_conflict"
    assert poll(catalog)["next_offset"] == 2**63 - 1
    assert len(provider.calls) == 2


@pytest.mark.parametrize("offset", [-1, 2**63, True, False, 1.0, 1.5, [], {}])
def test_invalid_catalog_cursor_never_polls(configured, offset):
    runtime, provider, catalog = configured
    expected: tuple[type[Exception], ...] = (OperationError, ValueError)
    with pytest.raises(expected):
        poll(catalog, offset=offset)
    assert not provider.calls
    assert state_rows(runtime) == (None, [])


@pytest.mark.parametrize(
    "offset,replay", [("1", False), (True, False), (0, "false"), (0, 1)]
)
def test_direct_entry_rejects_nonexact_types(configured, offset, replay):
    runtime, provider, _ = configured
    with pytest.raises(OperationError):
        receive_updates(runtime, "first", offset, replay)
    assert not provider.calls


def test_replay_records_sources_and_preserves_normal_continuation(configured):
    runtime, provider, catalog = configured
    provider.responses = [
        response(envelope(20)),
        response(envelope(5, messages=[message(id="historical")])),
        ProviderResponse(204),
        ProviderResponse(204),
    ]
    poll(catalog, offset=0)
    assert poll(catalog, offset=0, replay=True)["next_offset"] == 5
    assert poll(catalog, offset=0, replay=True) == {
        "updates": [],
        "next_offset": 0,
        "replay": True,
    }
    assert poll(catalog)["next_offset"] == 20
    assert state_rows(runtime)[0] == 20
    assert {row[0] for row in state_rows(runtime)[1]} == {"inbound-1", "historical"}
    with pytest.raises(OperationError):
        poll(catalog, replay=True)
    assert len(provider.calls) == 4


def test_replay_before_initialization_does_not_initialize_normal_cursor(configured):
    runtime, provider, catalog = configured
    provider.responses = [response(envelope())]
    poll(catalog, offset=0, replay=True)
    assert state_rows(runtime)[0] is None
    with pytest.raises(OperationError):
        poll(catalog)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("status", [204, 409, 401, 429, 500])
def test_provider_status_keeps_state_or_commits_requested_zero(configured, status):
    runtime, provider, catalog = configured
    provider.responses = [ProviderResponse(status)]
    if status == 204:
        poll(catalog, offset=0)
        assert state_rows(runtime) == (0, [])
    else:
        with pytest.raises(OperationError):
            poll(catalog, offset=0)
        assert state_rows(runtime) == (None, [])
    assert len(provider.calls) == 1


def test_status_only_page_does_not_make_replyable_sources(configured):
    runtime, provider, catalog = configured
    status = {
        "id": "outbound-1",
        "recipient_id": "user:creator",
        "timestamp": "345",
        "status": "read",
    }
    provider.responses = [response(envelope(messages=[], statuses=[status]))]
    result = poll(catalog, offset=0)
    assert result["updates"][0]["entry"][0]["changes"][0]["value"]["statuses"] == [
        status
    ]
    assert state_rows(runtime) == (17, [])


@pytest.mark.parametrize(
    "kind", ["text", "reaction", "image", "audio", "video", "document", "sticker"]
)
@pytest.mark.parametrize("ingress", [True, False])
def test_known_projection_withholding_and_quoted_context(kind, ingress):
    item = message(kind)
    item["context"] = {
        "id": "quoted-1",
        "from": "agent:provider-1",
        "unknown": "secret-canary",
    }
    item[kind]["unknown"] = "secret-canary"
    data = envelope(messages=[item])
    data["next_extra"] = "secret-canary"
    data["entry"][0]["unknown"] = "secret-canary"
    value = data["entry"][0]["changes"][0]["value"]
    value["contacts"][0]["profile"]["unknown"] = "secret-canary"
    value["unknown"] = "secret-canary"
    updates, sources, cursor = project_page(response(data), 0, "provider-1", ingress)
    assert cursor == 17 and sources == [("inbound-1", "user:creator", "123", kind)]
    projected = updates[0]["entry"][0]["changes"][0]["value"]["messages"][0]
    assert projected["context"] == {"id": "quoted-1", "from": "agent:provider-1"}
    assert "secret-canary" not in json.dumps(updates)
    assert "next_offset" not in updates[0]
    if kind not in ("text", "reaction") and not ingress:
        assert projected[kind] == {"withheld": True}
        for secret in (
            "media-1",
            "private caption",
            "private.pdf",
            "mime_type",
            "sha256",
        ):
            assert secret not in json.dumps(updates)
    else:
        assert projected[kind] == {
            k: v for k, v in item[kind].items() if k != "unknown"
        }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(object="other"),
        lambda d: d.update(next_offset=0),
        lambda d: d.update(next_offset=True),
        lambda d: d.update(next_offset=2**63),
        lambda d: d.update(next_offset=1.0),
        lambda d: d["entry"][0].update(id="other"),
        lambda d: d["entry"][0]["changes"][0].update(field="other"),
        lambda d: d["entry"][0]["changes"][0]["value"].update(contacts={}),
        lambda d: d["entry"][0]["changes"][0]["value"]["messages"][0].update(
            timestamp="NaN"
        ),
        lambda d: d["entry"][0]["changes"][0]["value"]["messages"][0].update(
            type="unsupported"
        ),
        lambda d: d["entry"][0]["changes"][0]["value"]["messages"][0].update(
            from_="ignored", image={}
        ),
        lambda d: d["entry"][0]["changes"][0]["value"]["messages"][0].update(
            context={"id": "x"}
        ),
        lambda d: d["entry"][0]["changes"][0]["value"]["messages"][0].update(
            timestamp="1" * 513
        ),
        lambda d: d["entry"][0]["changes"][0]["value"].update(
            messages=[message(id=str(i)) for i in range(101)]
        ),
    ],
)
def test_bad_pages_never_publish_any_state(configured, mutation, capsys):
    runtime, provider, catalog = configured
    data = envelope()
    mutation(data)
    provider.responses = [response(data)]
    with pytest.raises(OperationError) as error:
        poll(catalog, offset=0)
    assert error.value.code == "upstream_protocol_error"
    assert state_rows(runtime) == (None, [])
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "body", [b'{"object":1,"object":2}', b'{"next_offset":NaN}', b"[]", b"\xff"]
)
def test_bad_json_is_safe_and_keeps_cursor(configured, body):
    runtime, provider, catalog = configured
    provider.responses = [ProviderResponse(200, body)]
    with pytest.raises(OperationError) as error:
        poll(catalog, offset=0)
    assert error.value.code == "upstream_protocol_error"
    assert state_rows(runtime) == (None, [])


def test_source_conflict_rolls_back_new_sources_and_cursor(configured):
    runtime, provider, catalog = configured
    provider.responses = [response(envelope())]
    poll(catalog, offset=0)
    changed = message()
    changed["from"] = "user:other"
    provider.responses = [response(envelope(30, messages=[message(id="new"), changed]))]
    with pytest.raises(OperationError) as error:
        poll(catalog)
    assert error.value.domain_code == "source_conflict"
    assert state_rows(runtime) == (17, [("inbound-1", "user:creator", "123", "text")])


def test_duplicate_source_is_idempotent_within_one_page(configured):
    runtime, provider, catalog = configured
    provider.responses = [response(envelope(messages=[message(), message()]))]
    poll(catalog, offset=0)
    assert len(state_rows(runtime)[1]) == 1


def test_capacity_retains_existing_rows_and_rejects_whole_new_page(configured):
    runtime, provider, catalog = configured
    with runtime.state.database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany(
            "INSERT INTO sources VALUES(?,?,?,?,?)",
            (
                ("provider-1", f"old-{i}", "user:creator", "123", "text")
                for i in range(250_000)
            ),
        )
        db.commit()
    provider.responses = [
        response(envelope(messages=[message(id="old-0")])),
        response(envelope(30, messages=[message(id="new")])),
    ]
    poll(catalog, offset=0)
    with pytest.raises(OperationError) as error:
        poll(catalog)
    assert error.value.code == "resource_exhausted"
    with runtime.state.database() as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 250_000
        assert db.execute("SELECT normal_offset FROM agents").fetchone()[0] == 17


def test_preflight_is_read_only_and_denied_policy_never_polls(configured):
    runtime, provider, catalog = configured
    before = {
        p.relative_to(runtime.state.path): p.read_bytes()
        for p in runtime.state.path.rglob("*")
        if p.is_file()
    }
    assert catalog.check_operation(
        "whatsapp:receive_updates", {"account": "first", "offset": 0}
    )["allowed"]
    after = {
        p.relative_to(runtime.state.path): p.read_bytes()
        for p in runtime.state.path.rglob("*")
        if p.is_file()
    }
    assert before == after and not provider.calls
    from arbiter_whatsapp.config import WhatsAppPolicy

    runtime.policies["default"] = WhatsAppPolicy(updates=False)
    for entry in (catalog.invoke_operation, catalog.check_operation):
        with pytest.raises(OperationError) as error:
            entry("whatsapp:receive_updates", {"account": "first", "offset": 0})
        assert error.value.code == "policy_denied"
    assert not provider.calls


def test_storage_commit_failure_never_returns_update_or_cursor(configured, monkeypatch):
    runtime, provider, catalog = configured
    provider.responses = [response(envelope())]

    def fail(*args):
        raise OperationError("plugin_error", "Safe synthetic storage failure")

    monkeypatch.setattr(runtime.state, "commit_poll", fail)
    with pytest.raises(OperationError):
        poll(catalog, offset=0)
    assert state_rows(runtime) == (None, [])


# Child code uses the real catalog and store. The parent kills the process at
# the actual SQLite commit boundary; no context-manager cleanup can run.
WORKER = r"""
from contextlib import contextmanager
import json, sys, time
from pathlib import Path
from arbiter_server.services import OperationCatalog, RuntimeRegistry, ServicePluginContext, ServiceRuntimeContext
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp import WhatsAppRuntime, WhatsAppServicePlugin
from arbiter_whatsapp.state import StateStore, reference_key
from arbiter_whatsapp.transport import ProviderResponse
root, mode, marker, fixture = sys.argv[1:]
storage = PluginStorage(plugin_name='whatsapp', root=Path(root))
state = StateStore(storage)
def wait_for_kill():
    Path(marker).write_text('ready')
    while True: time.sleep(1)
if mode == 'lock':
    with state.lock(reference_key('whatsapp/first') + '.poll'):
        wait_for_kill()
else:
    class Provider:
        def request(self, *args, **kwargs):
            return ProviderResponse(200, Path(fixture).read_bytes())
    runtime = WhatsAppRuntime({'first': {'credential': 'whatsapp/first'}}, {'default': {}},
        ServiceRuntimeContext(dependencies={'plugin_storage':storage, 'whatsapp_client':Provider()}))
    original = runtime.state.database
    class CommitBoundary:
        def __init__(self, db): self.db = db
        def __getattr__(self, key): return getattr(self.db, key)
        def commit(self):
            if mode == 'before': wait_for_kill()
            self.db.commit()
            if mode == 'after': wait_for_kill()
    @contextmanager
    def database(**kwargs):
        with original(**kwargs) as db: yield CommitBoundary(db)
    runtime.state.database = database
    catalog = OperationCatalog([WhatsAppServicePlugin()],
        ServicePluginContext(RuntimeRegistry({'whatsapp':runtime})),
        max_account_preview_limit=8, max_operation_preview_limit=8)
    catalog.invoke_operation('whatsapp:receive_updates', {'account':'first', 'offset':0})
    raise RuntimeError('worker missed its requested commit boundary')
"""


def wait_ready(process, marker):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if marker.exists():
            return
        if process.poll() is not None:
            out, err = process.communicate()
            pytest.fail(f"Worker exited before boundary: {out!r} {err!r}")
        time.sleep(0.02)
    pytest.fail("Worker did not reach the bounded persistence boundary")


def start_worker(tmp_path, mode):
    marker = tmp_path / f"{mode}.ready"
    fixture = tmp_path / "page.json"
    fixture.write_text(json.dumps(envelope(7)))
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER, str(tmp_path), mode, str(marker), str(fixture)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, marker


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_killed_poll_has_atomic_sources_cursor_and_replay_recovery(
    configured, tmp_path, boundary
):
    runtime, provider, catalog = configured
    process, marker = start_worker(tmp_path, boundary)
    try:
        wait_ready(process, marker)
    finally:
        process.kill()
        out, err = process.communicate(timeout=10)
    assert not out and not err
    assert state_rows(runtime) == (
        (None, [])
        if boundary == "before"
        else (7, [("inbound-1", "user:creator", "123", "text")])
    )
    provider.responses = [response(envelope(7))]
    if boundary == "after":
        assert poll(catalog, offset=0, replay=True)["next_offset"] == 7
    else:
        assert poll(catalog, offset=0)["next_offset"] == 7
    assert state_rows(runtime) == (7, [("inbound-1", "user:creator", "123", "text")])
    assert b"Hello" not in (runtime.state.path / "state.sqlite").read_bytes()
    assert (
        b"credential-canary" not in (runtime.state.path / "state.sqlite").read_bytes()
    )


def test_separate_process_poll_lock_excludes_runtime_and_releases_on_exit(
    configured, tmp_path
):
    runtime, provider, catalog = configured
    process, marker = start_worker(tmp_path, "lock")
    try:
        wait_ready(process, marker)
        with pytest.raises(OperationError) as error:
            poll(catalog, offset=0)
        assert error.value.code == "conflict"
        assert not provider.calls and state_rows(runtime) == (None, [])
    finally:
        process.kill()
        process.communicate(timeout=10)
    provider.responses = [ProviderResponse(204)]
    assert poll(catalog, offset=0)["next_offset"] == 0
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "kind,field,bad",
    [
        ("image", "sha256", "not-a-digest"),
        ("image", "id", ""),
        ("audio", "voice", "true"),
        ("sticker", "animated", True),
        ("document", "filename", 123),
        ("text", "body", "\ud800"),
    ],
)
@pytest.mark.parametrize("ingress", [True, False])
def test_malformed_known_payload_rejects_even_when_withheld(kind, field, bad, ingress):
    item = message(kind)
    item[kind][field] = bad
    with pytest.raises(OperationError) as error:
        project_page(response(envelope(messages=[item])), 0, "provider-1", ingress)
    assert error.value.code == "upstream_protocol_error"


def test_media_policy_is_applied_through_real_catalog(configured):
    from arbiter_whatsapp.config import WhatsAppPolicy

    runtime, provider, catalog = configured
    runtime.policies["default"] = WhatsAppPolicy(media_ingress=False)
    provider.responses = [response(envelope(messages=[message("document")]))]
    result = poll(catalog, offset=0)
    assert result["updates"][0]["entry"][0]["changes"][0]["value"]["messages"][0][
        "document"
    ] == {"withheld": True}
    assert state_rows(runtime) == (
        17,
        [("inbound-1", "user:creator", "123", "document")],
    )


def test_sqlite_full_rolls_back_sources_and_cursor(configured, monkeypatch):
    import sqlite3

    runtime, provider, catalog = configured
    original = sqlite3.connect

    class Full(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("database or disk is full")

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=Full)

    provider.responses = [response(envelope())]
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(OperationError) as error:
            poll(catalog, offset=0)
    assert error.value.code == "resource_exhausted"
    assert state_rows(runtime) == (None, [])


def test_missing_and_corrupt_agent_state_fails_closed(configured):
    runtime, provider, catalog = configured
    with pytest.raises(OperationError):
        runtime.state.poll_offset("missing-provider", 0, False)
    with pytest.raises(OperationError):
        runtime.state.commit_poll("missing-provider", 0, 1, [], False)
    with runtime.state.database() as db:
        db.execute("UPDATE agents SET normal_offset=-1")
    with pytest.raises(OperationError) as error:
        poll(catalog)
    assert error.value.domain_code == "private_state_unavailable"
    assert not provider.calls


def test_changed_cursor_during_provider_request_rejects_commit(configured, monkeypatch):
    runtime, provider, catalog = configured

    def changed(*args, **kwargs):
        with runtime.state.database() as db:
            db.execute("UPDATE agents SET normal_offset=99")
        return response(envelope(100))

    monkeypatch.setattr(provider, "request", changed)
    with pytest.raises(OperationError) as error:
        poll(catalog, offset=0)
    assert error.value.domain_code == "cursor_conflict"
    assert state_rows(runtime) == (99, [])


def test_invalid_account_and_contact_without_optional_profile(configured):
    runtime, provider, _ = configured
    with pytest.raises(OperationError):
        receive_updates(runtime, "", 0)
    data = envelope()
    del data["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]
    projected, _, _ = project_page(response(data), 0, "provider-1", True)
    assert projected[0]["entry"][0]["changes"][0]["value"]["contacts"] == [
        {"wa_id": "user:creator"}
    ]
    assert not provider.calls
