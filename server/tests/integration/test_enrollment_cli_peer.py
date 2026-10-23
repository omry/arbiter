"""The real Go CLI uses Python HTTPS registration and the production carrier.

The separate loopback control socket is the trusted synthetic human handoff.
Secrets stay in memory and are never printed, passed in argv/env or persisted.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import cast

import pytest
from starlette.applications import Starlette

from arbiter_server.authority import initialize_authority
from arbiter_server.main import _generate_self_signed_tls_certificate
from server.tests.unit.conftest import private_windows_test_parent, tmp_path
from server.tests.unit.test_artifact_http_admission import live_server
from server.tests.unit.test_native_http_protocol import _client


@pytest.mark.parametrize("mode", ["ok", "closed", "restart", "wrong-secret"])
def test_fresh_python_go_enrollment_cli(tmp_path, private_windows_test_parent, mode):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    server_root = tmp_path / "server" / "authority"
    initialize_authority(server_root, recovery_file=tmp_path / "operator.json")
    app = cast(Starlette, _client(tmp_path)._app)
    cert, key = tmp_path / "outer.crt", tmp_path / "outer.key"
    _generate_self_signed_tls_certificate(
        cert_path=cert, key_path=key, names=("127.0.0.1",)
    )
    with live_server(
        app,
        ws=app.state.native_websocket_protocol,
        http=app.state.native_http_protocol,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
    ) as port:
        requests = app.state.enrollment_requests
        if mode != "closed":
            requests.open_window()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(30)

            def operator():
                connection, _ = listener.accept()
                with connection, connection.makefile("rwb") as stream:
                    connection.settimeout(20)
                    start = {
                        "URL": f"https://127.0.0.1:{port}",
                        "Directory": str(tmp_path / "client"),
                        "Mode": mode,
                    }
                    stream.write(json.dumps(start).encode() + b"\n")
                    stream.flush()
                    if mode == "closed":
                        return
                    handoff = json.loads(stream.readline(1025))
                    pending = next(
                        r
                        for r in requests.pending()
                        if r.advertisement.selector == handoff["selector"]
                    )
                    secret = handoff["secret"]
                    assert len(secret) == 43
                    if mode == "wrong-secret":
                        import base64

                        secret = (
                            base64.urlsafe_b64encode(os.urandom(32))
                            .decode()
                            .rstrip("=")
                        )
                    requests.control(
                        "approve",
                        dict(
                            selector=pending.advertisement.selector,
                            revision=pending.revision,
                            request_digest=pending.advertisement.digest,
                            psk=secret,
                            grants=[],
                        ),
                        set(),
                    )
                    if mode == "restart":
                        requests.reset()
                    stream.write(b"true\n")
                    stream.flush()

            with ThreadPoolExecutor(max_workers=1) as pool:
                owner = pool.submit(operator)
                result = subprocess.run(
                    [
                        "go",
                        "test",
                        "-count=1",
                        "-run",
                        "^TestPythonEnrollmentCLI$",
                        "./internal/cli",
                    ],
                    cwd=Path(__file__).parents[3] / "client" / "go-cli",
                    env={
                        **os.environ,
                        "ARBITER_TEST_ENROLLMENT_CONTROL": f"127.0.0.1:{listener.getsockname()[1]}",
                    },
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                owner.result(timeout=5)
        # Proofs do not create identities, grants, credentials or trust on disk.
        assert not (tmp_path / "client" / "enrolled").exists()
        if mode == "ok":
            sessions = app.state.pairing_responder._attempts.values()
            assert all(state.active is None for state in sessions)
