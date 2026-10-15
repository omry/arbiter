"""Bound operation-body readers before receive and recover admission capacity."""

import asyncio
from contextlib import contextmanager
import threading

import pytest

from arbiter_server.artifacts import ArtifactStore
from server.tests.unit.test_artifact_http_admission import (
    available,
    live_server,
    wait_thread,
)
from server.tests.unit.test_native_http_protocol import _asgi_request, _client


def socket_headers(connection):
    data = b""
    while b"\r\n\r\n" not in data:
        piece = connection.recv(4096)
        assert piece, "server closed before sending headers"
        data += piece
        assert len(data) < 65536
    return data


def wire_request(port, method, path, body=None, *, chunked=False):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        headers = {"Transfer-Encoding": "chunked"} if chunked else {}
        if chunked:
            assert body is not None
            payload = [body[:8], body[8:]]
        else:
            payload = body
        connection.request(
            method, path, body=payload, headers=headers, encode_chunked=chunked
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("declared", [None, "1"])
@pytest.mark.parametrize("finish", ["complete", "cancel"])
def test_body_admission_precedes_reads_and_recovers(tmp_path, declared, finish):
    app = _client(tmp_path, max_blocking_workers=2, max_request_body_bytes=128)._app

    async def exercise():
        release = asyncio.Event()
        entered = [asyncio.Event(), asyncio.Event()]
        reads = [0, 0]

        def gate(index):
            async def before_receive():
                reads[index] += 1
                if reads[index] == 2:
                    entered[index].set()
                    await release.wait()

            return before_receive

        headers = [] if declared is None else [(b"content-length", declared.encode())]
        tasks = [
            asyncio.create_task(
                _asgi_request(
                    app,
                    "POST",
                    "/api/v1/operations/echo:repeat",
                    body_chunks=[b'{"args":{"text":"held', b'"}}'],
                    extra_headers=headers,
                    before_receive=gate(index),
                )
            )
            for index in range(2)
        ]
        rejected_reads = []
        try:
            for event in entered:
                await asyncio.wait_for(event.wait(), 2)
            busy = await _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                body_chunks=[b"never read"],
                on_receive=lambda: rejected_reads.append(True),
            )
            assert busy.status_code == 503 and rejected_reads == []
            assert busy.json()["error"]["details"] == {"max_blocking_workers": 2}
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            assert reads == [2, 2]
            if finish == "cancel":
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                assert all(isinstance(r, asyncio.CancelledError) for r in results)
            else:
                release.set()
                assert all(r.status_code == 200 for r in await asyncio.gather(*tasks))
            await available(app)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), 5))


@pytest.mark.parametrize(
    "body,status",
    [
        (b"{", 400),
        (b"[]", 400),
        (b'{"args":[]}', 400),
        (b"x" * 65, 413),
        (b'{"args":{"text":"ok"}}', 200),
    ],
)
def test_body_errors_and_success_return_admission_capacity(tmp_path, body, status):
    app = _client(tmp_path, max_blocking_workers=1, max_request_body_bytes=64)._app

    async def exercise():
        response = await _asgi_request(
            app, "POST", "/api/v1/operations/echo:repeat", body_chunks=[body]
        )
        assert response.status_code == status
        await available(app)

    asyncio.run(asyncio.wait_for(exercise(), 3))


def test_json_parsing_uses_admitted_worker_and_keeps_health_responsive(
    tmp_path, monkeypatch
):
    from arbiter_server import main

    app = _client(tmp_path, max_blocking_workers=1)._app
    parse = main._parse_operation_arguments
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def slow_parse(body):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(4)
        return parse(body)

    monkeypatch.setattr(main, "_parse_operation_arguments", slow_parse)

    async def exercise():
        task = asyncio.create_task(
            _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                json_body={"args": {"text": "parse"}},
            )
        )
        try:
            await wait_thread(entered)
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            assert (
                await _asgi_request(
                    app, "POST", "/api/v1/operations/echo:repeat", json_body={}
                )
            ).status_code == 503
        finally:
            release.set()
            assert (await task).status_code == 200

    watchdog = threading.Timer(3, release.set)
    watchdog.start()
    try:
        asyncio.run(asyncio.wait_for(exercise(), 5))
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("finish", ["complete", "disconnect"])
def test_real_socket_slow_body_admission_health_and_recovery(tmp_path, chunked, finish):
    import socket
    import time

    app = _client(tmp_path, max_blocking_workers=2)._app
    target = "/api/v1/operations/echo:repeat"
    body = b'{"args":{"text":"socket"}}'
    prefix = body[:12]
    with live_server(app) as port:
        held = []
        try:
            for _ in range(2):
                connection = socket.create_connection(("127.0.0.1", port), timeout=2)
                held.append(connection)
                framing = (
                    b"Transfer-Encoding: chunked"
                    if chunked
                    else f"Content-Length: {len(body)}".encode()
                )
                connection.sendall(
                    b"POST "
                    + target.encode()
                    + b" HTTP/1.1\r\nHost: localhost\r\n"
                    + framing
                    + b"\r\nExpect: 100-continue\r\n\r\n"
                )
                assert b" 100 Continue" in socket_headers(connection)
                connection.sendall(
                    f"{len(prefix):x}\r\n".encode() + prefix + b"\r\n"
                    if chunked
                    else prefix
                )
            with socket.create_connection(("127.0.0.1", port), timeout=2) as excess:
                excess.sendall(
                    b"POST " + target.encode() + b" HTTP/1.1\r\nHost: localhost\r\n"
                    b"Content-Length: 100\r\nExpect: 100-continue\r\n\r\n"
                )
                headers = socket_headers(excess)
                assert b" 503 " in headers and b" 100 " not in headers
            assert wire_request(port, "GET", "/_health_")[0] == 200
            for connection in held:
                if finish == "complete":
                    rest = body[len(prefix) :]
                    connection.sendall(
                        f"{len(rest):x}\r\n".encode() + rest + b"\r\n0\r\n\r\n"
                        if chunked
                        else rest
                    )
                    assert b" 200 " in socket_headers(connection)
                connection.close()
            deadline = time.monotonic() + 3
            while True:
                status, result = wire_request(port, "POST", target, body)
                if status != 503:
                    assert status == 200 and b"socket" in result
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
        finally:
            for connection in held:
                connection.close()


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("size,status", [(63, 200), (64, 200), (65, 413)])
def test_real_socket_body_limit_boundaries_and_recovery(
    tmp_path, chunked, size, status
):
    app = _client(tmp_path, max_blocking_workers=1, max_request_body_bytes=64)._app
    body = b'{"args":{"text":"ok"}}'.ljust(size, b" ")
    with live_server(app) as port:
        assert (
            wire_request(
                port, "POST", "/api/v1/operations/echo:repeat", body, chunked=chunked
            )[0]
            == status
        )
        assert (
            wire_request(port, "POST", "/api/v1/operations/echo:repeat", b"{")[0] == 400
        )
        assert (
            wire_request(
                port,
                "POST",
                "/api/v1/operations/echo:repeat",
                b'{"args":{"text":"recovered"}}',
            )[0]
            == 200
        )


def test_real_socket_oversized_declared_body_is_rejected_before_continue(tmp_path):
    import socket

    app = _client(tmp_path, max_blocking_workers=1, max_request_body_bytes=64)._app
    with live_server(app) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(
                b"POST /api/v1/operations/echo:repeat HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 65\r\nExpect: 100-continue\r\n\r\n"
            )
            headers = socket_headers(connection)
            assert b" 413 " in headers and b" 100 " not in headers
        assert wire_request(port, "GET", "/_health_")[0] == 200


def test_body_reader_and_cancelled_artifact_lock_wait_share_capacity(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, max_blocking_workers=2)
    app = client._app
    store = ArtifactStore(root=tmp_path, base_url="/api/v1/artifacts")
    descriptor = store.create(
        plugin="echo",
        content=b"combined",
        filename=None,
        content_type="text/plain",
        source={},
    )
    original = ArtifactStore._storage_lock
    held, storage_entered, release_lock = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def hold():
        with original(store):
            held.set()
            assert release_lock.wait(5)

    @contextmanager
    def observed(self):
        storage_entered.set()
        with original(self):
            yield

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(2)
    monkeypatch.setattr(ArtifactStore, "_storage_lock", observed)

    async def exercise():
        reader_entered = asyncio.Event()

        async def slow_receive():
            reader_entered.set()
            await asyncio.Event().wait()

        reader = asyncio.create_task(
            _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                body_chunks=[b"{}"],
                before_receive=slow_receive,
            )
        )
        artifact = asyncio.create_task(_asgi_request(app, "GET", descriptor.url))
        try:
            await reader_entered.wait()
            await wait_thread(storage_entered)
            artifact.cancel()
            with pytest.raises(asyncio.CancelledError):
                await artifact
            rejected_reads = []
            excess = await _asgi_request(
                app,
                "POST",
                "/api/v1/operations/echo:repeat",
                body_chunks=[b"{}"],
                on_receive=lambda: rejected_reads.append(True),
            )
            assert excess.status_code == 503 and rejected_reads == []
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
            # The body-reader slot recovers while cancelled disk work still owns
            # the other slot; cancellation must neither leak nor over-admit.
            await available(app)
            assert not release_lock.is_set()
        finally:
            release_lock.set()
            reader.cancel()
            artifact.cancel()
            await asyncio.gather(reader, artifact, return_exceptions=True)
        await available(app)

    watchdog = threading.Timer(4, release_lock.set)
    watchdog.start()
    try:
        asyncio.run(asyncio.wait_for(exercise(), 6))
    finally:
        release_lock.set()
        holder.join(3)
        watchdog.cancel()
        watchdog.join()
        assert not holder.is_alive()
