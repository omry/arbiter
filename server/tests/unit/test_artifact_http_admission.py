"""Admission and cancellation tests use real storage locks and ASGI requests."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
import threading
from typing import Any

import pytest

from arbiter_server.artifacts import ArtifactStore
from arbiter_server.blocking import BlockingOperationPool
from arbiter_server.http_work import HTTPWork
from server.tests.unit.test_native_http_protocol import _asgi_request, _client


async def wait_thread(event):
    assert await asyncio.to_thread(event.wait, 3), "worker did not reach test barrier"


async def available(app):
    async def retry():
        while True:
            response = await _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                json_body={"args": {"text": "ready"}},
            )
            if response.status_code != 503:
                assert response.status_code == 200
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(retry(), 3)


def artifact_fixture(tmp_path):
    client = _client(tmp_path, max_blocking_workers=1)
    store = ArtifactStore(root=tmp_path, base_url="/api/v1/artifacts")
    descriptor = store.create(
        plugin="echo",
        content=b"x" * 150000,
        filename="test.txt",
        content_type="text/plain",
        source={},
    )
    return client._app, store, descriptor.url


@pytest.mark.parametrize(
    "method,content", [("GET", False), ("HEAD", True), ("GET", True)]
)
@pytest.mark.parametrize("valid_nonce", [True, False])
def test_artifact_lock_contention_keeps_health_responsive(
    tmp_path, method, content, valid_nonce
):
    app, store, target = artifact_fixture(tmp_path)
    if content:
        target = target.replace("?", "/content?", 1)
    if not valid_nonce:
        target = target.split("?", 1)[0] + "?nonce=invalid"
    held, release, entered = threading.Event(), threading.Event(), threading.Event()
    original = ArtifactStore._storage_lock

    @contextmanager
    def observed_lock(self):
        entered.set()
        with original(self):
            yield

    def hold():
        with store._storage_lock():
            held.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(2)

    async def exercise():
        request = asyncio.create_task(_asgi_request(app, method, target))
        try:
            await wait_thread(entered)
            health = await asyncio.wait_for(_asgi_request(app, "GET", "/_health_"), 1)
            assert health.status_code == 200 and not release.is_set()
            busy = await _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                json_body={"args": {"text": "busy"}},
            )
            assert busy.status_code == 503
            assert busy.json()["error"]["retry"] == {
                "transient": "yes",
                "replay": "safe",
            }
        finally:
            release.set()
            response = await request
            assert response.status_code == (200 if valid_nonce else 404)
        await available(app)

    # Watchdog makes an accidental event-loop block fail rather than deadlock.
    watchdog = threading.Timer(4, release.set)
    watchdog.start()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(ArtifactStore, "_storage_lock", observed_lock)
            asyncio.run(asyncio.wait_for(exercise(), 6))
    finally:
        release.set()
        watchdog.cancel()
        holder.join(3)
        watchdog.join()
        assert not holder.is_alive()


@pytest.mark.parametrize("cancel", [False, True])
def test_slow_artifact_read_is_bounded_and_closes_after_cancellation(
    tmp_path, monkeypatch, cancel
):
    app, _, target = artifact_fixture(tmp_path)
    target = target.replace("?", "/content?", 1)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = Path.open
    loop_thread = threading.get_ident()

    class SlowFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            assert threading.get_ident() != loop_thread
            return self

        def __exit__(self, *args):
            assert threading.get_ident() != loop_thread
            self.handle.close()
            closed.set()

        def read(self, size):
            assert threading.get_ident() != loop_thread
            assert size == 64 * 1024
            entered.set()
            assert release.wait(5)
            return self.handle.read(size)

    def slow_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        handle = original(path, *args, **kwargs)
        return SlowFile(handle) if path.name == "payload" else handle

    monkeypatch.setattr(Path, "open", slow_open)

    async def exercise():
        task = asyncio.create_task(_asgi_request(app, "GET", target))
        try:
            await wait_thread(entered)
            assert (
                await asyncio.wait_for(_asgi_request(app, "GET", "/_health_"), 1)
            ).status_code == 200
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            busy = await _asgi_request(app, "GET", target)
            assert busy.status_code == 503
            assert not closed.is_set()
        finally:
            release.set()
            if not task.done():
                response = await task
                assert response.content == b"x" * 150000
        await wait_thread(closed)
        await available(app)
        consumed = await _asgi_request(app, "GET", target)
        assert consumed.status_code == 410

    watchdog = threading.Timer(4, release.set)
    watchdog.start()
    try:
        asyncio.run(asyncio.wait_for(exercise(), 6))
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()


def test_http_work_cancellation_waits_for_transport_cleanup_before_reusing_slot():
    async def exercise():
        pool = BlockingOperationPool(1)
        work = HTTPWork(pool)
        entered, cleaning, release_cleanup = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def transport():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()

        request = asyncio.create_task(work.run(lambda bridge: bridge.call(transport())))
        try:
            await entered.wait()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await cleaning.wait()
            with pytest.raises(Exception, match="capacity is full"):
                await pool.run(lambda: None)
            release_cleanup.set()

            async def retry():
                while True:
                    try:
                        return await pool.run(lambda: "recovered")
                    except Exception:
                        await asyncio.sleep(0)

            assert await asyncio.wait_for(retry(), 2) == "recovered"
        finally:
            release_cleanup.set()
            work.close()

    asyncio.run(asyncio.wait_for(exercise(), 4))


@pytest.mark.parametrize("end", ["disconnect", "cancel"])
def test_stream_backpressure_retains_capacity_and_closes_file(
    tmp_path, monkeypatch, end
):
    app, _, target = artifact_fixture(tmp_path)
    target = target.replace("?", "/content?", 1)
    original = Path.open
    closed = threading.Event()
    read_sizes = []

    class TrackedFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()
            closed.set()

        def read(self, size):
            read_sizes.append(size)
            return self.handle.read(size)

    def tracked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        handle = original(path, *args, **kwargs)
        return TrackedFile(handle) if path.name == "payload" else handle

    monkeypatch.setattr(Path, "open", tracked_open)

    async def exercise():
        sending, disconnected = asyncio.Event(), asyncio.Event()

        async def slow_send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                sending.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            _asgi_request(
                app, "GET", target, on_send=slow_send, disconnect=disconnected
            )
        )
        try:
            await asyncio.wait_for(sending.wait(), 2)
            assert read_sizes == [64 * 1024], "worker read ahead of backpressure"
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            assert (await _asgi_request(app, "HEAD", target)).status_code == 503
            if end == "disconnect":
                disconnected.set()
                await task
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await wait_thread(closed)
            await available(app)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), 4))


def test_shutdown_unblocks_a_worker_waiting_on_transport():
    async def exercise():
        work = HTTPWork(BlockingOperationPool(1))
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def transport():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(work.run(lambda bridge: bridge.call(transport())))
        await entered.wait()
        work.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()

    asyncio.run(asyncio.wait_for(exercise(), 3))


@contextmanager
def live_server(app):
    import logging
    import socket
    import time
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    access_logger = logging.getLogger("uvicorn.access")
    logging_patch = pytest.MonkeyPatch()
    logging_patch.setattr(access_logger, "handlers", list(access_logger.handlers))
    logging_patch.setattr(access_logger, "propagate", access_logger.propagate)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            log_level=None,
            access_log=False,
            lifespan="on",
            http="h11",
            loop="asyncio",
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        logging_patch.undo()
        assert not thread.is_alive()


def wire_request(port, method, path):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "method,content", [("GET", False), ("HEAD", True), ("GET", True)]
)
@pytest.mark.parametrize("valid_nonce", [False, True])
@pytest.mark.usefixtures("private_windows_test_parent")
def test_real_socket_artifact_contention_does_not_delay_health(
    tmp_path, monkeypatch, method, content, valid_nonce
):
    import http.client

    app, store, target = artifact_fixture(tmp_path)
    if content:
        target = target.replace("?", "/content?", 1)
    if not valid_nonce:
        target = target.split("?", 1)[0] + "?nonce=invalid"
    held, entered, release = threading.Event(), threading.Event(), threading.Event()
    original = ArtifactStore._storage_lock

    def hold():
        with original(store):
            held.set()
            assert release.wait(5)

    @contextmanager
    def observed(self):
        entered.set()
        with original(self):
            yield

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(2)
    monkeypatch.setattr(ArtifactStore, "_storage_lock", observed)
    watchdog = threading.Timer(4, release.set)
    watchdog.start()
    try:
        with live_server(app) as port:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request(method, target)
                assert entered.wait(2)
                assert wire_request(port, "GET", "/_health_")[0] == 200
                assert not release.is_set(), "health waited for artifact storage"
                release.set()
                response = connection.getresponse()
                assert response.status == (200 if valid_nonce else 404)
                response.read()
            finally:
                release.set()
                connection.close()
    finally:
        release.set()
        holder.join(3)
        watchdog.cancel()
        watchdog.join()
        assert not holder.is_alive()


def test_repeated_shutdown_preserves_in_progress_transport_cleanup():
    async def exercise():
        work = HTTPWork(BlockingOperationPool(1))
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cleaned = asyncio.Event()
        interrupted = asyncio.Event()

        async def transport():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    interrupted.set()
                    raise
                finally:
                    cleaned.set()

        task = asyncio.create_task(work.run(lambda bridge: bridge.call(transport())))
        try:
            await entered.wait()
            work.close()
            await cleaning.wait()
            work.close()
            task.cancel()
            await asyncio.sleep(0.01)
            assert not interrupted.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await cleaned.wait()
            assert not interrupted.is_set()
        finally:
            release.set()
            work.close()

    asyncio.run(asyncio.wait_for(exercise(), 3))


def test_streaming_send_failure_does_not_send_second_response_headers(tmp_path):
    from arbiter_server.errors import OperationError

    app, _, target = artifact_fixture(tmp_path)
    target = target.replace("?", "/content?", 1)

    async def exercise():
        starts = []

        async def failing_send(message):
            if message["type"] == "http.response.start":
                starts.append(message["status"])
            elif message.get("body"):
                raise OperationError("resource_exhausted", "Transport failed.")

        with pytest.raises(OperationError, match="Transport failed"):
            await _asgi_request(app, "GET", target, on_send=failing_send)
        assert starts == [200]
        await available(app)

    asyncio.run(asyncio.wait_for(exercise(), 3))
