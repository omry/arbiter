"""Shared native carrier bounds and lifetime handshake admission."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
import tracemalloc
from typing import Any, cast

import pytest
from wsproto import ConnectionType, WSConnection, events

from arbiter_server.enrollment import EnrollmentRequests
from arbiter_server.errors import OperationError
from arbiter_server.limits import NativeTransportLimitsConfig as Limits
from arbiter_server.pairing import PairingResponder
from arbiter_server.pairing_carrier import PAIRING_PATH, CONTROL_PATH, PairingProtocol
from server.tests.unit.test_pairing_carrier import Transport


def setup(handler, *, target=CONTROL_PATH, **kwargs):
    limits = Limits(**kwargs)
    owner = PairingResponder(EnrollmentRequests(), limits)
    transport = Transport()
    protocol = PairingProtocol(
        None,
        SimpleNamespace(connections=set(), tasks=set()),
        {},
        responder=owner,
        limits=limits,
        continue_pairing=handler if target != CONTROL_PATH else None,
        continue_control=handler if target == CONTROL_PATH else None,
    )
    protocol.connection_made(cast(Any, transport))
    ws = WSConnection(ConnectionType.CLIENT)
    protocol.data_received(ws.send(events.Request(host="localhost", target=target)))
    ws.receive_data(bytes(transport.output))
    assert isinstance(next(ws.events()), events.AcceptConnection)
    transport.output.clear()
    return owner, protocol, transport, ws


@pytest.mark.parametrize(
    "failure", ["coalesced", "overrun", "blocked-write", "shutdown", "idle"]
)
def test_control_bounds_and_deadline(failure):
    async def run():
        async def handler(carrier):
            if failure == "blocked-write":
                await carrier.send(b"synthetic TLS output")
            else:
                await asyncio.Event().wait()

        owner, protocol, transport, ws = setup(handler, handshake_timeout_seconds=0.05)
        try:
            if failure in {"coalesced", "overrun"}:
                number = 4 if failure == "coalesced" else 10
                protocol.data_received(
                    b"".join(
                        ws.send(events.BytesMessage(data=b"x" * 16384))
                        for _ in range(number)
                    )
                )
                if failure == "coalesced":
                    assert not transport.closed
                    assert protocol.queue.qsize() == 1
                    assert len(await protocol.receive()) == 65536
                    assert transport.paused  # TLS still owns the returned input.
                else:
                    assert transport.closed
            elif failure == "blocked-write":
                protocol.pause_writing()
            elif failure == "shutdown":
                protocol.shutdown()
            await asyncio.sleep(0.08)
            assert transport.closed
            assert protocol.task is not None
            with suppress(asyncio.CancelledError):
                await protocol.task
            protocol.connection_lost(None)
            assert not owner._leases
        finally:
            protocol.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_control_and_noise_message_boundaries():
    async def run():
        async def wait_for_input(carrier):
            await asyncio.Event().wait()

        async def exercise(target, cap, accepted, rejected):
            kwargs = {} if cap is None else {"max_websocket_message_bytes": cap}
            owner, protocol, transport, ws = setup(
                wait_for_input if target == CONTROL_PATH else None,
                target=target,
                **kwargs,
            )
            try:
                for parts in (accepted, rejected):
                    for index, size in enumerate(parts):
                        protocol.data_received(
                            ws.send(
                                events.BytesMessage(
                                    data=b"x" * size,
                                    message_finished=index == len(parts) - 1,
                                )
                            )
                        )
                    if accepted and parts == accepted:
                        assert len(await protocol.receive()) == sum(accepted)
                assert transport.closed
            finally:
                protocol.connection_lost(None)
                owner.close()

        noise = PAIRING_PATH + ("A" * 22) + "/pair"
        for target, cap, accepted, rejected in (
            (CONTROL_PATH, None, (65536,), (65537,)),
            (CONTROL_PATH, None, (32768, 32768), (65537,)),
            (noise, None, (), (65536,)),
            (noise, 1024, (1024,), (1025,)),
            (noise, 1024, (512, 512), (512, 513)),
        ):
            await exercise(target, cap, accepted, rejected)

    asyncio.run(run())


def test_control_coalescing_reserves_live_allocation_budget():
    async def run():
        async def wait_for_input(carrier):
            await asyncio.Event().wait()

        owner, protocol, transport, ws = setup(wait_for_input)
        try:
            wire = b"".join(
                ws.send(events.BytesMessage(data=b"x" * 65536)) for _ in range(2)
            )
            tracemalloc.start()
            try:
                protocol.data_received(wire)
                current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            assert peak <= protocol.limits.max_buffered_bytes
            assert current <= protocol.limits.max_buffered_bytes
            assert transport.closed
            assert protocol.queue.empty()
        finally:
            protocol.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("split_callbacks", [False, True])
def test_control_coalescing_preserves_framed_maximum_order(split_callbacks):
    async def run():
        async def wait_for_input(carrier):
            await asyncio.Event().wait()

        owner, protocol, transport, ws = setup(wait_for_input)
        try:
            payloads = [bytes([value]) * 16384 for value in (65, 66, 67, 68)]
            payloads.append(b"e" * 32)
            wire = [ws.send(events.BytesMessage(data=payload)) for payload in payloads]
            if split_callbacks:
                for chunk in wire:
                    protocol.data_received(chunk)
            else:
                protocol.data_received(b"".join(wire))
            expected = b"".join(payloads)
            assert not transport.closed
            assert await protocol.receive() == expected
            assert len(expected) == 65568
        finally:
            protocol.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_control_deadline_starts_once_after_handshake():
    async def run():
        async def handler(carrier):
            await asyncio.Event().wait()

        owner, protocol, _, _ = setup(handler)
        assert protocol.lease is not None
        held = [protocol.lease] + [owner.reserve() for _ in range(3)]
        tasks = [asyncio.create_task(owner._run(lease, lambda: None)) for lease in held]
        await asyncio.sleep(0.05)
        with pytest.raises(OperationError):
            await owner._run(owner.reserve(), lambda: None)
        assert not owner._pool._pending
        # Keep the fake origin integral so exact deadline checks avoid rounding.
        now = [float(int(owner._clock()))]
        owner._clock = lambda: now[0]
        now[0] += 9
        protocol.begin_control()
        assert len(owner._handshakes) == 3
        assert protocol.lease.remaining_seconds == 10
        now[0] += 9
        assert protocol.lease.remaining_seconds == 1
        with pytest.raises(ValueError):
            protocol.begin_control()
        now[0] += 1
        with pytest.raises(ValueError):
            await owner._run(protocol.lease, lambda: None)
        protocol.connection_lost(None)
        owner.close()
        await asyncio.sleep(0)

    asyncio.run(run())
