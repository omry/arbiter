from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arbiter_server import authority as a, local_control as c, windows_control as wc
from arbiter_server.config import AppConfig
from arbiter_server.main import build_server
from server.tests.unit.test_native_http_protocol import _asgi_request
from server.tests.unit.test_windows_pipe import different_user, set_acl

CONTROL_FAILURES: tuple[type[Exception], ...] = (a.AuthorityError, OSError)


@pytest.mark.parametrize(
    "data",
    [b"", b"123", struct.pack("!I", 3) + b"{}", c._frame({}) + b"x", b"\0" * 65541],
    ids=["empty", "short-header", "length-mismatch", "trailing-byte", "oversized"],
)
def test_frame_length_rejected_before_json_decode(data):
    with pytest.raises(a.AuthorityError, match="message length"):
        wc._message(data)


def test_exact_message_boundary_and_duplicate_field_rejection():
    data = json.dumps({"x": "x" * (c.MAX_MESSAGE - 8)}, separators=(",", ":")).encode()
    assert len(data) == c.MAX_MESSAGE
    assert wc._message(struct.pack("!I", len(data)) + data) == json.loads(data)
    duplicate = b'{"x":1,"x":2}'
    with pytest.raises(
        a.AuthorityError, match="invalid local control message"
    ) as error:
        wc._message(struct.pack("!I", len(duplicate)) + duplicate)
    assert isinstance(error.value.__cause__, a.AuthorityError)
    assert "duplicate" in str(error.value.__cause__)


def test_exchange_timeout_keeps_listener_alive():
    async def exercise():
        control = cast(Any, object.__new__(wc.WindowsControl))
        control.closed = False
        control.api = SimpleNamespace(
            k=SimpleNamespace(DisconnectNamedPipe=lambda handle: True)
        )
        reconnected = asyncio.Event()
        connections = 0

        async def connect():
            nonlocal connections
            connections += 1
            if connections > 1:
                reconnected.set()
                await asyncio.Event().wait()

        async def exchange(pipe):
            raise asyncio.TimeoutError

        control.exchange = exchange
        task = asyncio.create_task(
            control.serve(SimpleNamespace(handle=1, connect=connect))
        )
        try:
            await asyncio.wait_for(reconnected.wait(), 3)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@asynccontextmanager
async def serve(root):
    with a.running_instance(root):
        control = wc.WindowsControl(root)
        try:
            await control.start()
            yield control
        finally:
            await control.close()


async def call(root, action="status", recovery=None):
    return await asyncio.to_thread(c.request, root, action, recovery, False)


@asynccontextmanager
async def last_instance(control):
    # Hold the other instances so a later successful call proves this slot reset.
    peers = []
    try:
        for _ in range(c.MAX_CONNECTIONS - 1):
            peers.append(control.api.open(control.name))
        yield
    finally:
        for peer in peers:
            peer.close()


@pytest.mark.skipif(os.name != "nt", reason="native Windows live authority control")
@pytest.mark.usefixtures("private_windows_test_parent")
class TestNativeControl:
    def test_installed_cli_setup_status_restart_and_namespace_cleanup(self, tmp_path):
        cfg = AppConfig()
        cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
        cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
        app = cast(Any, build_server(cfg, service_plugins=[], prepare_tls=False).app)
        root = tmp_path / "server" / "authority"
        recovery = tmp_path / "operator.json"
        executable = Path(sys.executable).with_name("arbiter-server.exe")
        assert executable.is_file(), "test the installed console entrypoint"

        async def command(action):
            argv = [
                str(executable),
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
                timeout=20,
                cwd=tmp_path,
            )

        async def exercise():
            with a.running_instance(root):
                busy = await command("status")
                assert busy.returncode == 1 and "authority state is busy" in busy.stderr
            async with app.router.lifespan_context(app):
                assert json.loads((await command("status")).stdout) == {
                    "initialized": False
                }
                initial = await command("init")
                assert initial.returncode == 0, initial.stderr
                expected = json.loads(initial.stdout)
                assert (
                    expected["initialized"] is True and "PRIVATE" not in initial.stdout
                )
                assert json.loads((await command("status")).stdout) == expected
                denied = await command("init")
                assert denied.returncode == 1 and "already exists" in denied.stderr
                name, _ = wc._locator(root)
                custody = recovery.read_bytes()
                assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
            assert not (root / wc._LOCATOR).exists()
            with pytest.raises(OSError):
                wc.w.Native().open(name)
            async with app.router.lifespan_context(app):
                assert wc._locator(root)[0] != name
                assert json.loads((await command("status")).stdout) == expected
                assert recovery.read_bytes() == custody
            restored = a.authority_status(root)
            assert restored is not None and restored.to_dict() == expected

        asyncio.run(exercise())

    def test_oversized_native_message_resets_the_same_instance(
        self, tmp_path, monkeypatch
    ):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                rejected = asyncio.Event()
                exchange = control.exchange

                async def observe(pipe):
                    try:
                        await exchange(pipe)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) == 234:  # ERROR_MORE_DATA
                            rejected.set()
                        raise

                monkeypatch.setattr(control, "exchange", observe)
                async with last_instance(control):
                    peer = control.api.open(control.name)
                    try:
                        # Bypass only the cooperative sender's size guard.
                        with suppress(OSError):
                            await asyncio.wait_for(
                                peer._io("WriteFile", b"x" * (wc.w.CAPACITY + 1)), 3
                            )
                        await asyncio.wait_for(rejected.wait(), 3)
                        assert control.work is None
                    finally:
                        peer.close()
                    assert await call(control.root) == {"initialized": False}

        asyncio.run(exercise())

    def test_delayed_reader_preserves_reply_and_invalid_ack_recovers_instance(
        self, tmp_path, monkeypatch
    ):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                written = asyncio.Event()
                write = wc.w.Pipe.write

                async def observe(pipe, data):
                    await write(pipe, data)
                    if pipe in control.pipes:
                        written.set()

                monkeypatch.setattr(wc.w.Pipe, "write", observe)
                async with last_instance(control):
                    peer = control.api.open(control.name)
                    try:
                        await peer.write(
                            c._frame({"root": str(control.root), "action": "status"})
                        )
                        # No ReadFile was posted before the server's actual write.
                        await asyncio.wait_for(written.wait(), 1)
                        await asyncio.sleep(0)
                        assert wc._message(await peer.read()) == {
                            "ok": True,
                            "result": {"initialized": False},
                        }
                        # An invalid ACK disconnects without losing the instance.
                        await peer.write(b"NO")
                        with pytest.raises(OSError):
                            await asyncio.wait_for(peer.read(), 3)
                    finally:
                        peer.close()
                    assert await call(control.root) == {"initialized": False}

        asyncio.run(exercise())

    @pytest.mark.parametrize(
        "data", [b"", b"{", b"[]", b'{"action":"status","action":"init"}']
    )
    def test_malformed_native_input_recovers_capacity(self, tmp_path, data):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                peer = control.api.open(control.name)
                try:
                    await peer.write(struct.pack("!I", len(data)) + data)
                    with pytest.raises(OSError):
                        await asyncio.wait_for(peer.read(), 2)
                finally:
                    peer.close()
                assert await call(control.root) == {"initialized": False}
                assert control.work is None or control.work.done()
            assert all(not pipe.handle for pipe in control.pipes)

        asyncio.run(exercise())

    def test_all_sixteen_instances_are_bounded_and_reusable(self, tmp_path):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                peers = []
                try:
                    for _ in range(16):
                        peers.append(control.api.open(control.name))
                    with pytest.raises(OSError) as error:
                        control.api.open(control.name)
                    assert (
                        getattr(error.value, "winerror", None) == 231
                    )  # ERROR_PIPE_BUSY
                    assert len(control.handlers) == len(control.pipes) == 16
                    assert control.work is None
                finally:
                    for peer in peers:
                        peer.close()
                for _ in range(100):
                    try:
                        result = await call(control.root)
                        break
                    except OSError as exc:
                        if getattr(exc, "winerror", None) != 231:
                            raise
                        await asyncio.sleep(0.02)
                else:
                    pytest.fail("native instances did not recover")
                assert result == {"initialized": False}

        asyncio.run(exercise())

    def test_idle_client_times_out_without_authority_work(self, tmp_path, monkeypatch):
        monkeypatch.setattr(c, "EXCHANGE_TIMEOUT", 0.1)

        async def exercise():
            async with serve(tmp_path / "authority") as control:
                peer = control.api.open(control.name)
                try:
                    with pytest.raises(OSError):
                        await asyncio.wait_for(peer.read(), 2)
                    assert control.work is None
                    assert all(not task.done() for task in control.handlers)
                finally:
                    peer.close()
                monkeypatch.setattr(c, "EXCHANGE_TIMEOUT", 10)
                assert await call(control.root) == {"initialized": False}

        asyncio.run(exercise())

    @pytest.mark.parametrize("damage", ["replace", "hardlink", "acl"])
    def test_locator_damage_fails_closed_and_is_preserved(self, tmp_path, damage):
        async def exercise():
            root = tmp_path / "authority"
            control = None
            try:
                async with serve(root) as control:
                    path = control.path
                    if damage == "replace":
                        replacement = root / "replacement"
                        replacement.write_bytes(path.read_bytes())
                        os.replace(replacement, path)
                    elif damage == "hardlink":
                        os.link(path, root / "extra-link")
                    else:
                        subprocess.run(
                            ["icacls", str(path), "/grant", "*S-1-1-0:R"],
                            check=True,
                            capture_output=True,
                        )
                    with pytest.raises(CONTROL_FAILURES):
                        await call(root)
                    assert control.work is None or control.work.done()
            except a.AuthorityError:
                assert damage in {"hardlink", "acl"}
            assert control is not None and all(not p.handle for p in control.pipes)
            assert (root / wc._LOCATOR).exists()

        asyncio.run(exercise())

    def test_partial_start_failure_closes_handles_without_publishing(
        self, tmp_path, monkeypatch
    ):
        async def exercise():
            root = a._root(tmp_path / "authority", create=True)
            control = wc.WindowsControl(root)
            real = control.api.create

            def create(*args, **kwargs):
                if len(control.pipes) == 3:
                    raise OSError("injected fourth-instance failure")
                return real(*args, **kwargs)

            monkeypatch.setattr(control.api, "create", create)
            try:
                with pytest.raises(OSError, match="fourth-instance"):
                    await control.start()
            finally:
                await control.close()
            assert len(control.pipes) == 3 and all(not p.handle for p in control.pipes)
            assert not control.path.exists()

        asyncio.run(exercise())

    def test_preclaimed_namespace_fails_without_publication(self, tmp_path):
        async def exercise():
            root = a._root(tmp_path / "authority", create=True)
            control = wc.WindowsControl(root)
            preclaimed = control.api.create(control.name, first=True)
            try:
                with pytest.raises(OSError):
                    await control.start()
            finally:
                await control.close()
                preclaimed.close()
            assert not control.path.exists() and not control.pipes

        asyncio.run(exercise())

    @pytest.mark.parametrize("data", [None, b"unrelated", b"x" * 513])
    def test_stale_locator_replaces_only_bounded_valid_record(self, tmp_path, data):
        async def exercise():
            root = a._root(tmp_path / "authority", create=True)
            old = r"\\.\pipe\arbiter-" + "1" * 32
            path = root / wc._LOCATOR
            path.write_bytes(
                data
                if data is not None
                else json.dumps({"version": 1, "name": old}).encode()
            )
            control = wc.WindowsControl(root)
            try:
                if data is None:
                    await control.start()
                    assert wc._locator(root)[0] == control.name != old
                else:
                    with pytest.raises(a.AuthorityError):
                        await control.start()
                    assert not control.pipes and path.read_bytes() == data
            finally:
                await control.close()
            assert path.exists() is (data is not None)

        asyncio.run(exercise())

    def test_failed_locator_publication_cleans_reserved_pending_and_native_handles(
        self, tmp_path, monkeypatch
    ):
        async def exercise():
            root = a._root(tmp_path / "authority", create=True)
            control = wc.WindowsControl(root)

            def fail(*args):
                raise OSError("injected locator publication failure")

            monkeypatch.setattr(a, "_publish", fail)
            try:
                with pytest.raises(OSError, match="publication failure"):
                    await control.start()
            finally:
                await control.close()
            assert len(control.pipes) == 16 and all(not p.handle for p in control.pipes)
            assert not control.path.exists()
            assert not (root / wc.generations._CONTROL_PENDING).exists()

        asyncio.run(exercise())

    def test_permissive_native_acl_rejected_without_dispatch(self, tmp_path):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                for pipe in control.pipes:
                    set_acl(
                        control.api,
                        pipe.handle,
                        f"D:P(A;;GA;;;{control.api.sid})(A;;GR;;;WD)",
                    )
                with pytest.raises(a.AuthorityError, match="ACL or owner"):
                    await call(control.root)
                assert control.work is None

        asyncio.run(exercise())

    def test_real_other_user_cannot_dispatch_even_with_test_only_acl_broadening(
        self, tmp_path, monkeypatch
    ):
        async def exercise():
            async with serve(tmp_path / "authority") as control:
                api = control.api
                with different_user(api) as token:
                    token_handle = token.value
                    assert token_handle is not None
                    impersonate = api.a.ImpersonateLoggedOnUser
                    impersonate.argtypes, impersonate.restype = [
                        wc.w.wt.HANDLE
                    ], wc.w.wt.BOOL

                    def connect():
                        assert impersonate(token)
                        try:
                            return api.k.CreateFileW(
                                control.name, 0xC0020000, 0, None, 3, 0x40110000, None
                            )
                        finally:
                            if not api.a.RevertToSelf():
                                os._exit(1)

                    assert connect() == wc.w._INVALID
                    for pipe in control.pipes:
                        set_acl(
                            api,
                            pipe.handle,
                            f"D:P(A;;GA;;;{api.sid})(A;;GA;;;{api.token_sid(token_handle)})",
                        )
                    # Deliberately bypass only the test listener ACL inspector so
                    # the real other-token input reaches the production SID check.
                    monkeypatch.setattr(api, "verify_acl", lambda handle: None)
                    handle = connect()
                    assert handle != wc.w._INVALID
                    peer = wc.w.Pipe(api, handle)
                    try:
                        await peer.write(
                            c._frame({"root": str(control.root), "action": "status"})
                        )
                        with pytest.raises(OSError):
                            await asyncio.wait_for(peer.read(), 2)
                        assert control.work is None
                        assert api.process_sid(api.k.GetCurrentProcess()) == api.sid
                    finally:
                        peer.close()

        asyncio.run(exercise())

    def test_cancelled_shutdown_retains_actual_job_and_instance_lock(
        self, tmp_path, monkeypatch
    ):
        cfg = AppConfig()
        cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
        cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "plugins")
        app = cast(Any, build_server(cfg, service_plugins=[], prepare_tls=False).app)
        root = tmp_path / "server" / "authority"
        entered, release, ready = threading.Event(), threading.Event(), asyncio.Event()
        real = wc.WindowsControl.dispatch

        def dispatch(control, value):
            entered.set()
            assert release.wait(10)
            return real(control, value)

        monkeypatch.setattr(wc.WindowsControl, "dispatch", dispatch)

        async def exercise():
            async def owner():
                async with app.router.lifespan_context(app):
                    ready.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(owner())
            client = None
            try:
                await asyncio.wait_for(ready.wait(), 10)
                client = asyncio.create_task(call(root))
                assert await asyncio.to_thread(entered.wait, 3)
                assert (await _asgi_request(app, "GET", "/_health_")).status_code == 200
                with pytest.raises(a.AuthorityError, match="work is busy"):
                    await call(root)
                client.cancel()
                task.cancel()
                await asyncio.sleep(0.03)
                task.cancel()
                await asyncio.sleep(0.03)
                assert not task.done()
                with pytest.raises(a.AuthorityBusy):
                    a.authority_status(root)
            finally:
                release.set()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                if client is not None:
                    with suppress(asyncio.CancelledError, OSError):
                        await client
            assert a.authority_status(root) is None
            assert not (root / wc._LOCATOR).exists()

        asyncio.run(exercise())
