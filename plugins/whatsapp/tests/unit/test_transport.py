import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading

import pytest

from arbiter_server.errors import OperationError
from arbiter_whatsapp.transport import ProviderClient, decode_json, token_text


@pytest.fixture
def endpoint():
    observed = []
    responses = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.path, self.headers.get("Authorization"), body))
            status, data, headers = responses.pop(0)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            observed.append((self.path, self.headers.get("Authorization")))
            status, body, headers = responses.pop(0)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def factory(host, port, *, timeout, context):
        assert (host, port) == ("api.whatsapp.com", 443)
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        return http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=timeout
        )

    yield ProviderClient(connection_factory=factory), observed, responses
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_fixed_origin_serialization_and_no_redirect(endpoint):
    client, observed, responses = endpoint
    responses.append((302, b"", {"Location": "https://untrusted.invalid/steal"}))
    response = client.request(
        "GET",
        "/agent/v1/updates",
        "synthetic-secret",
        query={"offset": 0, "limit": 100, "timeout": 0},
    )
    assert response.status == 302
    assert observed == [
        ("/agent/v1/updates?offset=0&limit=100&timeout=0", "Bearer synthetic-secret")
    ]
    assert not responses


def test_successful_bounded_body_is_read_exactly(endpoint):
    client, observed, responses = endpoint
    body = b'{"ok":true}'
    responses.append((200, body, {}))
    response = client.request(
        "GET",
        "/agent/v1/updates",
        "synthetic",
        query={"offset": 0},
        response_limit=len(body),
    )
    assert response.body == body and decode_json(response.body) == {"ok": True}
    assert len(observed) == 1


@pytest.mark.parametrize(
    "path",
    [
        "https://untrusted.invalid",
        "//untrusted.invalid/x",
        "/agent/v1/updates\r\nX: bad",
        "/other",
    ],
)
def test_unrecognized_request_never_connects(path):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected transport")

    with pytest.raises(OperationError):
        ProviderClient(connection_factory=forbidden).request(
            "GET", path, "synthetic", query={}
        )


def test_response_bounds_and_safe_error(endpoint, capsys):
    client, observed, responses = endpoint
    responses.append((200, b"x" * 1025, {}))
    with pytest.raises(OperationError) as error:
        client.request(
            "GET",
            "/agent/v1/updates",
            "redaction-canary",
            query={},
            response_limit=1024,
        )
    assert error.value.domain_code == "response_too_large"
    assert "redaction-canary" not in json.dumps(error.value.to_dict())
    assert "redaction-canary" not in str(error.value)
    assert capsys.readouterr() == ("", "")
    assert len(observed) == 1


def test_dns_budget_overrun_prevents_submission():
    clock = [0.0]

    class Connection:
        sock = None

        def connect(self):
            clock[0] = 31

        def request(self, *args, **kwargs):
            pytest.fail("request after DNS overrun")

        def close(self):
            pass

    client = ProviderClient(
        clock=lambda: clock[0], connection_factory=lambda *a, **k: Connection()
    )
    with pytest.raises(OperationError) as error:
        client.request("GET", "/agent/v1/updates", "synthetic", query={})
    assert error.value.code == "upstream_timeout"


def test_transport_failure_has_no_hidden_retry_or_exception_diagnostic():
    calls = []

    class Connection:
        def connect(self):
            calls.append("connect")
            raise OSError("sensitive-canary")

        def close(self):
            calls.append("close")

    with pytest.raises(OperationError) as error:
        ProviderClient(connection_factory=lambda *a, **k: Connection()).request(
            "GET", "/agent/v1/updates", "synthetic", query={}
        )
    assert calls == ["connect", "close"]
    assert error.value.domain_code == "transport_failed"
    assert "sensitive-canary" not in json.dumps(error.value.to_dict())


@pytest.mark.parametrize(
    "body", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b'{"x":Infinity}', b"\xff"]
)
def test_untrusted_json_rejected(body):
    with pytest.raises(OperationError):
        decode_json(body)


@pytest.mark.parametrize("value", ["", "a\nb", "a\rb", "a\x00b", "x" * 8193, "\u2603"])
def test_invalid_token_is_rejected_without_diagnostics(value):
    with pytest.raises(OperationError) as error:
        token_text(value)
    assert error.value.domain_code == "invalid_credential"


@pytest.mark.parametrize("phase", ["connect", "request", "response", "read"])
@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("path", ["/agent/v1/messages", "/agent/v1/statuses"])
def test_write_transport_phase_proves_non_submission_only_before_request(
    phase, timeout, path
):
    from arbiter_whatsapp.transport import WriteNotSubmitted

    calls = []

    class Response:
        status = 200

        def read1(self, size):
            calls.append("read")
            if phase == "read":
                raise TimeoutError("secret") if timeout else OSError("secret")
            return b""

        def close(self):
            calls.append("response-close")

    class Connection:
        sock = None

        def connect(self):
            calls.append("connect")
            if phase == "connect":
                raise TimeoutError("secret") if timeout else OSError("secret")

        def request(self, *args, **kwargs):
            calls.append("request")
            assert args == ("POST", path)
            assert kwargs["headers"]["Content-Type"] == "application/json"
            assert json.loads(kwargs["body"]) == {"type": "text"}
            if phase == "request":
                raise TimeoutError("secret") if timeout else OSError("secret")

        def getresponse(self):
            calls.append("response")
            if phase == "response":
                raise TimeoutError("secret") if timeout else OSError("secret")
            return Response()

        def close(self):
            calls.append("close")

    with pytest.raises(OperationError) as error:
        ProviderClient(connection_factory=lambda *a, **k: Connection()).request(
            "POST",
            path,
            "credential-canary",
            query={},
            json_body={"type": "text"},
        )
    assert isinstance(error.value, WriteNotSubmitted) == (phase == "connect")
    assert error.value.replay == ("safe" if phase == "connect" else "unsafe")
    assert error.value.code == (
        "upstream_timeout" if timeout else "upstream_unavailable"
    )
    assert calls.count("request") == (0 if phase == "connect" else 1)
    assert calls[-1] == "close"
    assert "secret" not in json.dumps(error.value.to_dict())
    assert "credential-canary" not in str(error.value)


@pytest.mark.parametrize("stage", ["connect", "request", "response"])
def test_upload_transport_failure_keeps_submission_boundary_and_closes(stage):
    from arbiter_whatsapp.transport import MediaUpload, WriteNotSubmitted

    calls = []

    class Connection:
        sock = None

        def connect(self):
            calls.append("connect")
            if stage == "connect":
                raise TimeoutError("private-canary")

        def request(self, method, path, *, body, headers):
            calls.append("request")
            assert (method, path) == ("POST", "/agent/v1/media")
            assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
            assert b'filename="upload"' in body and b"payload" in body
            if stage == "request":
                raise OSError("private-canary")

        def getresponse(self):
            calls.append("response")
            raise OSError("private-canary")

        def close(self):
            calls.append("close")

    with pytest.raises(OperationError) as caught:
        ProviderClient(connection_factory=lambda *a, **k: Connection()).request(
            "POST",
            "/agent/v1/media",
            "synthetic",
            query={},
            multipart=MediaUpload(b"payload", "application/octet-stream"),
        )
    assert isinstance(caught.value, WriteNotSubmitted) == (stage == "connect")
    assert caught.value.replay == ("safe" if stage == "connect" else "unsafe")
    assert calls.count("connect") == 1 and calls[-1] == "close"
    assert "private-canary" not in json.dumps(caught.value.to_dict())


@pytest.mark.parametrize("multipart", [b"raw", {"content": b"raw"}, None])
def test_upload_transport_rejects_untyped_payload_before_connection(multipart):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected connection")

    with pytest.raises(OperationError):
        ProviderClient(connection_factory=forbidden).request(
            "POST", "/agent/v1/media", "synthetic", query={}, multipart=multipart
        )
