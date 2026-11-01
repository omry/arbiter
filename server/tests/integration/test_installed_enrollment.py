"""Installed platform wheel, real terminal handoff and opaque proxy writes."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
from pathlib import Path
import os
import select
import socket
import socketserver
import sqlite3
import ssl
import subprocess
import sys
import threading
import urllib.request
from typing import cast

import pytest
from starlette.applications import Starlette

from arbiter_server.authority import initialize_authority
from arbiter_server.enrollment_receipt import result_fields
from arbiter_server.local_control import enrollment_request
from arbiter_server.owner_issuers import control as issuer_control
from arbiter_server.main import _generate_self_signed_tls_certificate
from server.tests.integration.test_cli_entrypoint import (
    ClientCommand,
    current_go_client_binary,
    packaged_arbiter_client_command,
)
from server.tests.unit.conftest import private_windows_test_parent, tmp_path
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_enrollment_activation import database
from server.tests.unit.test_native_http_protocol import _client


@contextmanager
def fragmented_passthrough(port):
    stopped = threading.Event()
    writes = []

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as upstream:
                peers = (self.request, upstream)
                try:
                    while not stopped.is_set():
                        ready, _, _ = select.select(peers, [], [], 0.1)
                        for source in ready:
                            data = source.recv(2048)
                            if not data:
                                return
                            destination = peers[1] if source is peers[0] else peers[0]
                            # The relay sees outer TLS ciphertext only. These
                            # split writes do not claim fixed TCP packet sizes.
                            for offset in range(0, len(data), 97):
                                piece = data[offset : offset + 97]
                                destination.sendall(piece)
                                writes.append((source is peers[0], len(piece)))
                except (ConnectionError, OSError):
                    return  # Peer cancellation is normal during lost replies.

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Relay) as proxy:
        proxy.daemon_threads = False
        thread = threading.Thread(target=proxy.serve_forever)
        thread.start()
        try:
            yield proxy.server_address[1], writes
        finally:
            stopped.set()
            proxy.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("mode", ["ok", "lost-reply", "promotion-failure", "reuse"])
def test_installed_enrollment_and_resume(
    tmp_path,
    private_windows_test_parent,
    packaged_arbiter_client_command: ClientCommand,
    monkeypatch,
    mode,
):
    source = None
    if mode == "reuse":
        source_root = tmp_path / "source"
        source_root.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        source = installed_journey(
            source_root, packaged_arbiter_client_command, monkeypatch, "ok"
        )
        # The first journey has closed A and its proxy before B is started.
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", source["port"]), timeout=0.5)
        tmp_path = tmp_path / "target"
        tmp_path.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        mode = "lost-reply"
    installed_journey(
        tmp_path, packaged_arbiter_client_command, monkeypatch, mode, source
    )


def installed_journey(tmp_path, command, monkeypatch, mode, source=None):
    root = tmp_path / "server" / "authority"
    initialize_authority(
        root,
        recovery_file=(source["base"] if source else tmp_path) / "operator.json",
        reuse_recovery_file=source is not None,
    )
    if source:
        _, fields = result_fields(
            base64.b64decode(source["saved"]["result"], validate=True)
        )
        issuer_control(
            root, "register", dict(certificate_der=base64.b64encode(fields[2]).decode())
        )
    app = cast(Starlette, _client(tmp_path)._app)
    responder, requests = app.state.pairing_responder, app.state.enrollment_requests
    original_activate = responder.activate
    committed = []

    async def activate(*args):
        receipt = await original_activate(*args)
        committed.append(True)
        if mode == "lost-reply":
            raise ValueError("synthetic loss after actual activation commit")
        return receipt

    monkeypatch.setattr(responder, "activate", activate)
    certificate, key = tmp_path / "outer.crt", tmp_path / "outer.key"
    _generate_self_signed_tls_certificate(
        cert_path=certificate,
        key_path=key,
        names=("127.0.0.1",),
    )
    directory = source["directory"] if source else tmp_path / "installed-custody"
    if not source:
        directory.mkdir(mode=0o700)
    marker = directory / "enrolled"
    if mode == "promotion-failure":
        marker.write_text("preserve unrelated existing file")
    with (
        live_server(
            app,
            ws=app.state.native_websocket_protocol,
            http=app.state.native_http_protocol,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
        ) as port,
        fragmented_passthrough(port) as (proxy_port, writes),
    ):
        requests.open_window()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(30)

            def approve():
                connection, _ = listener.accept()
                with connection, connection.makefile("rwb") as stream:
                    connection.settimeout(20)
                    handoff = json.loads(stream.readline(1025))
                    pending = next(
                        p
                        for p in requests.pending()
                        if p.advertisement.selector == handoff["selector"]
                    )
                    enrollment_request(
                        root,
                        "approve",
                        dict(
                            selector=pending.advertisement.selector,
                            revision=pending.revision,
                            request_digest=pending.advertisement.digest,
                            psk=handoff["secret"],
                            grants=[],
                        ),
                    )
                    stream.write(b"true\n")
                    stream.flush()

            config = dict(
                command=str(command.command),
                directory=str(directory),
                url=f"https://127.0.0.1:{proxy_port}",
                control=listener.getsockname()[1],
                recover=mode == "promotion-failure",
                identity=source["selector"] if source else "",
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                owner = pool.submit(approve)
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = subprocess.CREATE_NEW_CONSOLE
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("enrollment_terminal_driver.py")),
                    ],
                    input=json.dumps(config) + "\n",
                    text=True,
                    capture_output=True,
                    timeout=65,
                    creationflags=creationflags,
                )
                assert (
                    result.returncode == 0
                ), "installed native terminal journey failed"
                owner.result(timeout=5)
            completion = json.loads(result.stdout)
        selector = completion["selector"]
        assert completion["activated"] == (mode != "promotion-failure")
        assert committed == [True]
        if mode == "promotion-failure":
            assert marker.read_text() == "preserve unrelated existing file"
            before = (directory / "prepared" / (selector + ".json")).read_bytes()
            marker.unlink()  # Explicit operator repair of this test-owned collision.
        else:
            before = (directory / "enrolled" / (selector + ".json")).read_bytes()
        for _ in range(2):
            resumed = subprocess.run(
                [
                    config["command"],
                    "--config-dir",
                    str(directory),
                    "arbiter.connect_timeout=15s",
                    "enrol",
                    "resume=" + selector,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert resumed.returncode == 0, "installed same-selector recovery failed"
            assert "Enrollment activated and saved" in resumed.stdout
        enrolled = directory / "enrolled" / (selector + ".json")
        assert enrolled.read_bytes() == before
        expected = [enrolled]
        if source:
            expected.append(directory / "enrolled" / (source["selector"] + ".json"))
        assert set((directory / "enrolled").glob("*.json")) == set(expected)
        assert not list((directory / "prepared").glob("*.json"))
        saved = json.loads(before)
        assert saved["provisional"]["version"] == 2
        assert set(saved["provisional"]) >= {
            "access_key_pkcs8",
            "portable_key_pkcs8",
            "recipient_key_pkcs8",
        }
        if source:
            original = directory / "enrolled" / (source["selector"] + ".json")
            assert original.read_bytes() == source["raw"]
            for key in ("access_key_pkcs8", "recipient_key_pkcs8"):
                assert saved["provisional"][key] != source["saved"]["provisional"][key]
            assert (
                saved["provisional"]["portable_key_pkcs8"]
                == source["saved"]["provisional"]["portable_key_pkcs8"]
            )
            a_ids, a_fields = result_fields(
                base64.b64decode(source["saved"]["result"], validate=True)
            )
            b_ids, b_fields = result_fields(
                base64.b64decode(saved["result"], validate=True)
            )
            assert a_ids[1:3] == b_ids[1:3]  # Owner and portable agent identity.
            assert a_ids[0] != b_ids[0] and a_ids[3] != b_ids[3]
            assert a_fields[2:4] == b_fields[2:4]
        with closing(sqlite3.connect(database(root))) as db:
            rows = db.execute("SELECT prepared FROM enrollments").fetchall()
            assert len(rows) == 1
            assert bytes(rows[0][0]) == base64.b64decode(saved["result"], validate=True)
        assert not requests.pending() and not responder._attempts
        assert writes and {direction for direction, _ in writes} == {False, True}
        assert all(0 < size <= 97 for _, size in writes)
        with urllib.request.urlopen(
            f"https://127.0.0.1:{port}/_health_",
            context=ssl._create_unverified_context(),
            timeout=5,
        ) as health:
            assert health.status == 200 and json.load(health) == {"status": "ok"}
    return dict(
        directory=directory,
        selector=selector,
        saved=saved,
        raw=before,
        base=tmp_path,
        port=proxy_port,
    )
