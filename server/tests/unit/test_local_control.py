from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import gc
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from typing import Any, cast

import pytest

from arbiter_server import authority as a, local_control as c
from arbiter_server.config import AppConfig
from arbiter_server.main import build_server
from server.tests.unit.test_native_http_protocol import _asgi_request

pytestmark = pytest.mark.skipif(os.name == "nt", reason="native POSIX control")
CONTROL_FAILURES: tuple[type[Exception], ...] = (a.AuthorityError, OSError)


@asynccontextmanager
async def serve(root):
    with a.running_instance(root):
        control = c.LocalControl(root)
        try:
            await control.start()
            yield control
        finally:
            await control.close()


async def call(root, action="status", recovery=None, reuse=False):
    return await asyncio.to_thread(c.request, root, action, recovery, reuse)


async def wire(control, data, size=None):
    reader, writer = await asyncio.open_unix_connection(str(control.path))
    writer.write(struct.pack("!I", len(data) if size is None else size) + data)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(), 2)
    finally:
        writer.close()
        await writer.wait_closed()


def test_real_asgi_cli_setup_status_and_restart(tmp_path):
    cfg = AppConfig()
    cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
    app = cast(Any, build_server(cfg, service_plugins=[], prepare_tls=False).app)
    root = tmp_path / "server" / "authority"
    recovery = tmp_path / "operator.json"

    async def command(action):
        argv = [
            sys.executable,
            "-m",
            "arbiter_server",
            "authority",
            action,
            "--state-dir",
            str(root),
            "--json",
        ]
        if action == "init":
            argv += ["--recovery-file", recovery.name]
        return await asyncio.to_thread(
            subprocess.run,
            argv,
            text=True,
            capture_output=True,
            timeout=15,
            cwd=tmp_path,
        )

    async def exercise():
        async with app.router.lifespan_context(app):
            assert (await command("status")).stdout.strip() == '{"initialized": false}'
            result = await command("init")
            assert result.returncode == 0, result.stderr
            initial = json.loads(result.stdout)
            assert initial["initialized"] is True
            assert json.loads((await command("status")).stdout) == initial
            denied = await command("init")
            assert denied.returncode == 1 and "already exists" in denied.stderr
            assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            custody = recovery.read_bytes()
            assert b"PRIVATE" not in result.stdout.encode()
        assert not c._endpoint(root, create=False).exists()
        restored = a.authority_status(root)
        assert restored is not None and restored.to_dict() == initial
        async with app.router.lifespan_context(app):
            assert json.loads((await command("status")).stdout) == initial
            assert recovery.read_bytes() == custody

    asyncio.run(exercise())


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 1)() != 0,
    reason="requires native root to drop to a distinct UID",
)
def test_real_different_os_user_is_rejected(monkeypatch):
    import pwd

    account = pwd.getpwnam("nobody")
    seen = []
    real = c._peer_uid

    def observe(peer):
        uid = real(peer)
        seen.append(uid)
        return uid

    monkeypatch.setattr(c, "_peer_uid", observe)
    with tempfile.TemporaryDirectory(prefix="arbiter-peer-", dir="/tmp") as directory:
        parent = Path(directory)
        runtime = parent / "control"
        monkeypatch.setattr(c, "_runtime_root", lambda: runtime)
        root = parent / "authority"

        async def exercise():
            async with serve(root) as control:
                # Test-only access through the filesystem lets the other UID
                # reach the kernel peer check. Production never broadens modes.
                parent.chmod(0o711)
                runtime.chmod(0o711)
                control.path.chmod(0o666)
                script = """
import os, socket, sys
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[1]))
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(2)
    peer.connect(sys.argv[3])
    try:
        assert not peer.recv(1)
    except ConnectionResetError:
        pass
print(os.geteuid())
"""
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            "-I",
                            "-c",
                            script,
                            str(account.pw_uid),
                            str(account.pw_gid),
                            str(control.path),
                        ],
                        text=True,
                        capture_output=True,
                        timeout=5,
                    )
                    assert result.returncode == 0, result.stderr
                    assert int(result.stdout) == account.pw_uid != os.geteuid()
                    assert seen == [account.pw_uid]
                    assert control.work is None
                finally:
                    control.path.chmod(0o600)
                    runtime.chmod(0o700)
                    parent.chmod(0o700)

        asyncio.run(exercise())


def test_kernel_peer_uid_is_checked_in_both_directions(tmp_path, monkeypatch):
    root = tmp_path / "authority"
    real = c._peer_uid
    observed = []

    def peer(sock):
        uid = real(sock)
        observed.append(uid)
        return uid

    monkeypatch.setattr(c, "_peer_uid", peer)

    async def exercise():
        async with serve(root):
            assert await call(root) == {"initialized": False}
        assert len(observed) == 2 and set(observed) == {os.geteuid()}

    asyncio.run(exercise())


@pytest.mark.parametrize("side", ["client", "server"])
def test_wrong_peer_uid_is_rejected_before_dispatch(tmp_path, monkeypatch, side):
    root = tmp_path / "authority"
    real = c._peer_uid
    dispatched = []

    def peer(sock):
        # The synchronous client runs in a worker; server checks run on the loop.
        client = threading.current_thread() is not threading.main_thread()
        return os.geteuid() + 1 if client == (side == "client") else real(sock)

    monkeypatch.setattr(c, "_peer_uid", peer)
    monkeypatch.setattr(
        c.LocalControl, "dispatch", lambda *args: dispatched.append(True)
    )

    async def exercise():
        async with serve(root):
            with pytest.raises(CONTROL_FAILURES):
                await call(root)
        assert not dispatched

    asyncio.run(exercise())


@pytest.mark.parametrize("damage", ["mode", "file", "symlink", "replacement"])
def test_endpoint_damage_fails_closed_and_is_not_deleted(tmp_path, damage):
    root = tmp_path / "authority"

    async def exercise():
        with a.running_instance(root):
            control = c.LocalControl(root)
            await control.start()
            path = control.path
            replacement = None
            if damage == "mode":
                path.chmod(0o666)
            else:
                path.unlink()
                if damage == "symlink":
                    path.symlink_to(tmp_path / "absent")
                elif damage == "file":
                    path.write_text("preserve")
                else:
                    replacement = socket.socket(socket.AF_UNIX)
                    replacement.bind(str(path))
                    path.chmod(0o600)
            try:
                with pytest.raises(CONTROL_FAILURES):
                    await call(root)
                try:
                    await control.close()
                except a.AuthorityError:
                    pass
                assert path.exists() or path.is_symlink()
            finally:
                if replacement is not None:
                    replacement.close()
                path.unlink()

    asyncio.run(exercise())


def test_stale_private_socket_reconciles_but_unknown_file_is_preserved(tmp_path):
    root = a._root(tmp_path / "authority", create=True)
    path = c._endpoint(root, create=True)
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(str(path))
        path.chmod(0o600)

    async def exercise():
        async with serve(root):
            assert await call(root) == {"initialized": False}
        path.write_text("unrelated")
        path.chmod(0o600)
        try:
            with pytest.raises(a.AuthorityError):
                async with serve(root):
                    pytest.fail("accepted an unrelated endpoint file")
            assert path.read_text() == "unrelated"
        finally:
            path.unlink()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"[]",
        b'{"action":"status","action":"init"}',
        b'{"root":"other","action":"status"}',
    ],
)
def test_malformed_requests_close_and_capacity_recovers(tmp_path, data):
    root = tmp_path / "authority"

    async def exercise():
        async with serve(root) as control:
            response = await wire(control, data)
            if response:
                assert struct.unpack("!I", response[:4])[0] == len(response) - 4
                reply = json.loads(response[4:])
                assert reply["ok"] is False and "result" not in reply
            assert await call(root) == {"initialized": False}

    asyncio.run(exercise())


def test_oversize_prefix_is_rejected_without_body(tmp_path):
    root = tmp_path / "authority"

    async def exercise():
        async with serve(root) as control:
            assert await wire(control, b"", size=c.MAX_MESSAGE + 1) == b""
            assert await call(root) == {"initialized": False}

    asyncio.run(exercise())


def test_exact_message_boundary_is_accepted(tmp_path):
    root = tmp_path / "authority"

    async def exercise():
        async with serve(root) as control:
            value = json.dumps({"root": str(root), "action": "status"}).encode()
            value += b" " * (c.MAX_MESSAGE - len(value))
            response = await wire(control, value)
            assert struct.unpack("!I", response[:4])[0] == len(response) - 4
            assert json.loads(response[4:]) == {
                "ok": True,
                "result": {"initialized": False},
            }

    asyncio.run(exercise())


def test_slow_connections_are_bounded_and_timeout_recovers_capacity(
    tmp_path, monkeypatch
):
    root = tmp_path / "authority"
    monkeypatch.setattr(c, "EXCHANGE_TIMEOUT", 0.15)

    async def exercise():
        async with serve(root) as control:
            clients = [
                await asyncio.open_unix_connection(str(control.path))
                for _ in range(c.MAX_CONNECTIONS)
            ]
            extra = await asyncio.open_unix_connection(str(control.path))
            assert await asyncio.wait_for(extra[0].read(1), 2) == b""
            assert len(control.connections) <= c.MAX_CONNECTIONS
            for reader, writer in [*clients, extra]:
                assert await asyncio.wait_for(reader.read(1), 2) == b""
                writer.close()
                await writer.wait_closed()
            assert await call(root) == {"initialized": False}

    asyncio.run(exercise())


@pytest.mark.filterwarnings("error::ResourceWarning")
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize("ending", ["malformed", "timeout", "overload", "cancel"])
def test_all_transports_close_before_control_returns(tmp_path, monkeypatch, ending):
    root = tmp_path / "authority"
    monkeypatch.setattr(c, "EXCHANGE_TIMEOUT", 0.1)
    monkeypatch.setattr(c, "MAX_CONNECTIONS", 1)
    if ending == "cancel":
        # A cancelled handler may never execute its own cleanup body.
        monkeypatch.setattr(
            c.LocalControl, "handle", lambda *args: asyncio.Event().wait()
        )
    transports = []
    connected = c._Protocol.connection_made

    def observe(protocol, transport):
        transports.append(transport)
        connected(protocol, transport)

    monkeypatch.setattr(c._Protocol, "connection_made", observe)

    async def exercise():
        clients = []
        async with serve(root) as control:
            reader, writer = await asyncio.open_unix_connection(str(control.path))
            clients.append(writer)
            if ending == "overload":
                extra, rejected = await asyncio.open_unix_connection(str(control.path))
                clients.append(rejected)
                assert await asyncio.wait_for(extra.read(1), 2) == b""
                assert len(control.handlers) == 1
            elif ending == "malformed":
                writer.write(struct.pack("!I", 1) + b"{")
                await writer.drain()
                assert await asyncio.wait_for(reader.read(1), 2) == b""
            elif ending == "timeout":
                assert await asyncio.wait_for(reader.read(1), 2) == b""
        for writer in clients:
            writer.close()
            await writer.wait_closed()
        assert not control.transports and cast(Any, control.acceptor).done()
        assert (listener := control.listener) is not None
        assert not listener.pending and listener.fileno() == -1
        assert not control.handlers and not control.connections
        # Excess sockets close before a stream transport or handler is created.
        assert len(transports) == 1
        assert all(t.get_extra_info("socket").fileno() == -1 for t in transports)
        transports.clear()
        gc.collect()

    asyncio.run(exercise())
    gc.collect()


@pytest.mark.parametrize("method", ["sock_accept", "connect_accepted_socket"])
def test_transient_failure_recovers_and_shutdown_joins_backoff(
    tmp_path, monkeypatch, method
):
    async def exercise():
        loop = asyncio.get_running_loop()
        original = getattr(loop, method)
        failed = asyncio.Event()

        async def transient(*args):
            if not failed.is_set():
                failed.set()
                raise OSError(c.EMFILE, "injected resource pressure")
            return await original(*args)

        monkeypatch.setattr(loop, method, transient)
        async with serve(tmp_path / "authority") as control:
            with suppress(*CONTROL_FAILURES):
                await call(control.root)
            assert await call(control.root) == {"initialized": False}
            failed.clear()
            pending = asyncio.create_task(call(control.root))
            await asyncio.wait_for(failed.wait(), 2)
            await control.close()
            with suppress(*CONTROL_FAILURES):
                await pending
            assert cast(Any, control.acceptor).done() and not control.path.exists()
            assert not control.transports and not cast(Any, control.listener).pending

    asyncio.run(exercise())


def test_disconnect_and_cancelled_shutdown_retain_actual_work_and_instance_lock(
    tmp_path, monkeypatch
):
    cfg = AppConfig()
    cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
    app = cast(Any, build_server(cfg, service_plugins=[], prepare_tls=False).app)
    root = tmp_path / "server" / "authority"
    entered, release = threading.Event(), threading.Event()
    ready = asyncio.Event()
    real = c.LocalControl.dispatch

    def dispatch(control, value):
        entered.set()
        assert release.wait(5)
        return real(control, value)

    monkeypatch.setattr(c.LocalControl, "dispatch", dispatch)

    async def exercise():
        async def owner():
            async with app.router.lifespan_context(app):
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        await asyncio.wait_for(ready.wait(), 2)
        _, writer = await asyncio.open_unix_connection(
            str(c._endpoint(root, create=False))
        )
        writer.write(c._frame({"root": str(root), "action": "status"}))
        await writer.drain()
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
        with pytest.raises(a.AuthorityError, match="busy"):
            await call(root)
        writer.close()
        await writer.wait_closed()
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        try:
            assert not task.done()
            with pytest.raises(a.AuthorityBusy):
                a.authority_status(root)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert a.authority_status(root) is None

    asyncio.run(exercise())
