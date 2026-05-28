#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import string
import sys
from pathlib import Path
from typing import Any


_SHARED_SCRIPTS = Path(__file__).resolve().parents[2] / "_shared" / "scripts"
_SKILL_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TEMPLATE_REGISTRY = _SKILL_ROOT / "templates.json"
sys.path.insert(0, str(_SHARED_SCRIPTS))

from agent_arbiter_client import (
    call_tool_sync,
    config_from_env,
    parse_json_argument,
)  # noqa: E402


def _template_fields(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    formatter = string.Formatter()
    fields: set[str] = set()
    for _, field_name, _, _ in formatter.parse(value):
        if field_name:
            fields.add(field_name)
    return fields


def _render_string(value: str, params: dict[str, Any]) -> str:
    try:
        return value.format_map(params)
    except KeyError as exc:
        missing = exc.args[0]
        raise ValueError(f"missing template parameter: {missing}") from exc


def _render_list(values: list[Any], params: dict[str, Any]) -> list[str]:
    rendered: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("template recipient values must be strings")
        rendered.append(_render_string(value, params))
    return rendered


def load_registry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("templates"), dict):
        raise ValueError("template registry must contain a templates object")
    return data


def default_registry_path() -> Path:
    return _DEFAULT_TEMPLATE_REGISTRY


def build_payload(
    template: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    account = template.get("account")
    subject = template.get("subject")
    text_body = template.get("text_body")
    html_body = template.get("html_body")
    to_values = template.get("to")

    if not isinstance(account, str) or not account.strip():
        raise ValueError("template account is required")
    if not isinstance(subject, str):
        raise ValueError("template subject is required")
    if not isinstance(to_values, list) or not to_values:
        raise ValueError("template to must be a non-empty array")
    if text_body is None and html_body is None:
        raise ValueError("template must define text_body or html_body")

    declared_allowed = template.get("allowed_params")
    if declared_allowed is not None:
        if not isinstance(declared_allowed, list) or not all(
            isinstance(item, str) for item in declared_allowed
        ):
            raise ValueError("allowed_params must be an array of strings")
        unexpected = sorted(set(params) - set(declared_allowed))
        if unexpected:
            raise ValueError(f"unexpected template parameters: {', '.join(unexpected)}")

    payload: dict[str, Any] = {
        "account": account,
        "to": _render_list(to_values, params),
        "subject": _render_string(subject, params),
    }

    if isinstance(text_body, str):
        payload["text_body"] = _render_string(text_body, params)
    elif text_body is not None:
        raise ValueError("template text_body must be a string when present")

    if isinstance(html_body, str):
        payload["html_body"] = _render_string(html_body, params)
    elif html_body is not None:
        raise ValueError("template html_body must be a string when present")

    cc_values = template.get("cc")
    if cc_values is not None:
        if not isinstance(cc_values, list):
            raise ValueError("template cc must be an array when present")
        payload["cc"] = _render_list(cc_values, params)

    bcc_values = template.get("bcc")
    if bcc_values is not None:
        if not isinstance(bcc_values, list):
            raise ValueError("template bcc must be an array when present")
        payload["bcc"] = _render_list(bcc_values, params)

    used_fields = set().union(
        _template_fields(subject),
        _template_fields(text_body),
        _template_fields(html_body),
        *(_template_fields(value) for value in to_values),
        *(_template_fields(value) for value in cc_values or []),
        *(_template_fields(value) for value in bcc_values or []),
    )
    missing_params = sorted(field for field in used_fields if field not in params)
    if missing_params:
        raise ValueError(f"missing template parameters: {', '.join(missing_params)}")

    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit a predefined Agent Arbiter send_email request."
    )
    parser.add_argument(
        "--template", required=True, help="Template name from the registry."
    )
    parser.add_argument(
        "--params-json",
        default="{}",
        help="JSON object of allowed template parameters.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = config_from_env()
    registry_location = default_registry_path()
    if not registry_location.is_file():
        parser.error(
            f"template registry not found at {registry_location}; "
            "place templates.json next to the predefined skill"
        )

    registry = load_registry(registry_location)
    templates = registry["templates"]
    if args.template not in templates:
        parser.error(f"unknown template: {args.template}")

    params = parse_json_argument(args.params_json)
    payload = build_payload(templates[args.template], params)
    result = call_tool_sync(config, "send_email", payload)
    result.setdefault("template", args.template)
    result.setdefault("account", payload["account"])

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
