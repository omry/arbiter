"""Kill the actual Python server after commit; recover with a fresh Go process."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading

import pytest

from arbiter_server.authority import initialize_authority
from arbiter_server.main import _generate_self_signed_tls_certificate
from server.tests.unit.conftest import private_windows_test_parent, tmp_path


SERVER = """
import asyncio,json,sys
from pathlib import Path
from server.tests.unit.test_native_http_protocol import _client
from server.tests.unit.test_artifact_http_admission import live_server
d=json.loads(sys.stdin.readline())
root=Path(d['root']); app=_client(root)._app
responder=app.state.pairing_responder
original=responder.activate
async def activate(*args):
    wire=await original(*args)
    print('COMMITTED',flush=True)
    await asyncio.Event().wait()
if d['drop']: responder.activate=activate
with live_server(app,ws=app.state.native_websocket_protocol,http=app.state.native_http_protocol,
                 bind_port=d['port'],ssl_certfile=str(root/'outer.crt'),ssl_keyfile=str(root/'outer.key')) as port:
    requests=app.state.enrollment_requests
    if d['drop']: requests.open_window()
    print(json.dumps(dict(port=port,pending=len(requests.pending()))),flush=True)
    for line in sys.stdin:
        command=json.loads(line)
        if command=='stop': break
        pending=next(p for p in requests.pending() if p.advertisement.selector==command['selector'])
        requests.control('approve',dict(selector=pending.advertisement.selector,revision=pending.revision,
                         request_digest=pending.advertisement.digest,psk=command['secret'],grants=[]),set())
        print('APPROVED',flush=True)
"""


def test_real_server_crash_then_fresh_go_resume(tmp_path, private_windows_test_parent):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    initialize_authority(
        tmp_path / "server" / "authority", recovery_file=tmp_path / "operator.json"
    )
    _generate_self_signed_tls_certificate(
        cert_path=tmp_path / "outer.crt",
        key_path=tmp_path / "outer.key",
        names=("127.0.0.1",),
    )
    repo = Path(__file__).parents[3]
    server = client = None

    def stop():
        for process in (client, server):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    watchdog = threading.Timer(90, stop)
    watchdog.start()
    with (tmp_path / "server.log").open("w+") as log:
        try:
            port = 0
            for mode in ("commit-stop", "resume"):
                server = subprocess.Popen(
                    [sys.executable, "-c", SERVER],
                    cwd=repo,
                    env={**os.environ, "PYTHONPATH": str(repo / "server" / "src")},
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                )
                assert server.stdin is not None and server.stdout is not None
                server.stdin.write(
                    json.dumps(
                        dict(root=str(tmp_path), port=port, drop=mode == "commit-stop")
                    )
                    + "\n"
                )
                server.stdin.flush()
                started = server.stdout.readline()
                if not started:
                    log.seek(0)
                    pytest.fail(f"server startup failed in {mode}: {log.read()}")
                status = json.loads(started)
                port = status["port"]
                assert status["pending"] == 0
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    listener.settimeout(45)
                    client = subprocess.Popen(
                        [
                            "go",
                            "test",
                            "-count=1",
                            "-run",
                            "^TestPythonEnrollmentCLI$",
                            "./internal/cli",
                        ],
                        cwd=repo / "client" / "go-cli",
                        env={
                            **os.environ,
                            "ARBITER_TEST_ENROLLMENT_CONTROL": f"127.0.0.1:{listener.getsockname()[1]}",
                        },
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    connection, _ = listener.accept()
                    with connection, connection.makefile("rwb") as stream:
                        connection.settimeout(20)
                        stream.write(
                            json.dumps(
                                dict(
                                    URL=f"https://127.0.0.1:{port}",
                                    Directory=str(tmp_path / "client"),
                                    Mode=mode,
                                )
                            ).encode()
                            + b"\n"
                        )
                        stream.flush()
                        if mode == "commit-stop":
                            handoff = json.loads(stream.readline(1025))
                            server.stdin.write(json.dumps(handoff) + "\n")
                            server.stdin.flush()
                            assert server.stdout.readline().strip() == "APPROVED"
                            stream.write(b"true\n")
                            stream.flush()
                            assert server.stdout.readline().strip() == "COMMITTED"
                            server.kill()
                            server.wait(timeout=10)
                    output, _ = client.communicate(timeout=60)
                    assert client.returncode == 0, output
                if mode == "commit-stop":
                    assert list((tmp_path / "client" / "prepared").glob("*.json"))
                    assert not (tmp_path / "client" / "enrolled").exists()
                else:
                    server.stdin.write('"stop"\n')
                    server.stdin.flush()
                    server.wait(timeout=15)
                    assert server.returncode == 0
            assert not list((tmp_path / "client" / "prepared").glob("*.json"))
            assert len(list((tmp_path / "client" / "enrolled").glob("*.json"))) == 1
        finally:
            watchdog.cancel()
            stop()
