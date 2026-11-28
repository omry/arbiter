from email import policy
from email.parser import BytesParser
from io import BytesIO
import json

from PIL import Image
import pytest

from arbiter_server.artifacts import ArtifactConsumed, ArtifactStore
from arbiter_server.errors import OperationError
from arbiter_whatsapp.config import WhatsAppPolicy
from arbiter_whatsapp.media import MIME_LIMITS, upload_media, validate_image
from arbiter_whatsapp.transport import MediaUpload, ProviderResponse, failure
from plugins.whatsapp.tests.unit.test_transport import endpoint
from plugins.whatsapp.tests.unit.test_updates import configured


@pytest.fixture
def upload_setup(configured, tmp_path):
    runtime, provider, catalog = configured
    store = ArtifactStore(
        root=tmp_path / "artifacts", base_url="http://localhost/_arbiter/artifacts"
    )
    runtime.artifacts = store.for_plugin("whatsapp")
    return runtime, provider, catalog, store


def artifact_input(store, data=b"example", mime="text/plain"):
    descriptor = store.for_plugin("imap").create(
        content=data, filename="private-canary.txt", content_type=mime, source={}
    )
    return {"id": descriptor.id, "url": descriptor.url}


def invoke(catalog, content, media_type="text/plain"):
    return catalog.invoke_operation(
        "whatsapp:upload_media",
        {"account": "first", "content": content, "media_type": media_type},
    )


def test_catalog_upload_consumes_cross_plugin_artifact_and_returns_only_media_id(
    upload_setup,
):
    runtime, provider, catalog, store = upload_setup
    content = artifact_input(store)
    arguments = {"account": "first", "content": content, "media_type": "text/plain"}
    catalog.check_operation("whatsapp:upload_media", arguments)
    assert provider.calls == []
    provider.responses.append(
        ProviderResponse(200, b'{"id":"media-123","url":"private-canary"}')
    )
    assert invoke(catalog, content) == {"media_id": "media-123"}
    method, path, token, options = provider.calls[0]
    assert (method, path, token) == ("POST", "/agent/v1/media", "credential-canary")
    assert options["multipart"] == MediaUpload(b"example", "text/plain")
    with pytest.raises(OperationError) as error:
        invoke(catalog, content)
    assert error.value.domain_code == "artifact_unavailable"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "egress,send", [(False, True), (False, False), (True, False), (True, True)]
)
def test_upload_permission_and_send_independence(upload_setup, egress, send):
    runtime, provider, catalog, store = upload_setup
    runtime.policies["default"] = WhatsAppPolicy(
        media_egress=egress, send_messages=send
    )
    content = artifact_input(store)
    if egress:
        provider.responses.append(ProviderResponse(200, b'{"id":"media-1"}'))
        assert invoke(catalog, content) == {"media_id": "media-1"}
    else:
        with pytest.raises(OperationError) as error:
            invoke(catalog, content)
        assert error.value.code == "policy_denied"
        assert provider.calls == []
        assert (
            runtime.artifacts.read_once(
                content["id"],
                content["url"],
                allowed_content_types={"text/plain"},
                max_bytes=10,
            ).content
            == b"example"
        )


@pytest.mark.parametrize(
    "mime,declared,limit",
    [
        ("text/plain", "application/pdf", 100),
        ("text/html", "text/html", 100),
        ("text/plain", "text/plain", 6),
    ],
)
def test_type_and_known_size_reject_before_consumption(
    upload_setup, monkeypatch, mime, declared, limit
):
    runtime, provider, catalog, store = upload_setup
    content = artifact_input(store, mime=mime)
    if mime == "text/plain" and declared == mime:
        monkeypatch.setitem(MIME_LIMITS, mime, limit)
    with pytest.raises(OperationError):
        invoke(catalog, content, declared)
    assert provider.calls == []
    assert (
        runtime.artifacts.read_once(
            content["id"], content["url"], allowed_content_types={mime}, max_bytes=100
        ).size
        == 7
    )


@pytest.mark.parametrize(
    "response",
    [
        ProviderResponse(200, b"{}"),
        ProviderResponse(200, b'{"id":"../invalid"}'),
        ProviderResponse(200, b'{"id":"a","id":"b"}'),
        ProviderResponse(200, b"canary"),
        ProviderResponse(204),
        ProviderResponse(302),
        ProviderResponse(500),
        ProviderResponse(429),
    ],
)
def test_upload_bad_response_never_retries_and_input_remains_consumed(
    upload_setup, response, capsys
):
    runtime, provider, catalog, store = upload_setup
    content = artifact_input(store)
    provider.responses.append(response)
    with pytest.raises(OperationError) as error:
        invoke(catalog, content)
    assert error.value.replay == "unsafe"
    assert len(provider.calls) == 1
    with pytest.raises(OperationError):
        invoke(catalog, content)
    assert len(provider.calls) == 1
    assert "canary" not in json.dumps(error.value.to_dict())
    assert "canary" not in str(capsys.readouterr())


def test_transport_failure_requires_fresh_input_even_before_submission(upload_setup):
    runtime, provider, catalog, store = upload_setup
    content = artifact_input(store)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise failure("upstream_timeout", "transport_timeout")

    runtime.client.request = fail
    with pytest.raises(OperationError) as error:
        invoke(catalog, content)
    assert error.value.replay == "unsafe" and len(calls) == 1
    with pytest.raises(OperationError):
        invoke(catalog, content)
    assert len(calls) == 1


def image_bytes(fmt="PNG", mode="RGB", size=(2, 2)):
    buffer = BytesIO()
    Image.new(mode, size).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_valid_image_formats_upload(upload_setup, fmt, mime):
    _, provider, catalog, store = upload_setup
    content = artifact_input(store, image_bytes(fmt), mime)
    provider.responses.append(ProviderResponse(200, b'{"id":"media-1"}'))
    assert invoke(catalog, content, mime) == {"media_id": "media-1"}


@pytest.mark.parametrize(
    "data",
    [
        b"not-an-image",
        image_bytes("PNG", "L"),
        image_bytes("PNG", "I;16"),
        image_bytes("JPEG"),
        image_bytes()[:30],
    ],
)
def test_image_validation_failure_consumes_without_transport(upload_setup, data):
    _, provider, catalog, store = upload_setup
    content = artifact_input(store, data, "image/png")
    with pytest.raises(OperationError) as error:
        invoke(catalog, content, "image/png")
    assert (
        error.value.domain_code == "invalid_media_content"
        and error.value.replay == "unsafe"
    )
    assert provider.calls == []
    with pytest.raises(OperationError) as error:
        invoke(catalog, content, "image/png")
    assert error.value.domain_code == "artifact_unavailable"


def test_image_dimensions_at_limit_and_above_without_decode():
    # Compressible solid-color inputs exercise actual headers, not a mocked PIL image.
    validate_image(image_bytes(size=(5000, 5000)), "image/png")
    with pytest.raises(OperationError):
        validate_image(image_bytes(size=(5001, 5000)), "image/png")
    validate_image(image_bytes("WEBP", size=(4096, 1)), "image/webp")
    with pytest.raises(OperationError):
        validate_image(image_bytes("WEBP", size=(4097, 1)), "image/webp")


def test_animated_webp_upload_is_supported():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        buffer,
        format="WEBP",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")],
        duration=100,
        loop=0,
    )
    validate_image(buffer.getvalue(), "image/webp")


def test_actual_transport_multipart_is_bounded_and_contains_only_fixed_fields(
    upload_setup, endpoint
):
    runtime, provider, catalog, store = upload_setup
    client, observed, responses = endpoint
    runtime.client = client
    data = b"file bytes\x00\xff\r\n"
    content = artifact_input(store, data)
    responses.append((200, b'{"id":"media-1"}', {}))
    assert invoke(catalog, content) == {"media_id": "media-1"}
    path, auth, body = observed[0]
    assert path == "/agent/v1/media" and auth == "Bearer credential-canary"
    boundary = body.split(b"\r\n", 1)[0][2:]
    message = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: multipart/form-data; boundary="'
        + boundary
        + b'"\r\nMIME-Version: 1.0\r\n\r\n'
        + body
    )
    parts = list(message.iter_parts())
    assert [p.get_param("name", header="content-disposition") for p in parts] == [
        "messaging_product",
        "type",
        "file",
    ]
    assert [p.get_payload(decode=True) for p in parts] == [
        b"whatsapp",
        b"text/plain",
        data,
    ]
    assert parts[-1].get_filename() == "upload"
    assert b"private-canary" not in body and content["url"].encode() not in body
    assert provider.calls == []


def test_maximum_upload_size_is_admitted_and_one_more_is_rejected():
    data = b"x" * 16_000_000
    body, content_type = MediaUpload(data, "application/octet-stream").encode()
    assert data in body and len(body) < len(data) + 1024
    assert content_type.startswith("multipart/form-data; boundary=")
    with pytest.raises(OperationError):
        MediaUpload(data + b"x", "application/octet-stream").encode()


@pytest.mark.parametrize("chunk", [b"VP8 ", b"VP8L", b"VP8X"])
@pytest.mark.parametrize("axis", ["width", "height"])
def test_webp_oversize_header_rejected_before_native_decoder(monkeypatch, chunk, axis):
    header = bytearray(b"RIFF" + bytes(4) + b"WEBP" + chunk + bytes(14))
    width, height = (4097, 1) if axis == "width" else (1, 4097)
    if chunk == b"VP8X":
        header[24:27] = (width - 1).to_bytes(3, "little")
        header[27:30] = (height - 1).to_bytes(3, "little")
    elif chunk == b"VP8L":
        header[20] = 0x2F
        header[21:25] = ((width - 1) | ((height - 1) << 14)).to_bytes(4, "little")
    else:
        header[23:26] = b"\x9d\x01\x2a"
        header[26:28] = width.to_bytes(2, "little")
        header[28:30] = height.to_bytes(2, "little")

    def forbidden(*args, **kwargs):
        pytest.fail("oversized WebP entered the native decoder")

    monkeypatch.setattr(Image, "open", forbidden)
    with pytest.raises(OperationError) as error:
        validate_image(bytes(header), "image/webp")
    assert error.value.domain_code == "invalid_media_content"


def test_lossless_webp_header_is_supported():
    buffer = BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 128)).save(
        buffer, format="WEBP", lossless=True
    )
    validate_image(buffer.getvalue(), "image/webp")
