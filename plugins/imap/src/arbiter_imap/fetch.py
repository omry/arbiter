"""Decode FETCH metadata without interpreting or materializing message bodies."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import (
    collapse_rfc2231_value,
    decode_params,
    formataddr,
    quote,
    unquote,
)
import re
from typing import Any, TypeAlias


Value: TypeAlias = bytes | None | list["Value"]
_TOKEN = re.compile(rb'\s+|[()]|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def fetch_attributes(
    data: Sequence[Any], uid: str, *, required_attribute: bytes | None = None
) -> dict[bytes, Value]:
    """Handle imaplib's split literals, including UID after a literal.

    Only response framing is tokenized; literal contents remain opaque values.
    An iterative stack avoids recursion on untrusted nested metadata.
    """
    root: list[Value] = []
    stack = [root]
    for item in data:
        literal = None
        if isinstance(item, tuple) and len(item) == 2:
            raw, literal = item
        else:
            raw = item
        if not isinstance(raw, bytes) or (
            literal is not None and not isinstance(literal, bytes)
        ):
            raise ValueError("Invalid FETCH response chunk")
        if literal is not None:
            marker = re.search(rb"\{([0-9]+)\}$", raw)
            if (
                marker is None
                or (marker.group(1).lstrip(b"0") or b"0") != str(len(literal)).encode()
            ):
                raise ValueError("Invalid FETCH literal framing")
            raw = raw[: marker.start()]
        position = 0
        for match in _TOKEN.finditer(raw):
            if match.start() != position:
                raise ValueError("Invalid FETCH string")
            position = match.end()
            token = match.group()
            if token.isspace():
                continue
            if token == b"(":
                nested: list[Value] = []
                stack[-1].append(nested)
                stack.append(nested)
            elif token == b")":
                if len(stack) == 1:
                    raise ValueError("Unbalanced FETCH list")
                stack.pop()
            elif token.startswith(b'"'):
                stack[-1].append(re.sub(rb"\\(.)", rb"\1", token[1:-1]))
            else:
                stack[-1].append(None if token.upper() == b"NIL" else token)
        if position != len(raw):
            raise ValueError("Invalid FETCH string")
        if literal is not None:
            stack[-1].append(literal)
    if len(stack) != 1 or len(root) % 2:
        raise ValueError("Incomplete FETCH response")
    matches: list[dict[bytes, Value]] = []
    for index in range(0, len(root), 2):
        sequence, attributes = root[index : index + 2]
        if not isinstance(sequence, bytes) or not sequence.isdigit():
            raise ValueError("Invalid FETCH sequence")
        if not isinstance(attributes, list) or len(attributes) % 2:
            raise ValueError("Invalid FETCH attributes")
        decoded: dict[bytes, Value] = {}
        for offset in range(0, len(attributes), 2):
            name, value = attributes[offset : offset + 2]
            if not isinstance(name, bytes) or name.upper() in decoded:
                raise ValueError("Invalid or duplicate FETCH attribute")
            decoded[name.upper()] = value
        response_uid = decoded.get(b"UID")
        if isinstance(response_uid, bytes) and response_uid.isdigit():
            if response_uid.lstrip(b"0") == uid.encode().lstrip(b"0"):
                # Unsolicited FLAGS updates for the requested UID are separate
                # responses, not duplicate requested metadata or body data.
                if required_attribute is None or required_attribute in decoded:
                    matches.append(decoded)
    if len(matches) != 1:
        raise ValueError("FETCH did not return exactly one matching UID")
    return matches[0]


def text(value: Value) -> str:
    if value is None:
        return ""
    if not isinstance(value, bytes):
        raise ValueError("Expected an IMAP string")
    return value.decode("utf-8", errors="replace")


def envelope_message(value: Value) -> EmailMessage:
    if not isinstance(value, list) or len(value) != 10:
        raise ValueError("Invalid IMAP envelope")
    message = EmailMessage(policy=policy.default)
    for name, index in (("Date", 0), ("Subject", 1), ("Message-ID", 9)):
        if value[index] is not None:
            message[name] = text(value[index])
    for name, index in (("From", 2), ("To", 5), ("Cc", 6)):
        addresses = value[index]
        if addresses is None:
            continue
        if not isinstance(addresses, list):
            raise ValueError("Invalid envelope address list")
        formatted: list[str] = []
        for address in addresses:
            if not isinstance(address, list) or len(address) != 4:
                raise ValueError("Invalid envelope address")
            personal, _, mailbox, host = address
            # Group delimiters have NIL hosts; group members remain addresses.
            if host is not None and mailbox is not None:
                formatted.append(
                    formataddr((text(personal), f"{text(mailbox)}@{text(host)}"))
                )
        if formatted:
            message[name] = ", ".join(formatted)
    return message


@dataclass(frozen=True)
class BodyPart:
    section: str
    headers: EmailMessage
    encoded_size: int
    encoding: str
    within_attachment: bool


def _set_parameters(
    message: EmailMessage, header: str, base: str, value: Value
) -> None:
    required = {
        "content-type": {"charset", "name"},
        "content-disposition": {"filename"},
    }[header.lower()]
    selected: list[tuple[str, str]] = []
    if value is not None:
        if not isinstance(value, list) or len(value) % 2:
            raise ValueError("Invalid MIME parameters")
        for index in range(0, len(value), 2):
            name = text(value[index])
            family = name.partition("*")[0].lower()
            if family in required:
                selected.append((name, text(value[index + 1])))
    if not selected:
        message[header] = base
        return

    # Decode RFC2231 continuations once, after filtering out parameters that
    # the summary never exposes.  The temporary primary parameter is required
    # by email.utils.decode_params and is discarded below.
    decoded = decode_params([("x", ""), *selected])[1:]
    parameters: dict[str, str] = {}
    for name, parameter in decoded:
        family = name.partition("*")[0].lower()
        if family in required and family not in parameters:
            parameters[family] = unquote(collapse_rfc2231_value(parameter))

    header_value = base + "".join(
        f'; {name}="{quote(parameter)}"' for name, parameter in parameters.items()
    )
    message[header] = header_value


def body_parts(value: Value) -> Iterator[BodyPart]:
    pending = [(value, "", False)]
    while pending:
        node, section, inherited_attachment = pending.pop()
        if not isinstance(node, list) or not node:
            raise ValueError("Invalid BODYSTRUCTURE")
        message = EmailMessage(policy=policy.default)
        child_count = 0
        if isinstance(node[0], list):
            child_count = next(
                (i for i, child in enumerate(node) if not isinstance(child, list)),
                len(node),
            )
            if child_count == len(node):
                raise ValueError("Missing multipart subtype")
            _set_parameters(
                message,
                "Content-Type",
                f"multipart/{text(node[child_count])}",
                node[child_count + 1] if len(node) > child_count + 1 else None,
            )
            disposition_index = child_count + 2
        else:
            if len(node) < 7 or not isinstance(node[6], bytes) or not node[6].isdigit():
                raise ValueError("Invalid body fields")
            _set_parameters(
                message,
                "Content-Type",
                f"{text(node[0])}/{text(node[1])}",
                node[2],
            )
            if node[3] is not None:
                message["Content-ID"] = text(node[3])
            media = message.get_content_type()
            disposition_index = (
                9
                if media.startswith("text/")
                else 11 if media in {"message/rfc822", "message/global"} else 8
            )
        disposition = node[disposition_index] if len(node) > disposition_index else None
        if disposition is not None:
            if not isinstance(disposition, list) or len(disposition) != 2:
                raise ValueError("Invalid body disposition")
            _set_parameters(
                message,
                "Content-Disposition",
                text(disposition[0]),
                disposition[1],
            )
        # Inline alone also describes ordinary text bodies and multipart
        # containers. Keep those eligible for snippets while excluding parts
        # identified as attachments, including named or Content-ID inline parts.
        attached = (
            inherited_attachment
            or message.get_content_disposition() == "attachment"
            or message.get_filename() is not None
            or message.get("Content-ID") is not None
        )
        if isinstance(node[0], list):
            for index in reversed(range(child_count)):
                child_section = f"{section}.{index + 1}" if section else str(index + 1)
                pending.append((node[index], child_section, attached))
        elif message.get_content_type() in {"message/rfc822", "message/global"}:
            if len(node) < 10:
                raise ValueError("Incomplete encapsulated message")
            child = node[8]
            parent_section = section or "1"
            child_section = (
                parent_section
                if isinstance(child, list) and child and isinstance(child[0], list)
                else parent_section + ".1"
            )
            pending.append((child, child_section, attached))
        else:
            yield BodyPart(
                section or "1", message, int(node[6]), text(node[5]), attached
            )
