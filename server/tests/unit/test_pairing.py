"""Real Noise peers exercise bounded admission and live approval ownership."""

import asyncio
import base64
from dataclasses import replace
import json
import os
import threading
from typing import Any, cast

from noise.connection import NoiseConnection
from omegaconf import OmegaConf
import pytest

from arbiter_server import pairing as p
from arbiter_server.config import AppConfig
from arbiter_server.enrollment import EnrollmentRequests
from arbiter_server.errors import OperationError
from arbiter_server.limits import NativeTransportLimitsConfig as Limits
from server.tests.unit.test_enrollment_requests import advertisement


def approved(*, clock=None):
    requests = EnrollmentRequests(**({"clock": clock} if clock else {}))
    requests.open_window()
    raw = json.dumps(advertisement()).encode()
    pending = requests.register(raw)
    secret = os.urandom(32)
    selection = dict(
        selector=pending.advertisement.selector,
        revision=pending.revision,
        request_digest=pending.advertisement.digest,
    )
    requests.control(
        "approve",
        dict(
            selection,
            psk=base64.urlsafe_b64encode(secret).decode().rstrip("="),
            grants=[],
        ),
        set(),
    )
    context = requests.approved(selection["selector"])
    assert context is not None
    return requests, context, selection


def initiator(context, *, secret=None, advertised=None, payload=None):
    advertised = advertised or context.request.advertisement
    peer = NoiseConnection.from_name(p.PROFILE)
    peer.set_as_initiator()
    peer.set_psks(psk=context.secret if secret is None else secret)
    peer.set_prologue(p.prologue(advertised))
    peer.start_handshake()
    frame = bytes(
        peer.write_message(p.first_payload(advertised) if payload is None else payload)
    )
    return peer, frame


async def connected(responder, context):
    peer, first = initiator(context)
    lease = responder.reserve()
    session = await responder.start(
        lease, context.request.advertisement.selector, first
    )
    assert isinstance(session, p.PairingSession)
    assert bytes(peer.read_message(session.response)) == p.first_payload(
        context.request.advertisement, server=True
    )
    assert peer.get_handshake_hash() == session.handshake_hash
    return lease, session, peer, first


def test_actual_noise_peer_role_binding_replay_and_both_directions():
    async def run():
        requests, context, _ = approved()
        responder = p.PairingResponder(requests)
        try:
            lease, session, peer, first = await connected(responder, context)
            replay_lease = responder.reserve()
            replay = await responder.start(
                replay_lease, context.request.advertisement.selector, first
            )
            assert isinstance(replay, p.ReplayedHandshake)
            assert replay.response == session.response and not hasattr(replay, "noise")
            replay_lease.close()
            assert (
                await responder.message(session, peer.encrypt(b"client proof"))
                == b"client proof"
            )
            assert (
                peer.decrypt(
                    await responder.message(session, b"server proof", encrypt=True)
                )
                == b"server proof"
            )
            assert base64.urlsafe_b64encode(context.secret).decode().rstrip(
                "="
            ) not in repr(session)
            lease.close()
            with pytest.raises(ValueError):
                await responder.start(
                    responder.reserve(), context.request.advertisement.selector, first
                )
            # Duplicate/replayed traffic consumed only the original one attempt.
            for _ in range(4):
                next_lease, _, _, _ = await connected(responder, context)
                next_lease.close()
            assert requests.approved(context.request.advertisement.selector) is None
            assert responder._attempts == {}
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid",
    [
        "psk",
        "digest",
        "selector",
        "access",
        "portable",
        "recipient",
        "empty",
        "role",
        "truncated",
        "garbage",
    ],
)
def test_unauthenticated_or_substituted_first_flights_spend_no_attempt(invalid):
    async def run():
        requests, context, _ = approved()
        responder = p.PairingResponder(requests)
        changed = context.request.advertisement
        kwargs: dict[str, Any] = {}
        if invalid == "psk":
            kwargs["secret"] = os.urandom(32)
        elif invalid in {"digest", "selector", "access", "portable", "recipient"}:
            field = invalid if invalid in {"digest", "selector"} else invalid + "_spki"
            value = (
                "0" * 64
                if invalid == "digest"
                else "A" * 22 if invalid == "selector" else b"different proposed key"
            )
            kwargs["advertised"] = replace(changed, **{field: value})
        elif invalid in {"empty", "role"}:
            kwargs["payload"] = (
                b"" if invalid == "empty" else p.first_payload(changed, server=True)
            )
        _, frame = initiator(context, **kwargs)
        if invalid == "truncated":
            frame = frame[:-1]
        elif invalid == "garbage":
            frame = b"invalid"
        try:
            for _ in range(6):
                with pytest.raises(ValueError, match="unavailable or invalid"):
                    await responder.start(responder.reserve(), changed.selector, frame)
            for _ in range(5):
                lease, _, _, _ = await connected(responder, context)
                lease.close()
            assert requests.approved(changed.selector) is None
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["reject", "reset", "expire"])
def test_live_transition_during_crypto_prevents_admission(monkeypatch, transition):
    async def run():
        now = [0.0]
        requests, context, selection = approved(clock=lambda: now[0])
        responder = p.PairingResponder(requests, clock=lambda: now[0])
        _, frame = initiator(context)
        entered, release = threading.Event(), threading.Event()
        original = NoiseConnection.read_message

        def blocked(noise, data):
            entered.set()
            assert release.wait(5)
            return original(noise, data)

        monkeypatch.setattr(NoiseConnection, "read_message", blocked)
        task = asyncio.create_task(
            responder.start(responder.reserve(), selection["selector"], frame)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if transition == "reject":
                requests.control("reject", cast(dict[str, object], selection), set())
            elif transition == "reset":
                requests.reset()
            else:
                now[0] = 600
            release.set()
            with pytest.raises(ValueError):
                await task
            assert requests.approved(selection["selector"]) is None
            assert not responder._leases
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["first", "message"])
@pytest.mark.parametrize("capacity", [1, 3])
def test_cancellation_retains_both_capacities_until_actual_crypto_finishes(
    monkeypatch, phase, capacity
):
    async def run():
        requests, context, selection = approved()
        responder = p.PairingResponder(
            requests, Limits(max_connections=capacity, max_handshakes=capacity)
        )
        session = peer = None
        original_first = b""
        if phase == "message":
            lease, session, peer, original_first = await connected(responder, context)
        else:
            lease = responder.reserve()
        entered, release = threading.Event(), threading.Event()
        method = "read_message" if phase == "first" else "decrypt"
        original = getattr(NoiseConnection, method)

        def blocked(noise, data):
            entered.set()
            assert release.wait(5)
            return original(noise, data)

        monkeypatch.setattr(NoiseConnection, method, blocked)
        _, first = initiator(context)
        task: asyncio.Task[Any]
        if phase == "first":
            task = asyncio.create_task(
                responder.start(lease, selection["selector"], first)
            )
        else:
            assert session is not None and peer is not None
            task = asyncio.create_task(
                responder.message(session, peer.encrypt(b"held message"))
            )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            # The event loop remains runnable while real crypto work is held.
            await asyncio.wait_for(asyncio.sleep(0), 0.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            if capacity == 1:
                with pytest.raises(OperationError):
                    responder.reserve()
            else:
                if phase == "message":
                    with pytest.raises(ValueError):
                        await responder.start(
                            responder.reserve(), selection["selector"], original_first
                        )
                with pytest.raises(OperationError):
                    await responder.start(
                        responder.reserve(), selection["selector"], first
                    )
            assert len(responder._leases) == 1
            release.set()

            async def drained():
                while responder._leases:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(drained(), 5)
            fresh, _, _, _ = await connected(responder, context)
            fresh.close()
            assert requests.approved(selection["selector"]) is context
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            responder.close()

    asyncio.run(run())


def test_concurrent_duplicate_never_operates_a_second_mutable_handshake(monkeypatch):
    async def run():
        requests, context, selection = approved()
        responder = p.PairingResponder(requests)
        entered, release = threading.Event(), threading.Event()
        original = NoiseConnection.read_message
        calls = []

        def blocked(noise, data):
            calls.append(noise)
            entered.set()
            assert release.wait(5)
            return original(noise, data)

        monkeypatch.setattr(NoiseConnection, "read_message", blocked)
        _, first = initiator(context)
        lease = responder.reserve()
        task = asyncio.create_task(responder.start(lease, selection["selector"], first))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(OperationError):
                await responder.start(responder.reserve(), selection["selector"], first)
            assert len(calls) == 1
            release.set()
            session = await task
            assert isinstance(session, p.PairingSession)
            duplicate = responder.reserve()
            replay = await responder.start(duplicate, selection["selector"], first)
            assert (
                isinstance(replay, p.ReplayedHandshake)
                and replay.response == session.response
            )
            assert len(calls) == 1
            duplicate.close()
            lease.close()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            responder.close()

    asyncio.run(run())


def test_crypto_capacity_rejection_and_shutdown_release_real_owners(monkeypatch):
    async def run():
        requests, context, selection = approved()
        responder = p.PairingResponder(
            requests, Limits(max_connections=2, max_handshakes=1)
        )
        entered, release = threading.Event(), threading.Event()
        original = NoiseConnection.read_message

        def blocked(noise, data):
            entered.set()
            assert release.wait(5)
            return original(noise, data)

        monkeypatch.setattr(NoiseConnection, "read_message", blocked)
        _, first = initiator(context)
        task = asyncio.create_task(
            responder.start(responder.reserve(), selection["selector"], first)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(OperationError):
                await responder.start(responder.reserve(), selection["selector"], first)
            assert len(responder._leases) == 1
            responder.close()
            assert len(responder._leases) == 1
            with pytest.raises(OperationError):
                responder.reserve()
            release.set()
            with pytest.raises(ValueError):
                await task
            assert not responder._leases
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("encrypt", [False, True])
def test_noise_maximum_frame_and_overrun_fail_closed(encrypt):
    async def run():
        requests, context, _ = approved()
        responder = p.PairingResponder(requests)
        try:
            _, session, peer, _ = await connected(responder, context)
            data = b"x" * 65519
            if encrypt:
                result = await responder.message(session, data, encrypt=True)
                assert len(result) == 65535 and peer.decrypt(result) == data
                invalid = data + b"x"
            else:
                assert await responder.message(session, peer.encrypt(data)) == data
                invalid = b"x" * 65536
            with pytest.raises(ValueError):
                await responder.message(session, invalid, encrypt=encrypt)
            assert not responder._leases
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("encrypt", [False, True])
def test_total_exchange_budget_includes_handshake_and_exact_boundary(encrypt):
    async def run():
        requests, context, _ = approved()
        responder = p.PairingResponder(requests, Limits(max_handshake_bytes=500))
        try:
            _, session, peer, _ = await connected(responder, context)
            used = session.sent if encrypt else session.received
            data = b"x" * (500 - used - 16)
            wire = data if encrypt else peer.encrypt(data)
            result = await responder.message(session, wire, encrypt=encrypt)
            assert (peer.decrypt(result) if encrypt else result) == data
            assert (session.sent if encrypt else session.received) == 500
            with pytest.raises(ValueError):
                await responder.message(
                    session, b"x" if encrypt else peer.encrypt(b"x"), encrypt=encrypt
                )
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_connections", 0),
        ("max_handshakes", True),
        ("max_noise_frame_bytes", 65536),
        ("max_handshake_bytes", -1),
        ("handshake_timeout_seconds", float("nan")),
        ("handshake_timeout_seconds", False),
    ],
)
def test_native_limits_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        Limits(**{field: value})


def test_native_limits_have_configured_approved_defaults_and_copy_validation():
    config = OmegaConf.structured(AppConfig)
    assert config.arbiter.limits.native_transport.max_connections == 16
    assert config.arbiter.limits.native_transport.max_handshakes == 4
    assert config.arbiter.limits.native_transport.handshake_timeout_seconds == 10
    limits = Limits()
    limits.max_connections = 0
    with pytest.raises(ValueError):
        p.PairingResponder(EnrollmentRequests(), limits)


def test_deadline_before_dispatch_does_not_leak_reserved_capacity():
    async def run():
        requests, context, selection = approved()
        times = iter([0.0, 0.0, 2.0])
        responder = p.PairingResponder(
            requests,
            Limits(max_connections=1, handshake_timeout_seconds=1),
            clock=lambda: next(times, 2.0),
        )
        _, first = initiator(context)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await responder.start(responder.reserve(), selection["selector"], first)
            assert not responder._leases and not responder._attempts
            responder.reserve().close()
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("replay", [False, True])
def test_expired_lease_or_replay_owner_fails_and_releases_capacity(replay):
    async def run():
        now = [0.0]
        requests, context, selection = approved(clock=lambda: now[0])
        responder = p.PairingResponder(
            requests, Limits(max_connections=2), clock=lambda: now[0]
        )
        if replay:
            lease, _, _, first = await connected(responder, context)
        else:
            lease = responder.reserve()
            _, first = initiator(context)
        now[0] = 10
        try:
            with pytest.raises(ValueError):
                await responder.start(
                    responder.reserve() if replay else lease,
                    selection["selector"],
                    first,
                )
            assert not responder._leases
            if replay:
                state = responder._attempts[selection["selector"]]
                assert state.active is None and len(state.digests) == 1
            responder.reserve().close()
        finally:
            responder.close()

    asyncio.run(run())


def test_fifth_authenticated_failure_discards_pending_secret(monkeypatch):
    async def run():
        requests, context, selection = approved()
        responder = p.PairingResponder(requests)
        try:
            for _ in range(4):
                lease, _, _, _ = await connected(responder, context)
                lease.close()
            _, first = initiator(context)

            def failed_write(_noise, _payload):
                raise RuntimeError("synthetic responder failure")

            monkeypatch.setattr(NoiseConnection, "write_message", failed_write)
            with pytest.raises(ValueError, match="unavailable or invalid"):
                await responder.start(responder.reserve(), selection["selector"], first)
            assert requests.approved(selection["selector"]) is None
            assert requests.pending() == () and not responder._attempts
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["reject", "reset", "expire"])
def test_retained_session_never_bypasses_live_approval(transition):
    async def run():
        now = [0.0]
        requests, context, selection = approved(clock=lambda: now[0])
        responder = p.PairingResponder(requests, clock=lambda: now[0])
        try:
            _, session, peer, _ = await connected(responder, context)
            if transition == "reject":
                requests.control("reject", cast(dict[str, object], selection), set())
            elif transition == "reset":
                requests.reset()
            else:
                now[0] = 600
            with pytest.raises(ValueError):
                await responder.message(session, peer.encrypt(b"must not advance"))
            assert not responder._leases
        finally:
            responder.close()

    asyncio.run(run())


@pytest.mark.parametrize("secret", [b"", b"x" * 31, b"x" * 33])
def test_invalid_psk_never_initializes_library(monkeypatch, secret):
    async def run():
        requests, context, selection = approved()
        requests._approved[selection["selector"]] = replace(context, secret=secret)
        responder = p.PairingResponder(requests)
        calls = []
        monkeypatch.setattr(
            NoiseConnection, "from_name", lambda *args: calls.append(args)
        )
        try:
            with pytest.raises(ValueError):
                await responder.start(responder.reserve(), selection["selector"], b"x")
            assert calls == []
        finally:
            responder.close()

    asyncio.run(run())


def test_authenticated_message_tampering_closes_the_owner():
    async def run():
        requests, context, _ = approved()
        responder = p.PairingResponder(requests)
        try:
            _, session, peer, _ = await connected(responder, context)
            encrypted = peer.encrypt(b"must not be released")
            damaged = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with pytest.raises(ValueError, match="unavailable or invalid"):
                await responder.message(session, damaged)
            assert not responder._leases
            with pytest.raises(ValueError):
                await responder.message(session, encrypted)
        finally:
            responder.close()

    asyncio.run(run())
