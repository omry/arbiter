from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import ssl
from time import monotonic
from urllib.parse import urlencode

from arbiter_server.errors import OperationError, Replay, Transient


def failure(
    code: str, domain: str, *, transient: Transient = "no", replay: Replay = "safe"
) -> OperationError:
    return OperationError(
        code,
        "WhatsApp operation could not complete safely.",
        plugin="whatsapp",
        domain_code=domain,
        transient=transient,
        replay=replay,
    )


class WriteNotSubmitted(OperationError):
    """Transport proved that no HTTP request submission began."""


def token_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 8192
        or any(c in value for c in "\x00\r\n")
    ):
        raise failure("validation_error", "invalid_credential")
    # HTTP header encoding must fail before any request is submitted.
    try:
        value.encode("latin-1")
    except UnicodeError:
        raise failure("validation_error", "invalid_credential") from None
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode_json(body: bytes) -> dict:
    try:

        def invalid_constant(value: str) -> None:
            raise ValueError("nonfinite JSON number")

        result = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=invalid_constant,
        )
        if not isinstance(result, dict):
            raise ValueError("expected JSON object")
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise failure("upstream_protocol_error", "invalid_response") from None


@dataclass(frozen=True)
class ProviderResponse:
    status: int
    body: bytes = field(default=b"", repr=False)


class ProviderClient:
    """Fixed HTTPS origin; no proxy, redirect handling or hidden retries."""

    def __init__(self, *, clock=monotonic, connection_factory=None):
        self.clock = clock
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def request(
        self,
        method: str,
        path: str,
        credential: str,
        *,
        query: dict,
        json_body=None,
        multipart=None,
        deadline: float | None = None,
        response_limit: int = 4 * 1024 * 1024,
    ) -> ProviderResponse:
        write = method == "POST" and path == "/agent/v1/messages"
        if (
            not (write or (method == "GET" and path == "/agent/v1/updates"))
            or (not write and json_body is not None)
            or (write and (query or not isinstance(json_body, dict)))
            or multipart is not None
        ):
            raise failure("validation_error", "unsupported_request")
        token_text(credential)
        body = None
        if write:
            try:
                body = json.dumps(
                    json_body, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise failure("validation_error", "invalid_message") from None
        end = min(deadline if deadline is not None else float("inf"), self.clock() + 30)
        connection = None
        response = None
        submitted = False
        try:

            def remaining() -> float:
                left = end - self.clock()
                if left <= 0:
                    raise TimeoutError
                return left

            connection = self.connection_factory(
                "api.whatsapp.com",
                443,
                timeout=min(5.0, remaining()),
                context=ssl.create_default_context(),
            )
            connection.connect()
            # DNS may exceed socket timeout. Never submit after its budget overrun.
            left = remaining()
            sock = connection.sock
            if sock is not None:
                sock.settimeout(min(20.0, left))
            headers = {
                "Authorization": "Bearer " + credential,
                "Accept": "application/json",
            }
            if write:
                headers["Content-Type"] = "application/json"
            # request() may partially send before raising. Mark before entering it.
            submitted = True
            connection.request(
                method,
                path + ("?" + urlencode(query) if query else ""),
                body=body,
                headers=headers,
            )
            remaining()
            response = connection.getresponse()
            limit = (
                response_limit if response.status < 400 else min(response_limit, 16384)
            )
            chunks: list[bytes] = []
            size = 0
            while True:
                left = remaining()
                if sock is not None and sock.fileno() >= 0:
                    sock.settimeout(min(20.0, left))
                chunk = response.read1(min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise failure("upstream_protocol_error", "response_too_large")
                chunks.append(chunk)
            remaining()
            return ProviderResponse(response.status, b"".join(chunks))
        except OperationError as error:
            if write and submitted:
                error.replay = "unsafe"
            raise
        except (OSError, http.client.HTTPException, ValueError) as cause:
            code, domain = (
                ("upstream_timeout", "transport_timeout")
                if isinstance(cause, TimeoutError)
                else ("upstream_unavailable", "transport_failed")
            )
            error = failure(code, domain, transient="yes")
            if write and not submitted:
                error = WriteNotSubmitted(
                    code,
                    error.message,
                    plugin="whatsapp",
                    domain_code=domain,
                    transient="yes",
                    replay="safe",
                )
            elif write:
                error.replay = "unsafe"
            raise error from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()


def check_response(response: ProviderResponse) -> None:
    if response.status in {200, 204}:
        return
    code, domain = {
        400: ("upstream_auth_failed", "credential_rejected"),
        401: ("upstream_auth_failed", "credential_rejected"),
        409: ("conflict", "poll_replaced"),
        429: ("rate_limited", "provider_rate_limited"),
    }.get(response.status, ("upstream_unavailable", "provider_unavailable"))
    raise failure(code, domain, transient="yes" if response.status >= 500 else "no")
