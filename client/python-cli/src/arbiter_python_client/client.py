from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import string
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from omegaconf import DictConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .cli_errors import print_cli_error
from .version import arbiter_python_client_version


DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
MCP_URL_ENV_VAR = "ARBITER_MCP_URL"
DEFAULT_CONFIG_DIR = "~/.arbiter"
DEFAULT_CLIENT_CONFIG_NAME = "arbiter-client"
DEFAULT_ARTIFACT_MAX_BYTES = 16 * 1024
DEFAULT_ARTIFACT_COMMAND_MAX_CHILD_STDOUT_BYTES = 256 * 1024
BOOTSTRAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_STAGED_DEPLOYMENT_WARNING_EMITTED = False
_CAPABILITY_FIELD_ALIASES = {
    "id": "id",
    "name": "id",
    "desc": "description",
    "description": "description",
    "version": "version",
    "num_accts": "account_count",
    "account_count": "account_count",
    "num_ops": "operation_count",
    "operation_count": "operation_count",
}
_ARTIFACT_CONTENT_TYPE_EXTENSIONS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/pdf": ".pdf",
    "application/rtf": ".rtf",
    "application/zip": ".zip",
    "application/json": ".json",
    "application/xml": ".xml",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/markdown": ".md",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "video/mp4": ".mp4",
}


@dataclass(frozen=True)
class ClientConfig:
    mcp_url: str | None = None


@dataclass(frozen=True)
class ResolvedMCPURL:
    url: str
    source: str


@dataclass(frozen=True)
class CapabilityQuery:
    fields: tuple[str, ...] = ()
    format: str | None = None


class ToolCallError(RuntimeError):
    pass


def _json_default(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def _print_json(value: object) -> None:
    print(json.dumps(value, default=_json_default, sort_keys=True))


def _print_yaml(value: object) -> None:
    yaml_ready = json.loads(json.dumps(value, default=_json_default))
    print(OmegaConf.to_yaml(OmegaConf.create(yaml_ready), resolve=True), end="")


def _print_account_summary(accounts: Mapping[str, object]) -> None:
    for capability, names in accounts.items():
        print(capability)
        if isinstance(names, Sequence) and not isinstance(names, str):
            for name in names:
                print(f"  {name}")


def _parse_capability_field_list(value: str) -> tuple[str, ...]:
    fields: list[str] = []
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    for raw_field in value.split(","):
        field = raw_field.strip()
        if not field:
            raise ValueError("cap list fields must not contain empty names")
        if field not in _CAPABILITY_FIELD_ALIASES:
            supported = ", ".join(sorted(_CAPABILITY_FIELD_ALIASES))
            raise ValueError(
                f"unsupported cap list field: {field}; supported fields: "
                f"{supported}"
            )
        if field not in fields:
            fields.append(field)
    return tuple(fields)


def _validate_capability_format(template: str) -> None:
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
        template
    ):
        if field_name is None:
            continue
        root_field = field_name.split(".", 1)[0].split("[", 1)[0]
        if root_field not in _CAPABILITY_FIELD_ALIASES:
            supported = ", ".join(sorted(_CAPABILITY_FIELD_ALIASES))
            raise ValueError(
                f"unsupported cap format field: {field_name}; supported fields: "
                f"{supported}"
            )


def _capability_format_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
        template
    ):
        if field_name is None:
            continue
        root_field = field_name.split(".", 1)[0].split("[", 1)[0]
        fields.add(root_field)
    return fields


def _parse_capability_query(query: Sequence[str]) -> CapabilityQuery:
    fields: tuple[str, ...] = ()
    template: str | None = None
    for item in query:
        key, separator, value = item.partition("=")
        if separator != "=" or key not in {"fields", "format"}:
            raise ValueError(
                "unsupported cap list query: "
                f"{item}; expected fields=desc,version,num_accts or "
                'format="{id}=={version}: {desc}"'
            )
        if key == "fields":
            fields = _parse_capability_field_list(value)
        elif key == "format":
            if not value:
                raise ValueError("cap list format must not be empty")
            _validate_capability_format(value)
            template = value
    return CapabilityQuery(fields=fields, format=template)


def _capability_query_uses_field(query: CapabilityQuery, field: str) -> bool:
    if any(
        _CAPABILITY_FIELD_ALIASES[query_field] == field for query_field in query.fields
    ):
        return True
    if query.format is None:
        return False
    return any(
        _CAPABILITY_FIELD_ALIASES[query_field] == field
        for query_field in _capability_format_fields(query.format)
    )


def _capability_field_value(capability: Mapping[str, object], field: str) -> object:
    canonical_field = _CAPABILITY_FIELD_ALIASES[field]
    if canonical_field == "account_count" and canonical_field not in capability:
        accounts = capability.get("accounts", [])
        if isinstance(accounts, Mapping):
            return len(accounts)
        if isinstance(accounts, Sequence) and not isinstance(accounts, str):
            return len(accounts)
    return capability.get(canonical_field, "")


def _capability_field_projection(
    capability: Mapping[str, object],
    fields: Sequence[str],
) -> dict[str, object]:
    projected = {"id": _capability_field_value(capability, "id")}
    for field in fields:
        if field == "id":
            continue
        projected[field] = _capability_field_value(capability, field)
    return projected


def _print_capability_field_rows(
    capabilities: Sequence[object],
    fields: Sequence[str],
) -> None:
    include_id = not any(_CAPABILITY_FIELD_ALIASES[field] == "id" for field in fields)
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            continue
        values = []
        if include_id:
            values.append(_capability_field_value(capability, "id"))
        values.extend(_capability_field_value(capability, field) for field in fields)
        print("\t".join(str(value) for value in values))


def _capability_format_values(capability: Mapping[str, object]) -> dict[str, object]:
    return {
        alias: _capability_field_value(capability, alias)
        for alias in _CAPABILITY_FIELD_ALIASES
    }


def _format_capability(
    capability: Mapping[str, object],
    template: str,
) -> str:
    return template.format_map(_capability_format_values(capability))


def _add_output_renderer_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--json",
        dest="output",
        action="store_const",
        const="json",
        default="json",
        help="print structured JSON (default)",
    )
    group.add_argument(
        "--yaml",
        dest="output",
        action="store_const",
        const="yaml",
        help="print structured YAML",
    )
    group.add_argument(
        "--plain",
        dest="output",
        action="store_const",
        const="plain",
        help="print compact text",
    )


def _capabilities_with_plugin_versions(
    capabilities: Sequence[object],
    version_info: object,
) -> list[object]:
    if not isinstance(version_info, Mapping):
        return list(capabilities)
    raw_plugins = version_info.get("plugins", [])
    if not isinstance(raw_plugins, list):
        return list(capabilities)
    plugin_versions: dict[str, str] = {}
    for plugin in raw_plugins:
        if not isinstance(plugin, Mapping):
            continue
        name = plugin.get("name")
        version = plugin.get("version")
        if isinstance(name, str) and isinstance(version, str):
            plugin_versions[name] = version

    enriched: list[object] = []
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            enriched.append(capability)
            continue
        capability_id = capability.get("id")
        if not isinstance(capability_id, str):
            enriched.append(capability)
            continue
        version = plugin_versions.get(capability_id)
        if version is None or capability.get("version"):
            enriched.append(capability)
            continue
        enriched_capability = dict(capability)
        enriched_capability["version"] = version
        enriched.append(enriched_capability)
    return enriched


def _mapping_or_attr(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _tool_result_error_message(result: object) -> str | None:
    is_error = _mapping_or_attr(result, "isError", False)
    if is_error is not True:
        return None

    content = _mapping_or_attr(result, "content", [])
    if isinstance(content, Sequence) and not isinstance(content, str):
        for item in content:
            text = _mapping_or_attr(item, "text")
            if isinstance(text, str) and text:
                match = re.fullmatch(r"Error executing tool [^:]+: (.*)", text)
                return match.group(1) if match else text
    return "tool call failed"


def _tool_result_payload(result: object) -> object:
    error_message = _tool_result_error_message(result)
    if error_message is not None:
        raise ToolCallError(error_message)

    if isinstance(result, Mapping) and "structuredContent" in result:
        structured_content = result["structuredContent"]
        if structured_content is not None:
            return structured_content

    structured_content = getattr(result, "structuredContent", None)
    if structured_content is not None:
        return structured_content

    return result


def _split_account_selector(
    capability: str,
    account: str | None,
) -> tuple[str, str | None]:
    if account is not None or ":" not in capability:
        return capability, account
    split_capability, split_account = capability.split(":", 1)
    if not split_capability or not split_account:
        return capability, account
    return split_capability, split_account


def _contains_exception(exc: BaseException, exc_type: type[BaseException]) -> bool:
    if isinstance(exc, exc_type):
        return True
    nested = getattr(exc, "exceptions", None)
    if isinstance(nested, tuple):
        return any(
            _contains_exception(nested_exc, exc_type)
            for nested_exc in nested
            if isinstance(nested_exc, BaseException)
        )
    return False


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON arguments must be an object")
    return parsed


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _is_textual_artifact_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type.startswith("text/"):
        return True
    if media_type in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/javascript",
    }:
        return True
    return media_type.endswith("+json") or media_type.endswith("+xml")


def _content_length(response: httpx.Response) -> int:
    raw_content_length = response.headers.get("content-length")
    if raw_content_length is None:
        raise ValueError("refusing to write artifact with unknown size to stdout")
    try:
        content_length = int(raw_content_length)
    except ValueError as exc:
        raise ValueError(
            "refusing to write artifact with invalid size to stdout"
        ) from exc
    if content_length < 0:
        raise ValueError("refusing to write artifact with invalid size to stdout")
    return content_length


def _fetch_artifact_stdout_bytes(artifact_url: str, max_bytes: int) -> bytes:
    with httpx.Client(timeout=30.0) as http_client:
        head_response = http_client.head(artifact_url)
        if not 200 <= head_response.status_code < 300:
            raise ValueError(
                f"artifact metadata request failed: HTTP {head_response.status_code}"
            )
        content_type = head_response.headers.get("content-type", "")
        if not _is_textual_artifact_content_type(content_type):
            raise ValueError(
                f"refusing to write non-text artifact to stdout: {content_type}"
            )
        content_length = _content_length(head_response)
        if content_length > max_bytes:
            raise ValueError(
                f"refusing to write {content_length} byte artifact to stdout; "
                f"limit is {max_bytes} bytes"
            )

        with http_client.stream("GET", artifact_url) as get_response:
            if not 200 <= get_response.status_code < 300:
                raise ValueError(
                    f"artifact fetch failed: HTTP {get_response.status_code}"
                )
            get_content_type = get_response.headers.get("content-type", "")
            if not _is_textual_artifact_content_type(get_content_type):
                raise ValueError(
                    "refusing to write non-text artifact to stdout: "
                    f"{get_content_type}"
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in get_response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"refusing to write artifact larger than {max_bytes} "
                        "bytes to stdout"
                    )
                chunks.append(chunk)
    return b"".join(chunks)


def _run_artifact_get(namespace: argparse.Namespace) -> int:
    if not namespace.stdout:
        print_cli_error("artifact get requires --stdout", area="usage")
        return 2
    try:
        data = _fetch_artifact_stdout_bytes(namespace.url, namespace.max_bytes)
    except (OSError, httpx.HTTPError, ValueError) as exc:
        print_cli_error(str(exc), area="artifact")
        return 1
    _write_stdout_bytes(data)
    return 0


def _run_artifact_save(namespace: argparse.Namespace) -> int:
    try:
        _save_artifact_to_file(namespace.url, namespace.output)
    except (OSError, httpx.HTTPError, ValueError) as exc:
        print_cli_error(str(exc), area="artifact")
        return 1
    return 0


def _save_artifact_to_file(artifact_url: str, output_path: Path) -> None:
    with httpx.Client(timeout=30.0) as http_client:
        with http_client.stream("GET", artifact_url) as get_response:
            if not 200 <= get_response.status_code < 300:
                raise ValueError(
                    f"artifact fetch failed: HTTP {get_response.status_code}"
                )
            fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    for chunk in get_response.iter_bytes():
                        output.write(chunk)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                output_path.unlink(missing_ok=True)
                raise


def _run_artifact_with_temp(namespace: argparse.Namespace) -> int:
    try:
        max_child_stdout_bytes, command = _artifact_command_options(
            namespace.child_argv,
            namespace.max_child_stdout_bytes,
        )
    except ValueError as exc:
        print_cli_error(str(exc), area="usage")
        return 2
    if not _artifact_command_has_separator(namespace):
        command = None
    if command is None:
        print_cli_error(
            "expected: arbiter-py artifact with-temp <url> "
            "[--max-child-stdout-bytes N] -- <argv...>",
            area="usage",
        )
        return 2
    if not any("{}" in arg for arg in command):
        print_cli_error(
            "artifact with-temp command must contain a {} path placeholder",
            area="usage",
        )
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="arbiter-artifact-") as temp_dir:
            with httpx.Client(timeout=30.0) as http_client:
                with http_client.stream("GET", namespace.url) as get_response:
                    if not 200 <= get_response.status_code < 300:
                        raise ValueError(
                            f"artifact fetch failed: HTTP {get_response.status_code}"
                        )
                    artifact_path = Path(temp_dir) / _artifact_temp_filename(
                        get_response.headers
                    )
                    fd = os.open(
                        artifact_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    with os.fdopen(fd, "wb") as output:
                        for chunk in get_response.iter_bytes():
                            output.write(chunk)
            replaced_command = [
                arg.replace("{}", str(artifact_path)) for arg in command
            ]
            _run_artifact_command(
                replaced_command,
                stdin=None,
                max_child_stdout_bytes=max_child_stdout_bytes,
            )
    except (OSError, httpx.HTTPError, ValueError) as exc:
        print_cli_error(str(exc), area="artifact")
        return 1
    return 0


def _run_artifact_with_stdin(namespace: argparse.Namespace) -> int:
    try:
        max_child_stdout_bytes, command = _artifact_command_options(
            namespace.child_argv,
            namespace.max_child_stdout_bytes,
        )
    except ValueError as exc:
        print_cli_error(str(exc), area="usage")
        return 2
    if not _artifact_command_has_separator(namespace):
        command = None
    if command is None:
        print_cli_error(
            "expected: arbiter-py artifact with-stdin <url> "
            "[--max-child-stdout-bytes N] -- <argv...>",
            area="usage",
        )
        return 2
    try:
        with httpx.Client(timeout=30.0) as http_client:
            with http_client.stream("GET", namespace.url) as get_response:
                if not 200 <= get_response.status_code < 300:
                    raise ValueError(
                        f"artifact fetch failed: HTTP {get_response.status_code}"
                    )
                _run_artifact_command(
                    command,
                    stdin=get_response.iter_bytes(),
                    max_child_stdout_bytes=max_child_stdout_bytes,
                )
    except (OSError, httpx.HTTPError, ValueError) as exc:
        print_cli_error(str(exc), area="artifact")
        return 1
    return 0


def _artifact_command_options(
    command: Sequence[str],
    max_child_stdout_bytes: int,
) -> tuple[int, list[str] | None]:
    normalized = list(command)
    if normalized and normalized[0] == "--max-child-stdout-bytes":
        if len(normalized) < 2 or normalized[1] == "--":
            raise ValueError("--max-child-stdout-bytes requires a value")
        try:
            max_child_stdout_bytes = int(normalized[1])
        except ValueError as exc:
            raise ValueError(
                "--max-child-stdout-bytes must be a positive integer"
            ) from exc
        if max_child_stdout_bytes < 1:
            raise ValueError("--max-child-stdout-bytes must be a positive integer")
        normalized = normalized[2:]
    if normalized and normalized[0] == "--":
        normalized = normalized[1:]
    if not normalized or not normalized[0].strip():
        return max_child_stdout_bytes, None
    return max_child_stdout_bytes, normalized


def _artifact_command_has_separator(namespace: argparse.Namespace) -> bool:
    raw_args = getattr(namespace, "_raw_args", ())
    if not isinstance(raw_args, Sequence):
        return False
    try:
        command_index = list(raw_args).index(namespace.artifact_command)
    except ValueError:
        return False
    return "--" in raw_args[command_index + 1 :]


def _artifact_temp_filename(headers: httpx.Headers) -> str:
    filename = ""
    content_disposition = headers.get("content-disposition", "")
    if content_disposition:
        message = Message()
        message["content-disposition"] = content_disposition
        filename = message.get_filename() or ""
    filename = _sanitize_artifact_filename(filename)
    if not filename:
        filename = "artifact"
    if not Path(filename).suffix:
        extension = _artifact_extension_for_content_type(
            headers.get("content-type", "")
        )
        if extension:
            filename += extension
    return filename


def _sanitize_artifact_filename(filename: str) -> str:
    filename = filename.strip()
    if not filename:
        return ""
    filename = Path(filename.replace("\\", "/")).name
    sanitized = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "_"
        for char in filename
    ).strip(".")
    return sanitized


def _artifact_extension_for_content_type(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in _ARTIFACT_CONTENT_TYPE_EXTENSIONS:
        return _ARTIFACT_CONTENT_TYPE_EXTENSIONS[media_type]
    return mimetypes.guess_extension(media_type) or ""


def _run_artifact_command(
    command: Sequence[str],
    *,
    stdin: Iterable[bytes] | None,
    max_child_stdout_bytes: int,
) -> None:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    captured: dict[str, bytes] = {}
    errors: list[ValueError] = []
    stop_event = threading.Event()

    stdout_reader = threading.Thread(
        target=_read_capped_child_pipe,
        args=(
            process,
            process.stdout,
            "child stdout",
            max_child_stdout_bytes,
            captured,
            errors,
            stop_event,
        ),
    )
    stderr_reader = threading.Thread(
        target=_read_capped_child_pipe,
        args=(
            process,
            process.stderr,
            "child stderr",
            max_child_stdout_bytes,
            captured,
            errors,
            stop_event,
        ),
    )
    stdout_reader.start()
    stderr_reader.start()
    stdin_writer: threading.Thread | None = None
    try:
        if stdin is not None:
            assert process.stdin is not None
            stdin_writer = threading.Thread(
                target=_write_child_stdin,
                args=(process.stdin, stdin, stop_event),
            )
            stdin_writer.start()
        returncode = process.wait()
    except Exception:
        process.kill()
        process.wait()
        raise
    finally:
        if stdin_writer is not None:
            stdin_writer.join()
        stdout_reader.join()
        stderr_reader.join()

    if errors:
        raise errors[0]
    stderr_data = _validated_text_output(
        captured.get("child stderr", b""), "child stderr"
    )
    if stderr_data:
        _write_stderr_bytes(stderr_data)
    stdout_data = _validated_text_output(
        captured.get("child stdout", b""), "child stdout"
    )
    if stdout_data:
        _write_stdout_bytes(stdout_data)
    if returncode != 0:
        raise ValueError(f"command failed with exit code {returncode}")


def _read_capped_child_pipe(
    process: subprocess.Popen[bytes],
    pipe: Any,
    label: str,
    max_bytes: int,
    captured: dict[str, bytes],
    errors: list[ValueError],
    stop_event: threading.Event,
) -> None:
    data = bytearray()
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            if len(data) + len(chunk) > max_bytes:
                errors.append(
                    ValueError(
                        f"refusing to write {label} larger than {max_bytes} bytes"
                    )
                )
                stop_event.set()
                process.kill()
                break
            data.extend(chunk)
    finally:
        pipe.close()
        captured[label] = bytes(data)


def _write_child_stdin(
    stdin_pipe: Any,
    stdin: Iterable[bytes],
    stop_event: threading.Event,
) -> None:
    try:
        for chunk in stdin:
            if stop_event.is_set():
                break
            stdin_pipe.write(chunk)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            stdin_pipe.close()
        except OSError:
            pass


def _validated_text_output(data: bytes, label: str) -> bytes:
    if b"\x00" in data:
        raise ValueError(f"refusing to write non-text {label}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"refusing to write non-text {label}") from exc
    return data


def _write_stdout_bytes(data: bytes) -> None:
    stdout_buffer = getattr(sys.stdout, "buffer", None)
    if stdout_buffer is not None:
        stdout_buffer.write(data)
        stdout_buffer.flush()
    else:
        sys.stdout.write(data.decode("utf-8"))
        sys.stdout.flush()


def _write_stderr_bytes(data: bytes) -> None:
    stderr_buffer = getattr(sys.stderr, "buffer", None)
    if stderr_buffer is not None:
        stderr_buffer.write(data)
        stderr_buffer.flush()
    else:
        sys.stderr.write(data.decode("utf-8"))
        sys.stderr.flush()


def _client_config_path(config_dir: str, config_name: str) -> Path:
    return Path(config_dir).expanduser() / f"{config_name}.yaml"


def _load_client_config(path: Path, *, explicit: bool) -> ClientConfig:
    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"client config not found: {path}")
        return ClientConfig()

    loaded = OmegaConf.load(path)
    if not isinstance(loaded, DictConfig):
        raise ValueError(f"client config must be a mapping: {path}")

    allowed_keys = {"arbiter"}
    loaded_keys = {str(key) for key in loaded.keys()}
    unknown_keys = sorted(loaded_keys - allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"unsupported client config key(s) in {path}: {', '.join(unknown_keys)}"
        )

    arbiter_config = OmegaConf.select(loaded, "arbiter", default=None)
    if arbiter_config is None:
        arbiter_keys: set[str] = set()
    elif isinstance(arbiter_config, DictConfig):
        arbiter_keys = {str(key) for key in arbiter_config.keys()}
    else:
        raise ValueError(f"client config arbiter must be a mapping: {path}")
    unknown_arbiter_keys = sorted(arbiter_keys - {"mcp_url"})
    if unknown_arbiter_keys:
        raise ValueError(
            "unsupported client config arbiter key(s) in "
            f"{path}: {', '.join(unknown_arbiter_keys)}"
        )

    mcp_url = OmegaConf.select(loaded, "arbiter.mcp_url", default=None)
    if mcp_url is not None and not isinstance(mcp_url, str):
        raise ValueError(f"client config arbiter.mcp_url must be a string: {path}")
    return ClientConfig(mcp_url=mcp_url)


def _override_client_config(overrides: Sequence[str]) -> ClientConfig:
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"client override must use KEY=VALUE syntax: {override}")
    try:
        loaded = OmegaConf.from_dotlist(list(overrides))
    except OmegaConfBaseException as exc:
        raise ValueError(f"invalid client override: {exc}") from exc
    if not isinstance(loaded, DictConfig):
        raise ValueError("client overrides must compose to a mapping")

    allowed_keys = {"arbiter"}
    loaded_keys = {str(key) for key in loaded.keys()}
    unknown_keys = sorted(loaded_keys - allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"unsupported client override key(s): {', '.join(unknown_keys)}"
        )

    arbiter_config = OmegaConf.select(loaded, "arbiter", default=None)
    if arbiter_config is None:
        arbiter_keys: set[str] = set()
    elif isinstance(arbiter_config, DictConfig):
        arbiter_keys = {str(key) for key in arbiter_config.keys()}
    else:
        raise ValueError("client override arbiter must be a mapping")
    unknown_arbiter_keys = sorted(arbiter_keys - {"mcp_url"})
    if unknown_arbiter_keys:
        raise ValueError(
            "unsupported client override arbiter key(s): "
            f"{', '.join(unknown_arbiter_keys)}"
        )

    mcp_url = OmegaConf.select(loaded, "arbiter.mcp_url", default=None)
    if mcp_url is not None and not isinstance(mcp_url, str):
        raise ValueError("client override arbiter.mcp_url must be a string")
    return ClientConfig(mcp_url=mcp_url)


def _resolve_mcp_url(namespace: argparse.Namespace) -> ResolvedMCPURL:
    config_path = _client_config_path(namespace.config_dir, namespace.config_name)
    config = _load_client_config(config_path, explicit=False)
    override_config = _override_client_config(namespace.overrides)
    if override_config.mcp_url is not None:
        return ResolvedMCPURL(
            url=override_config.mcp_url,
            source="client override arbiter.mcp_url",
        )

    env_mcp_url = os.environ.get(MCP_URL_ENV_VAR)
    if env_mcp_url is not None:
        return ResolvedMCPURL(
            url=env_mcp_url,
            source=f"environment variable {MCP_URL_ENV_VAR}",
        )

    if config.mcp_url is not None:
        return ResolvedMCPURL(
            url=config.mcp_url,
            source=f"client config {config_path}",
        )

    return ResolvedMCPURL(
        url=DEFAULT_MCP_URL,
        source=f"built-in default; no client config found at {config_path}",
    )


def _connection_error_message(namespace: argparse.Namespace) -> str:
    return (
        f"could not connect to Arbiter at {namespace.mcp_url} "
        f"({namespace.mcp_url_source}). Is arbiter-server serve running?"
    )


def _apply_resolved_mcp_url(
    namespace: argparse.Namespace,
    resolved_mcp_url: ResolvedMCPURL,
) -> None:
    namespace.mcp_url = resolved_mcp_url.url
    namespace.mcp_url_source = resolved_mcp_url.source


def _client_config_yaml(config: ClientConfig) -> str:
    return OmegaConf.to_yaml(
        OmegaConf.create({"arbiter": {"mcp_url": config.mcp_url or DEFAULT_MCP_URL}})
    )


def _run_bootstrap_client(namespace: argparse.Namespace) -> int:
    if not BOOTSTRAP_NAME_PATTERN.fullmatch(namespace.config_name):
        print_cli_error(
            "config name must contain only letters, numbers, underscores, and "
            "dashes.",
            area="client config",
        )
        return 2
    try:
        override_config = _override_client_config(namespace.overrides)
    except ValueError as exc:
        print_cli_error(str(exc), area="client config")
        return 1

    config_path = _client_config_path(namespace.config_dir, namespace.config_name)
    if config_path.exists() and not namespace.force:
        print_cli_error(
            f"refusing to overwrite existing file: {config_path}",
            area="client config",
        )
        return 1

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_client_config_yaml(override_config), encoding="utf-8")
    print(f"wrote {config_path}")
    return 0


def _warn_if_remote_version_mismatch(initialize_result: object) -> None:
    server_info = getattr(initialize_result, "serverInfo", None)
    remote_version = getattr(server_info, "version", None)
    local_version = arbiter_python_client_version()
    if (
        not isinstance(remote_version, str)
        or remote_version == "unknown"
        or local_version == "unknown"
        or remote_version == local_version
    ):
        return
    print(
        "Arbiter server version warning: "
        f"local Python client version {local_version} does not match "
        f"remote server server version {remote_version}.",
        file=sys.stderr,
    )


def _warn_if_staged_deployment(version_info: object, url: str) -> None:
    global _STAGED_DEPLOYMENT_WARNING_EMITTED

    if _STAGED_DEPLOYMENT_WARNING_EMITTED or not isinstance(version_info, Mapping):
        return
    deployment_scope = version_info.get("deployment_scope")
    if deployment_scope != "staged":
        return
    print(
        f"Heads up: connected to staged Arbiter at {url}.",
        file=sys.stderr,
    )
    _STAGED_DEPLOYMENT_WARNING_EMITTED = True


async def _warn_if_staged_server(session: ClientSession, url: str) -> None:
    try:
        result = await session.call_tool("version_info", {})
        payload = _tool_result_payload(result)
    except Exception:
        return
    _warn_if_staged_deployment(payload, url)


async def list_tools(url: str) -> list[Mapping[str, object]]:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            _warn_if_remote_version_mismatch(initialize_result)
            await _warn_if_staged_server(session, url)
            result = await session.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        }
        for tool in result.tools
    ]


async def call_tool(
    url: str,
    name: str,
    arguments: Mapping[str, Any],
) -> object:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            _warn_if_remote_version_mismatch(initialize_result)
            if name == "version_info":
                result = await session.call_tool(name, dict(arguments))
                _warn_if_staged_deployment(_tool_result_payload(result), url)
                return result
            await _warn_if_staged_server(session, url)
            return await session.call_tool(name, dict(arguments))


async def call_arbiter_operation(
    url: str,
    operation_id: str,
    arguments: Mapping[str, Any],
) -> object:
    return await call_tool(
        url,
        "run_op",
        {
            "id": operation_id,
            "arguments": dict(arguments),
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter-py",
        description="Client CLI for an Arbiter MCP server.",
        epilog=(
            f"Uses {DEFAULT_CONFIG_DIR}/{DEFAULT_CLIENT_CONFIG_NAME}.yaml by "
            "default. "
            "Override client config values with Hydra-style KEY=VALUE "
            "arguments after the command, for example: "
            "arbiter-py cap arbiter.mcp_url=http://127.0.0.1:8000/mcp"
        ),
    )
    parser.add_argument(
        "--config-dir",
        default=DEFAULT_CONFIG_DIR,
        help=f"client config directory (default: {DEFAULT_CONFIG_DIR})",
    )
    parser.add_argument(
        "--config-name",
        default=DEFAULT_CLIENT_CONFIG_NAME,
        help="client config file name without .yaml",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {arbiter_python_client_version()}",
    )
    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    bootstrap = subcommands.add_parser("bootstrap", help="create config templates")
    bootstrap_subcommands = bootstrap.add_subparsers(
        dest="bootstrap_command",
        required=True,
    )
    bootstrap_client = bootstrap_subcommands.add_parser(
        "client",
        help="create the Arbiter client config",
    )
    bootstrap_client.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config file",
    )

    info = subcommands.add_parser(
        "info",
        help="discover Arbiter server identity, plugins, accounts, and operations",
    )
    info.add_argument(
        "--yaml",
        action="store_true",
        help="print YAML instead of the default JSON",
    )
    info.add_argument(
        "--short",
        action="store_true",
        help="print only plugin account identifiers and descriptions",
    )
    info_subcommands = info.add_subparsers(dest="info_command")

    info_subcommands.add_parser(
        "plugins",
        help="list installed plugins",
    )
    info_plugin = info_subcommands.add_parser(
        "plugin",
        help="describe one plugin",
    )
    info_plugin.add_argument("plugin", help="plugin name, such as smtp")

    info_accounts = info_subcommands.add_parser(
        "accounts",
        help="list accounts for one plugin",
    )
    info_accounts.add_argument("plugin", help="plugin name, such as smtp")

    info_account = info_subcommands.add_parser(
        "account",
        help="show one account and its policy summary",
    )
    info_account.add_argument("plugin", help="plugin name, such as smtp")
    info_account.add_argument("account", help="account name")

    info_subcommands.add_parser(
        "tests",
        help="run read-only account tests for all plugins",
    )
    info_test = info_subcommands.add_parser(
        "test",
        help="run read-only account tests for one plugin or account",
    )
    info_test.add_argument("plugin", help="plugin name, such as smtp")
    info_test.add_argument("account", nargs="?", help="optional account name")

    info_ops = info_subcommands.add_parser(
        "ops",
        help="list operations for one plugin",
    )
    info_ops.add_argument("plugin", help="plugin name, such as smtp")

    info_op = info_subcommands.add_parser(
        "op",
        help="show one operation schema",
    )
    info_op.add_argument("plugin", help="plugin name, such as smtp")
    info_op.add_argument("operation", help="operation name, such as send_email")

    mcp = subcommands.add_parser("mcp", help="inspect and call raw MCP tools")
    mcp_subcommands = mcp.add_subparsers(dest="mcp_command")

    mcp_tools = mcp_subcommands.add_parser("tools", help="list available MCP tools")
    mcp_tools.add_argument(
        "--json",
        action="store_true",
        help="print the full tool metadata as JSON",
    )

    mcp_call = mcp_subcommands.add_parser("call", help="call an MCP tool")
    mcp_call.add_argument("name", help="tool name")
    mcp_call.add_argument(
        "--args",
        default={},
        type=_parse_json_object,
        help='tool arguments as a JSON object, for example \'{"account": "primary"}\'',
    )

    capabilities = subcommands.add_parser(
        "cap",
        help="discover Arbiter capabilities (alias: capabilities)",
    )
    capabilities_subcommands = capabilities.add_subparsers(
        dest="capabilities_command",
    )
    capabilities_list = capabilities_subcommands.add_parser(
        "list",
        help="list capability names",
    )
    capabilities_list.add_argument(
        "--json",
        action="store_true",
        help="print capability names as JSON",
    )
    capabilities_list.add_argument(
        "query",
        nargs="*",
        help=(
            "optional query such as fields=desc,version,num_accts or "
            'format="{id}=={version}: {desc}"'
        ),
    )
    capabilities_describe = capabilities_subcommands.add_parser(
        "desc",
        help="describe all capabilities or one capability (alias: describe)",
    )
    capabilities_describe.add_argument(
        "capability",
        nargs="?",
        help="capability name to describe; omit for bounded summaries of all",
    )

    operation = subcommands.add_parser(
        "op",
        help="inspect or run Arbiter operations (alias: operation)",
    )
    operation_subcommands = operation.add_subparsers(
        dest="operation_command",
        required=True,
    )
    operation_list = operation_subcommands.add_parser(
        "list",
        help="list plugins or operation summaries for one plugin",
    )
    operation_list.add_argument(
        "plugin",
        nargs="?",
        help="plugin to list operations for; omit to list plugins",
    )
    _add_output_renderer_arguments(operation_list)
    operation_describe = operation_subcommands.add_parser(
        "desc",
        help="describe one plugin or operation (alias: describe)",
    )
    operation_describe.add_argument(
        "id",
        help="plugin id such as smtp, or operation id such as smtp:send_email",
    )
    _add_output_renderer_arguments(operation_describe)
    operation_run = operation_subcommands.add_parser(
        "run",
        help="run one operation",
    )
    operation_run.add_argument("id", help="operation id, such as smtp:send_email")
    operation_run.add_argument(
        "--args",
        default={},
        type=_parse_json_object,
        help='operation arguments as a JSON object, for example \'{"account": "bot"}\'',
    )

    artifact = subcommands.add_parser(
        "artifact",
        help="fetch explicit Arbiter artifacts",
    )
    artifact_subcommands = artifact.add_subparsers(
        dest="artifact_command",
        required=True,
    )
    artifact_get = artifact_subcommands.add_parser(
        "get",
        help="fetch an explicit artifact",
    )
    artifact_get.add_argument("url", help="one-time artifact URL")
    artifact_get.add_argument(
        "--stdout",
        action="store_true",
        help="write the artifact bytes to stdout",
    )
    artifact_get.add_argument(
        "--max-bytes",
        default=DEFAULT_ARTIFACT_MAX_BYTES,
        type=_parse_positive_int,
        help=f"maximum bytes to write to stdout (default: {DEFAULT_ARTIFACT_MAX_BYTES})",
    )
    artifact_save = artifact_subcommands.add_parser(
        "save",
        description=(
            "Save one artifact URL to a local file. Use only when the user "
            "explicitly requests saving the artifact to a file. This command "
            "never writes artifact bytes to stdout."
        ),
        help="save an artifact to a file only on explicit user request",
    )
    artifact_save.add_argument("url", help="one-time artifact URL")
    artifact_save.add_argument(
        "output",
        type=Path,
        help=(
            "file path; use only when the user explicitly requests saving the "
            "artifact"
        ),
    )
    artifact_with_temp = artifact_subcommands.add_parser(
        "with-temp",
        help="run a command with the artifact as a temporary file",
    )
    artifact_with_temp.add_argument("url", help="one-time artifact URL")
    artifact_with_temp.add_argument(
        "--max-child-stdout-bytes",
        default=DEFAULT_ARTIFACT_COMMAND_MAX_CHILD_STDOUT_BYTES,
        type=_parse_positive_int,
        help=(
            "maximum child stdout bytes to write "
            f"(default: {DEFAULT_ARTIFACT_COMMAND_MAX_CHILD_STDOUT_BYTES})"
        ),
    )
    artifact_with_temp.add_argument(
        "child_argv",
        nargs=argparse.REMAINDER,
        help="command argv after --; use {} where the temp path should go",
    )
    artifact_with_stdin = artifact_subcommands.add_parser(
        "with-stdin",
        help="run a command with the artifact bytes on stdin",
    )
    artifact_with_stdin.add_argument("url", help="one-time artifact URL")
    artifact_with_stdin.add_argument(
        "--max-child-stdout-bytes",
        default=DEFAULT_ARTIFACT_COMMAND_MAX_CHILD_STDOUT_BYTES,
        type=_parse_positive_int,
        help=(
            "maximum child stdout bytes to write "
            f"(default: {DEFAULT_ARTIFACT_COMMAND_MAX_CHILD_STDOUT_BYTES})"
        ),
    )
    artifact_with_stdin.add_argument(
        "child_argv",
        nargs=argparse.REMAINDER,
        help="command argv after --",
    )

    accounts = subcommands.add_parser("accounts", help="inspect configured accounts")
    accounts_subcommands = accounts.add_subparsers(
        dest="accounts_command",
    )
    accounts_list = accounts_subcommands.add_parser(
        "list", help="list configured accounts"
    )
    accounts_list.add_argument(
        "--json",
        action="store_true",
        help="print account names as JSON",
    )
    accounts_desc = accounts_subcommands.add_parser(
        "desc",
        help="describe accounts for a capability (alias: describe)",
    )
    accounts_desc.add_argument("capability", help="capability name")
    accounts_desc.add_argument("account", nargs="?", help="account name")

    return parser


def _extract_global_config_args(args: Sequence[str]) -> list[str]:
    extracted: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            remaining.extend(args[index:])
            break
        if arg in {"--config-dir", "--config-name"}:
            if index + 1 < len(args):
                extracted.extend([arg, args[index + 1]])
                index += 2
                continue
            remaining.append(arg)
            index += 1
            continue
        if arg.startswith("--config-dir=") or arg.startswith("--config-name="):
            extracted.append(arg)
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return [*extracted, *remaining]


def _extract_client_overrides(args: Sequence[str]) -> tuple[list[str], list[str]]:
    overrides: list[str] = []
    remaining: list[str] = []
    skip_next = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            remaining.extend(args[index:])
            break
        if skip_next:
            remaining.append(arg)
            skip_next = False
            index += 1
            continue
        if arg == "--args":
            remaining.append(arg)
            skip_next = True
            index += 1
            continue
        if arg.startswith("arbiter.mcp_url="):
            overrides.append(arg)
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return remaining, overrides


def _normalize_command_aliases(args: Sequence[str]) -> list[str]:
    normalized = list(args)
    index = 0
    while index < len(normalized):
        arg = normalized[index]
        if arg in {"--config-dir", "--config-name"}:
            index += 2
            continue
        if arg.startswith("--config-dir=") or arg.startswith("--config-name="):
            index += 1
            continue
        break

    if index >= len(normalized):
        return normalized

    command_aliases = {
        "capabilities": "cap",
        "operation": "op",
    }
    normalized[index] = command_aliases.get(normalized[index], normalized[index])

    if index + 1 >= len(normalized):
        if normalized[index] == "mcp":
            normalized.append("tools")
        elif normalized[index] in {"cap", "accounts"}:
            normalized.append("list")
        return normalized

    next_arg = normalized[index + 1]
    if next_arg in {"-h", "--help"}:
        return normalized
    if normalized[index] in {"cap", "accounts"} and (
        "=" in next_arg or next_arg.startswith("-")
    ):
        normalized.insert(index + 1, "list")
    if normalized[index] == "mcp" and ("=" in next_arg or next_arg.startswith("-")):
        normalized.insert(index + 1, "tools")

    if (
        normalized[index] in {"cap", "op", "accounts"}
        and normalized[index + 1] == "describe"
    ):
        normalized[index + 1] = "desc"

    return normalized


def _normalize_info_output_flags(args: Sequence[str]) -> list[str]:
    normalized = list(args)
    index = 0
    while index < len(normalized):
        arg = normalized[index]
        if arg in {"--config-dir", "--config-name"}:
            index += 2
            continue
        if arg.startswith("--config-dir=") or arg.startswith("--config-name="):
            index += 1
            continue
        break

    if index >= len(normalized) or normalized[index] != "info":
        return normalized

    output_flags = [
        arg for arg in normalized[index + 1 :] if arg in {"--yaml", "--short"}
    ]
    if not output_flags:
        return normalized

    without_output_flags = [
        arg
        for arg_index, arg in enumerate(normalized)
        if arg_index <= index or arg not in {"--yaml", "--short"}
    ]
    return [
        *without_output_flags[: index + 1],
        *output_flags,
        *without_output_flags[index + 1 :],
    ]


def _apply_capability_query(namespace: argparse.Namespace) -> None:
    namespace.capability_query = CapabilityQuery()
    if namespace.command not in {"capabilities", "cap"}:
        return
    if namespace.capabilities_command not in {None, "list"}:
        return
    namespace.capability_query = _parse_capability_query(
        getattr(namespace, "query", []),
    )


def _print_short_usage() -> None:
    print("usage: arbiter-py {info,op,mcp} ...")
    print("Run 'arbiter-py --help' for full help.")


def _info_arguments(namespace: argparse.Namespace) -> dict[str, str]:
    command = namespace.info_command
    if command is None:
        return {"kind": "overview"}
    if command == "plugins":
        return {"kind": "plugins"}
    if command == "plugin":
        return {"kind": "plugin", "plugin": namespace.plugin}
    if command == "accounts":
        return {"kind": "accounts", "plugin": namespace.plugin}
    if command == "account":
        return {
            "kind": "account",
            "plugin": namespace.plugin,
            "account": namespace.account,
        }
    if command == "tests":
        return {"kind": "tests"}
    if command == "test":
        arguments = {"kind": "test", "plugin": namespace.plugin}
        if namespace.account is not None:
            arguments["account"] = namespace.account
        return arguments
    if command == "ops":
        return {"kind": "ops", "plugin": namespace.plugin}
    if command == "op":
        return {
            "kind": "op",
            "plugin": namespace.plugin,
            "operation": namespace.operation,
        }
    raise RuntimeError(f"unhandled info command: {command}")


def _with_server_url(payload: object, url: str) -> object:
    if not isinstance(payload, Mapping):
        return payload
    return {"server_url": url, **payload}


def _short_info_payload(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return payload
    result: dict[str, object] = {"kind": "overview_short"}
    server_url = payload.get("server_url")
    if isinstance(server_url, str):
        result["server_url"] = server_url
    result["accounts"] = _short_info_accounts(payload.get("plugins"))
    return result


def _short_info_accounts(plugins: object) -> list[dict[str, str]]:
    if not isinstance(plugins, list):
        return []
    accounts: list[dict[str, str]] = []
    for plugin in plugins:
        if not isinstance(plugin, Mapping):
            continue
        plugin_id = plugin.get("id")
        if not isinstance(plugin_id, str) or not plugin_id:
            continue
        plugin_accounts = plugin.get("accounts")
        if not isinstance(plugin_accounts, list):
            continue
        for account in plugin_accounts:
            if not isinstance(account, Mapping):
                continue
            name = account.get("name")
            if not isinstance(name, str) or not name:
                continue
            entry = {"id": f"{plugin_id}:{name}"}
            description = account.get("description")
            if isinstance(description, str) and description:
                entry["description"] = description
            accounts.append(entry)
    return accounts


def _plugin_ids_from_info_payload(payload: object) -> list[str]:
    if not isinstance(payload, Mapping):
        raise ToolCallError("unexpected info plugins payload")
    raw_plugins = payload.get("plugins")
    if not isinstance(raw_plugins, list):
        raise ToolCallError("unexpected info plugins payload")
    plugins: list[str] = []
    for raw_plugin in raw_plugins:
        if not isinstance(raw_plugin, Mapping):
            raise ToolCallError("unexpected info plugins payload")
        plugin_id = raw_plugin.get("id")
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ToolCallError("unexpected info plugins payload")
        plugins.append(plugin_id)
    return sorted(plugins)


def _operation_ids_from_info_payload(payload: object) -> list[str]:
    _, operation_ids = _operations_by_id_from_info_payload(payload)
    return operation_ids


def _operations_by_id_from_info_payload(
    payload: object,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    if not isinstance(payload, Mapping):
        raise ToolCallError("unexpected info ops payload")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list):
        raise ToolCallError("unexpected info ops payload")
    operations_by_id: dict[str, dict[str, object]] = {}
    operation_ids: list[str] = []
    for raw_operation in raw_operations:
        if not isinstance(raw_operation, Mapping):
            raise ToolCallError("unexpected info ops payload")
        operation_id = raw_operation.get("id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ToolCallError("unexpected info ops payload")
        operations_by_id[operation_id] = {
            str(key): value for key, value in raw_operation.items() if key != "id"
        }
        operation_ids.append(operation_id)
    operation_ids = sorted(operation_ids)
    return {operation_id: operations_by_id[operation_id] for operation_id in operation_ids}, operation_ids


def _operation_list_structured_payload(payload: object) -> tuple[object, list[str]]:
    if not isinstance(payload, Mapping):
        raise ToolCallError("unexpected info ops payload")
    operations_by_id, operation_ids = _operations_by_id_from_info_payload(payload)
    structured: dict[str, object] = {}
    if "kind" in payload:
        structured["kind"] = payload["kind"]
    structured["operations"] = operations_by_id
    if "plugin" in payload:
        structured["plugin"] = payload["plugin"]
    for key, value in payload.items():
        if key not in structured and key != "operations":
            structured[str(key)] = value
    return structured, operation_ids


async def _operation_ids_for_plugin(url: str, plugin: str) -> list[str]:
    result = await call_tool(url, "info", {"kind": "ops", "plugin": plugin})
    return sorted(_operation_ids_from_info_payload(_tool_result_payload(result)))


async def _operation_list_payload(
    url: str,
    plugin: str | None,
) -> tuple[object, list[str]]:
    if plugin is not None:
        result = await call_tool(url, "info", {"kind": "ops", "plugin": plugin})
        payload = _tool_result_payload(result)
        return _operation_list_structured_payload(payload)

    result = await call_tool(url, "info", {"kind": "plugins"})
    plugin_ids = _plugin_ids_from_info_payload(_tool_result_payload(result))
    return {"plugins": plugin_ids}, plugin_ids


def _operation_desc_plain_lines(payload: object) -> list[str]:
    if not isinstance(payload, Mapping):
        return [str(payload)]
    lines: list[str] = []
    item_id = payload.get("id")
    if isinstance(item_id, str) and item_id:
        lines.append(item_id)
    description = payload.get("description")
    if isinstance(description, str) and description:
        lines.append(description)
    operations = payload.get("operations")
    if isinstance(operations, list):
        for operation in operations:
            if not isinstance(operation, Mapping):
                continue
            operation_id = operation.get("id")
            if isinstance(operation_id, str) and operation_id:
                lines.append(operation_id)
    return lines or [str(payload)]


def _render_operation_payload(
    payload: object,
    output: str,
    plain_lines: Sequence[str],
) -> None:
    if output == "plain":
        for line in plain_lines:
            print(line)
    elif output == "yaml":
        _print_yaml(payload)
    else:
        _print_json(payload)


def _tool_error_message_for_cli(
    exc: ToolCallError,
    namespace: argparse.Namespace,
) -> str:
    message = str(exc)
    if (
        namespace.command == "info"
        and namespace.info_command in {"test", "tests"}
        and message.startswith("unknown info kind:")
    ):
        return (
            f"{message}\n"
            f"The local Arbiter client understands 'info {namespace.info_command}', "
            f"but the server at {namespace.mcp_url} does not. This usually means "
            "the running server is older than the client or was not restarted after "
            "updating the wheelhouse. Rebuild/redeploy the server package and "
            "restart the Arbiter service, then retry the command."
        )
    return message


async def _run_async(namespace: argparse.Namespace) -> int:
    if namespace.command == "info":
        if namespace.short and namespace.info_command is not None:
            print_cli_error("info --short is only valid for overview", area="usage")
            return 2
        result = await call_tool(namespace.mcp_url, "info", _info_arguments(namespace))
        payload = _with_server_url(_tool_result_payload(result), namespace.mcp_url)
        if namespace.short:
            payload = _short_info_payload(payload)
        if namespace.yaml:
            _print_yaml(payload)
        else:
            _print_json(payload)
        return 0

    if namespace.command in {"capabilities", "cap"} and (
        namespace.capabilities_command is None
        or namespace.capabilities_command == "list"
    ):
        capability_query = getattr(namespace, "capability_query", CapabilityQuery())
        if capability_query.fields or capability_query.format is not None:
            result = await call_tool(namespace.mcp_url, "describe_caps", {})
            payload = _tool_result_payload(result)
            capabilities = []
            if isinstance(payload, Mapping):
                raw_capabilities = payload.get("capabilities", [])
                if isinstance(raw_capabilities, list):
                    capabilities = raw_capabilities
            needs_version_fallback = any(
                isinstance(capability, Mapping) and not capability.get("version")
                for capability in capabilities
            )
            if needs_version_fallback and _capability_query_uses_field(
                capability_query, "version"
            ):
                version_result = await call_tool(namespace.mcp_url, "version_info", {})
                capabilities = _capabilities_with_plugin_versions(
                    capabilities,
                    _tool_result_payload(version_result),
                )
            if namespace.json:
                if capability_query.format is not None:
                    _print_json(
                        {
                            "capabilities": [
                                _format_capability(
                                    capability,
                                    capability_query.format,
                                )
                                for capability in capabilities
                                if isinstance(capability, Mapping)
                            ]
                        }
                    )
                else:
                    _print_json(
                        {
                            "capabilities": [
                                _capability_field_projection(
                                    capability,
                                    capability_query.fields,
                                )
                                for capability in capabilities
                                if isinstance(capability, Mapping)
                            ]
                        }
                    )
            else:
                if capability_query.format is not None:
                    for capability in capabilities:
                        if isinstance(capability, Mapping):
                            print(
                                _format_capability(capability, capability_query.format)
                            )
                else:
                    _print_capability_field_rows(
                        capabilities,
                        capability_query.fields,
                    )
            return 0

        result = await call_tool(namespace.mcp_url, "list_caps", {})
        payload = _tool_result_payload(result)
        if namespace.json:
            _print_json(payload)
        else:
            capabilities = []
            if isinstance(payload, Mapping):
                raw_capabilities = payload.get("capabilities", [])
                if isinstance(raw_capabilities, list):
                    capabilities = raw_capabilities
            for capability in capabilities:
                print(capability)
        return 0
    if namespace.command in {
        "capabilities",
        "cap",
    } and namespace.capabilities_command in {
        "describe",
        "desc",
    }:
        if namespace.capability is None:
            result = await call_tool(namespace.mcp_url, "describe_caps", {})
        else:
            result = await call_tool(
                namespace.mcp_url,
                "describe_cap",
                {"capability": namespace.capability},
            )
        _print_json(_tool_result_payload(result))
        return 0
    if (
        namespace.command in {"operation", "op"}
        and namespace.operation_command == "list"
    ):
        payload, plain_lines = await _operation_list_payload(
            namespace.mcp_url,
            namespace.plugin,
        )
        _render_operation_payload(payload, namespace.output, plain_lines)
        return 0
    if namespace.command in {"operation", "op"} and namespace.operation_command in {
        "describe",
        "desc",
    }:
        if ":" in namespace.id:
            result = await call_tool(
                namespace.mcp_url,
                "describe_op",
                {"id": namespace.id},
            )
        else:
            result = await call_tool(
                namespace.mcp_url,
                "info",
                {"kind": "plugin", "plugin": namespace.id},
            )
        payload = _tool_result_payload(result)
        _render_operation_payload(
            payload,
            namespace.output,
            _operation_desc_plain_lines(payload),
        )
        return 0
    if (
        namespace.command in {"operation", "op"}
        and namespace.operation_command == "run"
    ):
        result = await call_arbiter_operation(
            namespace.mcp_url,
            namespace.id,
            namespace.args,
        )
        _print_json(_tool_result_payload(result))
        return 0
    if namespace.command == "mcp" and (
        namespace.mcp_command is None or namespace.mcp_command == "tools"
    ):
        tools = await list_tools(namespace.mcp_url)
        if namespace.json:
            _print_json({"tools": tools})
        else:
            for tool in tools:
                print(tool["name"])
        return 0
    if namespace.command == "mcp" and namespace.mcp_command == "call":
        result = await call_tool(namespace.mcp_url, namespace.name, namespace.args)
        _print_json(result)
        return 0
    if namespace.command == "accounts" and (
        namespace.accounts_command is None or namespace.accounts_command == "list"
    ):
        summaries = await call_tool(namespace.mcp_url, "describe_caps", {})
        payload = _tool_result_payload(summaries)
        accounts: dict[str, object] = {}
        if isinstance(payload, Mapping):
            capabilities = payload.get("capabilities", [])
            if isinstance(capabilities, list):
                for capability in capabilities:
                    if not isinstance(capability, Mapping):
                        continue
                    capability_id = capability.get("id")
                    if not isinstance(capability_id, str):
                        continue
                    raw_accounts = capability.get("accounts", [])
                    if isinstance(raw_accounts, list):
                        accounts[capability_id] = raw_accounts
        if namespace.json:
            _print_json({"accounts": accounts})
        else:
            _print_account_summary(accounts)
        return 0
    if namespace.command == "accounts" and namespace.accounts_command in {
        "describe",
        "desc",
    }:
        capability, account_name = _split_account_selector(
            namespace.capability,
            namespace.account,
        )
        details = await call_tool(
            namespace.mcp_url,
            "describe_cap",
            {"capability": capability},
        )
        payload = _tool_result_payload(details)
        if (
            account_name is not None
            and isinstance(payload, Mapping)
            and isinstance(payload.get("accounts"), Mapping)
        ):
            account = payload["accounts"].get(account_name)
            _print_json(
                {
                    "capability": capability,
                    "account": account_name,
                    "details": account,
                }
            )
        else:
            _print_json(payload)
        return 0
    raise RuntimeError("unhandled command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args:
        _print_short_usage()
        return 2
    args = _extract_global_config_args(raw_args)
    args, extracted_overrides = _extract_client_overrides(args)
    args = _normalize_info_output_flags(args)
    args = _normalize_command_aliases(args)
    namespace, overrides = parser.parse_known_args(args)
    namespace._raw_args = args
    namespace.overrides = [*extracted_overrides, *overrides]
    if namespace.command == "artifact" and overrides:
        print_cli_error(f"unknown artifact argument: {overrides[0]}", area="usage")
        return 2
    try:
        _apply_capability_query(namespace)
    except ValueError as exc:
        print_cli_error(str(exc), area="usage")
        return 2
    if namespace.command == "bootstrap" and namespace.bootstrap_command == "client":
        return _run_bootstrap_client(namespace)
    if namespace.command == "artifact" and namespace.artifact_command == "get":
        return _run_artifact_get(namespace)
    if namespace.command == "artifact" and namespace.artifact_command == "save":
        return _run_artifact_save(namespace)
    if namespace.command == "artifact" and namespace.artifact_command == "with-temp":
        return _run_artifact_with_temp(namespace)
    if namespace.command == "artifact" and namespace.artifact_command == "with-stdin":
        return _run_artifact_with_stdin(namespace)
    try:
        _apply_resolved_mcp_url(namespace, _resolve_mcp_url(namespace))
    except (FileNotFoundError, ValueError) as exc:
        print_cli_error(str(exc), area="client config")
        return 1
    try:
        return anyio.run(_run_async, namespace)
    except KeyboardInterrupt:
        print("Arbiter client stopped.", file=sys.stderr)
        return 130
    except ToolCallError as exc:
        print_cli_error(_tool_error_message_for_cli(exc, namespace), area="tool")
        return 1
    except BaseException as exc:
        if _contains_exception(exc, httpx.TransportError):
            print_cli_error(_connection_error_message(namespace), area="connection")
            return 1
        raise
