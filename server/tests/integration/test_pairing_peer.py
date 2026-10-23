"""Fresh Go production initiators talk to the actual bounded Python responder.

The loopback socket is trusted synthetic test control, including ephemeral PSK
handoff; it is not a plaintext product pairing carrier. Nothing secret is logged.
"""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from arbiter_server.enrollment import EnrollmentRequests
from arbiter_server.pairing import PairingResponder, PairingSession


@pytest.mark.parametrize("invalid", [None, "secret", "request"])
def test_fresh_python_go_pairing_peer(invalid):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")

    def serve(listener):
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(10)
            with conn.makefile("rwb") as stream:
                packet = json.loads(stream.readline(16385))
                raw = base64.b64decode(packet["advertisement"], validate=True)
                if invalid == "request":
                    changed = json.loads(raw)
                    changed["name"] = "Substituted requester"
                    raw = json.dumps(changed).encode()
                requests = EnrollmentRequests()
                requests.open_window()
                pending = requests.register(raw)
                secret = packet["secret"]
                if invalid == "secret":
                    secret = (
                        base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
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
                responder = PairingResponder(requests)

                def send(value):
                    stream.write(json.dumps(value).encode() + b"\n")
                    stream.flush()

                async def exchange():
                    lease = responder.reserve()
                    first = base64.b64decode(packet["first"], validate=True)
                    selector = pending.advertisement.selector
                    if invalid:
                        with pytest.raises(ValueError):
                            await responder.start(lease, selector, first)
                        assert not responder._attempts[selector].digests
                        send({"rejected": True})
                        return
                    session = await responder.start(lease, selector, first)
                    assert isinstance(session, PairingSession)
                    message = await responder.message(
                        session, b"Python server payload", encrypt=True
                    )
                    send(
                        {
                            "response": base64.b64encode(session.response).decode(),
                            "hash": base64.b64encode(session.handshake_hash).decode(),
                            "message": base64.b64encode(message).decode(),
                        }
                    )
                    reply = json.loads(stream.readline(16385))
                    assert (
                        await responder.message(
                            session, base64.b64decode(reply["message"], validate=True)
                        )
                        == b"Go client payload"
                    )
                    send({"OK": True})

                try:
                    asyncio.run(exchange())
                finally:
                    responder.close()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(120)
        with ThreadPoolExecutor(max_workers=1) as pool:
            peer = pool.submit(serve, listener)
            result = subprocess.run(
                [
                    "go",
                    "test",
                    "-count=1",
                    "-run",
                    "^TestPythonPairingPeer$",
                    "-v",
                    "./internal/identity",
                ],
                cwd=Path(__file__).resolve().parents[3] / "client" / "go-cli",
                env={
                    **os.environ,
                    "ARBITER_TEST_PAIRING_CONTROL": f"127.0.0.1:{listener.getsockname()[1]}",
                    "ARBITER_TEST_PAIRING_REJECT": "1" if invalid else "0",
                },
                capture_output=True,
                text=True,
                timeout=120,
            )
            peer.result(timeout=15)
            assert result.returncode == 0, "Go pairing peer failed"
            assert result.stdout.splitlines().count("ARBITER_PAIRING_PEER=pass") == 1
