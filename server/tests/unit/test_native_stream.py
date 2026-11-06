"""Shared pump progress and credits, including actual cancelled worker owners."""

import asyncio
import threading
import time
from typing import cast

from OpenSSL import SSL
import pytest

from arbiter_server.native_stream import CHUNK, RECORD_RESERVE, TLSStream
from server.tests.unit.test_control_carrier import setup


class Connection:
    def __init__(self):
        self.output = []
        self.received = bytearray()
        self.attempts = 0

    def bio_read(self, size):
        if self.output:
            return self.output.pop(0)
        raise SSL.WantReadError()

    def pending(self):
        return 0

    def bio_write(self, data):
        self.received.extend(data)
        return len(data)

    def send(self, data):
        return len(data)

    def recv(self, size):
        raise SSL.WantReadError()

    def shutdown(self):
        return None


def test_want_write_drains_before_retry_and_read_feeds_bounded_chunks():
    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(waiting)
        connection = Connection()
        stream = TLSStream(carrier, cast(SSL.Connection, connection))
        carrier.begin_stream()
        sends = []

        async def capture(wire):
            sends.append(wire)

        carrier.send = capture

        async def receive():
            return b"x" * (CHUNK + 1)

        carrier.receive = receive

        def operation():
            connection.attempts += 1
            if connection.attempts == 1:
                connection.output.append(b"drain first")
                raise SSL.WantWriteError()
            assert sends == [b"drain first"]
            if len(connection.received) < CHUNK + 1:
                raise SSL.WantReadError()
            return b"complete"

        try:
            assert await stream._call(operation) == b"complete"
            assert connection.received == b"x" * (CHUNK + 1)
            assert carrier.credits.fed >= RECORD_RESERVE
            assert carrier.credits.adapter == 0
            assert connection.attempts == 4
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["write-stall", "zero-write", "overrun"])
def test_no_progress_and_pending_overrun_are_finite(failure):
    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(waiting)
        connection = Connection()
        stream = TLSStream(carrier, cast(SSL.Connection, connection))
        carrier.begin_stream()

        def write(data):
            if failure == "write-stall":
                raise SSL.WantWriteError()
            return 0

        connection.send = write
        try:
            if failure == "overrun":
                carrier.credits.pending = carrier.limits.max_buffered_bytes
            with pytest.raises(ValueError, match="progress|limit"):
                await asyncio.wait_for(stream.write(b"payload"), 1)
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_cancelled_worker_retains_credits_and_lease_until_actual_exit():
    entered, release = threading.Event(), threading.Event()
    from cffi import FFI

    ffi = FFI()

    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        async def wait_for_entered():
            for _ in range(2000):
                if entered.is_set():
                    return
                await asyncio.sleep(0.001)
            raise AssertionError("worker did not enter")

        owner, carrier, _, _ = setup(waiting, max_connections=1)
        connection = Connection()
        stream = TLSStream(carrier, cast(SSL.Connection, connection))
        carrier.begin_stream()

        def write(data):
            exported = ffi.from_buffer(data)
            entered.set()
            try:
                assert release.wait(5)
                return min(3, len(data))
            finally:
                del exported

        connection.send = write
        task = asyncio.create_task(stream.write(b"payload"))
        try:
            await wait_for_entered()
            held = carrier.credits.application
            with pytest.raises(ValueError, match="Concurrent"):
                await stream.read(1)
            assert carrier.credits.application == held == 14
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ValueError, match="Concurrent"):
                await stream.read(1)
            carrier.connection_lost(None)
            assert len(owner._leases) == 1
            assert carrier.credits.application == held
            release.set()

            async def settled():
                while owner._leases:
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(settled(), 2)
            lease = owner.reserve()
            lease.close()
        finally:
            release.set()
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("blocked", [False, True])
def test_partial_writes_and_transport_credit_preflight(blocked):
    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        owner, carrier, transport, _ = setup(waiting)
        connection = Connection()
        stream = TLSStream(carrier, cast(SSL.Connection, connection))
        carrier.begin_stream()
        written = bytearray()
        threads = []

        def send(data):
            threads.append(threading.get_ident())
            written.extend(data[:3])
            return min(3, len(data))

        connection.send = send
        connection.shutdown = lambda: threads.append(threading.get_ident())
        try:
            if blocked:
                transport.buffered = carrier.limits.max_buffered_bytes - 1
                with pytest.raises(ValueError, match="limit"):
                    await stream.write(b"payload")
                assert not written  # Reject before another crypto allocation.
            else:
                await stream.write(b"payload")
                assert written == b"payload"
                await stream.close()
                assert carrier.closed
                assert threads and threading.get_ident() not in threads
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.parametrize("direction", ["read", "write"])
def test_abort_wakes_a_stream_blocked_on_either_direction(direction):
    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(waiting)
        connection = Connection()
        stream = TLSStream(carrier, cast(SSL.Connection, connection))
        carrier.begin_stream()

        connection.output = [b"response"] if direction == "write" else []
        if direction == "write":
            carrier.pause_writing()

        async def transfer():
            if direction == "write":
                await stream.write(b"request")
            else:
                await stream.read()

        task = asyncio.create_task(transfer())
        try:
            await asyncio.sleep(0.02)
            assert not task.done()
            carrier.abort()
            with pytest.raises(ValueError, match="closed"):
                await asyncio.wait_for(task, 1)
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_competing_input_owners_cannot_overbook_credits():
    async def run():
        async def waiting(carrier):
            await asyncio.Event().wait()

        owner, carrier, _, _ = setup(waiting, max_buffered_bytes=65536)
        carrier.begin_stream()
        original = carrier.credits.check
        barrier = threading.Barrier(2)
        results = []

        def slow_check(extra=0):
            original(extra)
            time.sleep(0.02)  # Force overlap between preflight and reservation.

        carrier.credits.check = slow_check

        def reserve():
            barrier.wait(timeout=1)
            try:
                carrier.credits.reserve_input(10000)
                results.append("reserved")
            except ValueError:
                results.append("full")

        try:
            await asyncio.gather(asyncio.to_thread(reserve), asyncio.to_thread(reserve))
            assert sorted(results) == ["full", "reserved"]
            original()
        finally:
            carrier.connection_lost(None)
            owner.close()
            await asyncio.sleep(0)

    asyncio.run(run())
