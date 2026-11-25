import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import cast

import pytest
from omegaconf import OmegaConf

from arbiter_server.config import AppConfig
from arbiter_server.errors import OperationError
from arbiter_server.main import build_server
from arbiter_whatsapp import WhatsAppServicePlugin
from arbiter_whatsapp.transport import ProviderClient
from plugins.whatsapp.tests.unit.test_updates import configured
from plugins.whatsapp.tests.unit.test_messages import arguments, invoke, observe
from server.tests.unit.test_native_http_protocol import ASGIApp, ASGIClient


@pytest.fixture
def provider_endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, self.headers.get("Authorization"), body))
            data = json.dumps(
                {
                    "messaging_product": "whatsapp",
                    "contacts": [{"input": body["to"], "wa_id": body["to"]}],
                    "messages": [{"id": "wamid.sent"}],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def client_for(port):
    def connection(host, endpoint_port, **kwargs):
        assert (host, endpoint_port) == ("api.whatsapp.com", 443)
        return http.client.HTTPConnection("127.0.0.1", port, timeout=kwargs["timeout"])

    return ProviderClient(connection_factory=connection)


def test_real_http_catalog_to_provider_serialization_and_replay(
    configured, provider_endpoint, tmp_path, capsys
):
    runtime, _, _ = configured
    observe(runtime)
    port, calls = provider_endpoint
    runtime.client = client_for(port)

    class Plugin(WhatsAppServicePlugin):
        def build_runtime(self, accounts, policies, context):
            return runtime

    cfg = AppConfig()
    cfg.arbiter.account = {
        "whatsapp": {"first": {"credential": "whatsapp/first", "policy": "default"}}
    }
    cfg.arbiter.policy = {"whatsapp": {"default": {}}}
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path)
    cfg.arbiter.storage.server_data_dir = str(tmp_path / "server")
    server = build_server(
        OmegaConf.structured(cfg), service_plugins=[Plugin()], prepare_tls=False
    )
    http = ASGIClient(cast(ASGIApp, server.app))
    first = http.post(
        "/api/v1/operations/whatsapp:send_message", json={"args": arguments()}
    )
    assert first.status_code == 200
    assert first.json()["result"]["delivery"] == "accepted"
    second = http.post(
        "/api/v1/operations/whatsapp:send_message", json={"args": arguments()}
    )
    assert second.json()["result"]["idempotency_replayed"] is True
    invalid = arguments(
        message={"type": "text", "text": {"body": "canary", "unknown": True}}
    )
    assert (
        http.post(
            "/api/v1/operations/whatsapp:send_message", json={"args": invalid}
        ).status_code
        == 422
    )
    assert len(calls) == 1
    assert calls[0] == (
        "/agent/v1/messages",
        "Bearer credential-canary",
        {
            "messaging_product": "whatsapp",
            "to": "user:creator",
            "type": "text",
            "text": {"body": "Hello", "preview_url": False},
        },
    )
    assert capsys.readouterr() == ("", "")


CHILD = r"""
from contextlib import contextmanager
import http.client,json,sys,time
from pathlib import Path
from arbiter_server.services import ServiceRuntimeContext
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp import WhatsAppRuntime
from arbiter_whatsapp.messages import send_message
from arbiter_whatsapp.transport import ProviderClient
root,port,stage,marker=sys.argv[1:]
def pause():
    Path(marker).write_text('ready')
    while True: time.sleep(1)
class Connection(http.client.HTTPConnection):
    def request(self,*args,**kwargs):
        super().request(*args,**kwargs)
        if stage=='submission': pause()
def connection(host,endpoint_port,**kwargs):
    assert (host,endpoint_port)==('api.whatsapp.com',443)
    return Connection('127.0.0.1',int(port),timeout=kwargs['timeout'])
client=ProviderClient(connection_factory=connection)
r=WhatsAppRuntime({'first':{'credential':'whatsapp/first'}},{'default':{}},ServiceRuntimeContext(dependencies={'plugin_storage':PluginStorage(plugin_name='whatsapp',root=Path(root)),'whatsapp_client':client}))
if stage=='before-reservation-commit':
    original=r.state.database
    @contextmanager
    def database(*args,**kwargs):
        with original(*args,**kwargs) as db:
            db.set_trace_callback(lambda sql: pause() if sql=='COMMIT' else None)
            yield db
    r.state.database=database
elif stage=='after-reservation':
    original=client.request
    def request(*args,**kwargs):
        pause()
        return original(*args,**kwargs)
    client.request=request
elif stage=='before-result-commit':
    original=r.state.finish_reply
    def finish(*args,**kwargs):
        pause()
        return original(*args,**kwargs)
    r.state.finish_reply=finish
send_message(r,'first','user:creator',{'type':'text','text':{'body':'Hello'}},'inbound-1')
"""


@pytest.mark.parametrize(
    "stage",
    [
        "before-reservation-commit",
        "after-reservation",
        "submission",
        "before-result-commit",
    ],
)
def test_killed_submitter_cannot_cause_a_second_submission(
    configured, provider_endpoint, tmp_path, stage
):
    runtime, _, catalog = configured
    observe(runtime)
    port, calls = provider_endpoint
    runtime.client = client_for(port)
    marker = tmp_path / "ready"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            CHILD,
            str(runtime.state.path.parent),
            str(port),
            stage,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not marker.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert marker.exists(), child.communicate(timeout=2)
        if stage != "before-reservation-commit":
            # A separate process still owns the send lock, even before transport.
            with pytest.raises(OperationError) as error:
                invoke(catalog)
            assert error.value.domain_code == "reply_in_progress"
    finally:
        child.kill()
        child.communicate(timeout=5)
    if stage == "before-reservation-commit":
        assert calls == []
        assert invoke(catalog)["delivery"] == "accepted"
        assert len(calls) == 1
    else:
        before = len(calls)
        assert invoke(catalog)["delivery"] == "unknown"
        assert invoke(catalog)["idempotency_replayed"] is True
        assert len(calls) == before
        assert before == (0 if stage == "after-reservation" else 1)
