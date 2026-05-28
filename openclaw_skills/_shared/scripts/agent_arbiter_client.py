#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AgentArbiterClientConfig:
    url: str
    bearer_token: str | None = None
    timeout_seconds: float = 30.0


def config_from_env() -> AgentArbiterClientConfig:
    url = os.environ.get("AGENT_ARBITER_MCP_URL", "").strip()
    if not url:
        raise ValueError("AGENT_ARBITER_MCP_URL is required")

    bearer_token = os.environ.get("AGENT_ARBITER_MCP_BEARER_TOKEN", "").strip() or None
    timeout_raw = os.environ.get("AGENT_ARBITER_TIMEOUT_SECONDS", "30").strip()

    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError("AGENT_ARBITER_TIMEOUT_SECONDS must be numeric") from exc

    return AgentArbiterClientConfig(
        url=url,
        bearer_token=bearer_token,
        timeout_seconds=timeout_seconds,
    )


def parse_json_argument(
    value: str | None, *, default: dict[str, Any] | None = None
) -> dict[str, Any]:
    if value is None:
        return default or {}

    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("JSON argument must decode to an object")
    return loaded


def normalize_tool_result(result: Any) -> dict[str, Any]:
    normalized: dict[str, Any]
    if result.structuredContent is not None:
        normalized = dict(result.structuredContent)
    else:
        normalized = {
            "ok": not result.isError,
            "content": [
                item.model_dump() if hasattr(item, "model_dump") else str(item)
                for item in result.content
            ],
        }

    if result.isError:
        normalized.setdefault("ok", False)

    return normalized


async def call_tool(
    config: AgentArbiterClientConfig,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import Implementation

    headers: dict[str, str] = {}
    if config.bearer_token:
        headers["Authorization"] = f"Bearer {config.bearer_token}"

    timeout = httpx.Timeout(config.timeout_seconds)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(
            config.url,
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(
                read_stream,
                write_stream,
                client_info=Implementation(
                    name="openclaw-agent-arbiter-skill", version="0.1.0"
                ),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
                return normalize_tool_result(result)


def call_tool_sync(
    config: AgentArbiterClientConfig,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return asyncio.run(call_tool(config, tool_name, arguments))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call an Agent Arbiter tool over Streamable HTTP."
    )
    parser.add_argument("tool_name", help="Agent Arbiter tool name.")
    parser.add_argument(
        "--arguments-json",
        required=True,
        help="JSON object containing the tool arguments.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = config_from_env()
    arguments = parse_json_argument(args.arguments_json)
    result = call_tool_sync(config, args.tool_name, arguments)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
