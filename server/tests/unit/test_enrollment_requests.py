"""Public registration cannot create trust or reset a pending lifetime."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import hashlib
import os
from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from starlette.applications import Starlette

from arbiter_server import enrollment as e
from arbiter_server.errors import OperationError
from server.tests.unit.test_artifact_http_admission import available
from server.tests.unit.test_native_http_protocol import _asgi_request, _client

ROUTE = "/api/v1/enrollment/requests"


def registration_app(tmp_path, **limits):
    app = _client(tmp_path, **limits)._app
    requests = cast(
        e.EnrollmentRequests, cast(Starlette, app).state.enrollment_requests
    )
    return app, requests


def advertisement() -> dict[str, object]:
    def encode(key):
        return base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()

    return dict(
        version=1,
        selector=base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("="),
        name="Agent Smith",
        access_spki=encode(ec.generate_private_key(ec.SECP256R1())),
        portable_spki=encode(ec.generate_private_key(ec.SECP256R1())),
        recipient_spki=encode(x25519.X25519PrivateKey.generate()),
    )


def test_original_deadlines_immutable_keys_and_reopen():
    now = [0.0]
    requests = e.EnrollmentRequests(clock=lambda: now[0])
    raw = json.dumps(advertisement()).encode()
    with pytest.raises(OperationError, match="Enrollment is closed"):
        requests.register(raw)
    requests.open_window()
    original = requests.register(raw)
    now[0] = 599
    requests.open_window()
    assert requests.register(raw) is original
    assert original.created_at == 0 and original.deadline == 600
    assert e.decode_advertisement(raw).digest == original.advertisement.digest
    assert original.advertisement.digest == hashlib.sha256(raw).hexdigest()
    with pytest.raises(FrozenInstanceError):
        setattr(original, "deadline", 999)
    changed = json.loads(raw)
    changed["name"] = "different untrusted label"
    with pytest.raises(OperationError) as error:
        requests.register(json.dumps(changed).encode())
    assert error.value.code == "conflict"
    assert requests.pending() == (original,)
    now[0] = 600
    assert requests.pending() == ()
    # A new registration after disposal gets a new revision, never revives an
    # expired approval context. The next local-control slice checks that revision.
    replacement = requests.register(raw)
    assert replacement.revision != original.revision and replacement.deadline == 1200
    requests.reset()
    assert requests.pending() == ()
    with pytest.raises(OperationError):
        requests.register(raw)


def test_count_bound_is_atomic_and_duplicates_consume_no_new_slot():
    requests = e.EnrollmentRequests()
    requests.open_window()
    bodies = [json.dumps(advertisement()).encode() for _ in range(40)]

    def register(raw):
        try:
            return requests.register(raw)
        except OperationError as error:
            assert error.code == "resource_exhausted"
            assert error.to_dict()["error"]["retry"] == {
                "transient": "yes",
                "replay": "safe",
            }
            return None

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(register, bodies))
    assert sum(result is not None for result in results) == 20
    assert len(requests.pending()) == 20
    accepted = next(body for body, result in zip(bodies, results) if result is not None)
    assert register(accepted) in requests.pending()
    assert len(requests.pending()) == 20


@pytest.mark.parametrize("limit", [0, -1, True, "20", 1.5])
def test_invalid_configured_capacity(limit):
    with pytest.raises(ValueError, match="positive integer"):
        e.EnrollmentRequests(limit)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("name", ""),
        ("name", " leading"),
        ("name", "bad\nname"),
        ("name", "x" * 257),
        ("name", "\ud800"),
        ("selector", "A" * 21 + "B"),
        ("selector", "a/../../"),
        ("access_spki", "x" * 1000),
        ("recipient_spki", None),
        ("psk", "never accepted"),
    ],
)
def test_strict_advertisement_shape_and_fields(field, value):
    data = advertisement()
    data[field] = value
    with pytest.raises(OperationError) as error:
        e.decode_advertisement(json.dumps(data).encode())
    assert error.value.code == "validation_error"
    assert error.value.message == "Invalid enrollment advertisement."


def test_duplicate_fields_keys_and_noncanonical_der():
    data = advertisement()
    raw = json.dumps(data)
    for body in (
        b"[]",
        b"null",
        b"",
        b"{" * 1000,
        b" " * (e.MAX_ADVERTISEMENT_BYTES + 1),
        raw.replace('"version": 1', '"version": 1, "version": 1').encode(),
    ):
        with pytest.raises(OperationError):
            e.decode_advertisement(body)
    data["portable_spki"] = data["access_spki"]
    with pytest.raises(OperationError):
        e.decode_advertisement(json.dumps(data).encode())
    data = advertisement()
    data["recipient_spki"] = data["access_spki"]
    with pytest.raises(OperationError):
        e.decode_advertisement(json.dumps(data).encode())
    # Reject valid DER plus trailing data and noncanonical base64 padding.
    data = advertisement()
    for field, value in (
        ("access_spki", str(data["access_spki"]) + "="),
        ("recipient_spki", base64.b64encode(b"not a key" + b"x" * 35).decode()),
    ):
        broken = dict(data, **{field: value})
        with pytest.raises(OperationError):
            e.decode_advertisement(json.dumps(broken).encode())


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-8-sig",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
    ],
)
def test_non_utf8_wire_encodings_rejected(tmp_path, encoding):
    raw = json.dumps(advertisement()).encode(encoding)
    with pytest.raises(OperationError) as error:
        e.decode_advertisement(raw)
    assert error.value.code == "validation_error"
    app, requests = registration_app(tmp_path)
    requests.open_window()

    async def exercise():
        response = await _asgi_request(app, "POST", ROUTE, body_chunks=[raw])
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert requests.pending() == ()

    asyncio.run(exercise())


def test_utf8_unicode_name_preserves_exact_wire_digest():
    data = dict(advertisement(), name="Agent Ö雪")
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    parsed = e.decode_advertisement(raw)
    assert parsed.name == "Agent Ö雪"
    assert parsed.digest == hashlib.sha256(raw).hexdigest()


def test_window_expiry_during_decode_cannot_admit(monkeypatch):
    now = [0.0]
    requests = e.EnrollmentRequests(clock=lambda: now[0])
    requests.open_window()
    original = e.decode_advertisement

    def expire(raw):
        value = original(raw)
        now[0] = 600
        return value

    monkeypatch.setattr(e, "decode_advertisement", expire)
    with pytest.raises(OperationError) as error:
        requests.register(json.dumps(advertisement()).encode())
    assert error.value.code == "policy_denied" and requests.pending() == ()


@pytest.mark.parametrize("restart", [False, True])
def test_reopen_during_decode_does_not_resurrect_old_context(monkeypatch, restart):
    now = [0.0]
    requests = e.EnrollmentRequests(clock=lambda: now[0])
    requests.open_window()
    original = e.decode_advertisement

    def reopen(raw):
        value = original(raw)
        if restart:
            requests.reset()
        else:
            now[0] = 600
        requests.open_window()
        return value

    monkeypatch.setattr(e, "decode_advertisement", reopen)
    with pytest.raises(OperationError) as error:
        requests.register(json.dumps(advertisement()).encode())
    assert error.value.code == "policy_denied" and requests.pending() == ()


def test_closed_http_never_reads_or_opens_remotely(tmp_path):
    app, requests = registration_app(tmp_path)

    async def exercise():
        reads = []
        response = await _asgi_request(
            app,
            "POST",
            ROUTE,
            body_chunks=[b"never read"],
            on_receive=lambda: reads.append(True),
        )
        assert response.status_code == 403 and reads == []
        assert "local administration" in response.json()["error"]["message"]
        assert (await _asgi_request(app, "POST", ROUTE + "/open")).status_code == 404
        assert requests.pending() == ()

    asyncio.run(exercise())


def test_open_http_bounds_and_failure_capacity_recovery(tmp_path):
    app, requests = registration_app(tmp_path, max_request_body_bytes=2048)
    requests.open_window()

    async def exercise():
        for body, status in ((b"bad json", 422), (b"x" * 2049, 413)):
            response = await _asgi_request(
                app,
                "POST",
                ROUTE,
                body_chunks=[body],
                extra_headers=[(b"content-length", b"1")],
            )
            assert response.status_code == status
        raw = json.dumps(advertisement()).encode()
        response = await _asgi_request(
            app, "POST", ROUTE, body_chunks=[raw[:10], raw[10:]]
        )
        assert response.status_code == 200 and response.json()["status"] == "pending"
        assert set(response.json()) == {"selector", "request_digest", "status"}
        assert len(requests.pending()) == 1
        await available(app)

    asyncio.run(exercise())


def test_registration_admits_before_body_and_cancellation_recovers(tmp_path):
    app, requests = registration_app(tmp_path, max_blocking_workers=1)
    requests.open_window()

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def hold():
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
                await release.wait()

        task = asyncio.create_task(
            _asgi_request(
                app,
                "POST",
                ROUTE,
                body_chunks=[b'{"version":', b"1}"],
                before_receive=hold,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            reads = []
            response = await _asgi_request(
                app,
                "POST",
                ROUTE,
                body_chunks=[b"not admitted"],
                on_receive=lambda: reads.append(True),
            )
            assert response.status_code == 503 and reads == []
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                await task
        await available(app)
        assert requests.pending() == ()

    asyncio.run(asyncio.wait_for(exercise(), 5))


def test_registration_body_timeout_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "REGISTRATION_TIMEOUT", 0.02)
    app, requests = registration_app(tmp_path, max_blocking_workers=1)
    requests.open_window()

    async def exercise():
        async def hold():
            await asyncio.Event().wait()

        response = await _asgi_request(
            app, "POST", ROUTE, body_chunks=[b"held"], before_receive=hold
        )
        assert response.status_code == 400
        await available(app)

    asyncio.run(asyncio.wait_for(exercise(), 3))
