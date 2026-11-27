import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from arbiter_server.errors import OperationError
from arbiter_server.services import (
    OperationCatalog,
    RuntimeRegistry,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.storage import PluginStorage
from arbiter_whatsapp import WhatsAppRuntime, WhatsAppServicePlugin
from arbiter_whatsapp.credentials import BindingStore
from arbiter_whatsapp.state import StateStore
from arbiter_whatsapp.transport import ProviderClient


def page(cursor=7, message_id="message-1"):
    return {
        "object": "whatsapp_agent_platform",
        "entry": [
            {
                "id": "provider-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [],
                            "statuses": [],
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": "user:creator",
                                    "timestamp": "123",
                                    "type": "text",
                                    "text": {"body": "synthetic body-canary"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
        "next_offset": cursor,
    }


@pytest.fixture
def storage(tmp_path):
    if os.name == "nt":
        from arbiter_server.file_protection.windows import _windows_file_security

        owner = _windows_file_security(tmp_path).owner_sid
        subprocess.run(
            [
                "icacls",
                str(tmp_path),
                "/inheritance:r",
                "/remove:g",
                "*S-1-3-4",
                "/grant:r",
                f"*{owner}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )
    result = PluginStorage(plugin_name="whatsapp", root=tmp_path)
    state = StateStore(result)
    state.directory("bindings", create=True)
    state.directory("locks", create=True)
    with state.database(initialize=True):
        pass
    BindingStore(state).publish(
        "whatsapp/first", "provider-1", "credential-canary", previous=None
    )
    return result


def runtime_and_catalog(storage, client):
    runtime = WhatsAppRuntime(
        {"first": {"credential": "whatsapp/first"}},
        {"default": {}},
        ServiceRuntimeContext(
            dependencies={"plugin_storage": storage, "whatsapp_client": client}
        ),
    )
    catalog = OperationCatalog(
        [WhatsAppServicePlugin()],
        ServicePluginContext(RuntimeRegistry({"whatsapp": runtime})),
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )
    return runtime, catalog


def invoke(catalog, **args):
    return catalog.invoke_operation(
        "whatsapp:receive_updates", {"account": "first", **args}
    )


def rows(storage):
    with StateStore(storage).database() as db:
        return (
            db.execute("SELECT normal_offset FROM agents").fetchone()[0],
            db.execute("SELECT message_id FROM sources ORDER BY message_id").fetchall(),
        )


def test_real_http_serialization_catalog_normal_replay_and_409(storage, capsys):
    requests = []
    responses = [(200, page()), (200, page(3, "replay-1")), (409, None), (204, None)]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            status, body = responses.pop(0)
            encoded = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def connection(host, port, **kwargs):
        assert (host, port) == ("api.whatsapp.com", 443)
        return http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=kwargs["timeout"]
        )

    _, catalog = runtime_and_catalog(
        storage, ProviderClient(connection_factory=connection)
    )
    try:
        assert invoke(catalog, offset=0)["next_offset"] == 7
        assert invoke(catalog, offset=0, replay=True)["next_offset"] == 3
        with pytest.raises(OperationError) as error:
            invoke(catalog)
        assert error.value.domain_code == "poll_replaced"
        assert invoke(catalog)["next_offset"] == 7
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert len(requests) == 4
    assert [parse_qs(urlsplit(path).query) for path, _ in requests] == [
        {"offset": ["0"], "limit": ["100"], "timeout": ["15"]},
        {"offset": ["0"], "limit": ["100"], "timeout": ["15"]},
        {"offset": ["7"], "limit": ["100"], "timeout": ["15"]},
        {"offset": ["7"], "limit": ["100"], "timeout": ["15"]},
    ]
    assert all(auth == "Bearer credential-canary" for _, auth in requests)
    assert rows(storage) == (7, [("message-1",), ("replay-1",)])
    assert capsys.readouterr() == ("", "")
