from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def _json_default(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def _print_json(value: object) -> None:
    print(json.dumps(value, default=_json_default, sort_keys=True))


def _tool_result_payload(result: object) -> object:
    if isinstance(result, Mapping) and "structuredContent" in result:
        structured_content = result["structuredContent"]
        if structured_content is not None:
            return structured_content

    structured_content = getattr(result, "structuredContent", None)
    if structured_content is not None:
        return structured_content

    return result


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


async def list_tools(url: str) -> list[Mapping[str, object]]:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
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
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(name, dict(arguments))


def _default_url() -> str:
    return os.environ.get("AGENT_ARBITER_URL", DEFAULT_URL)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter",
        description="Client CLI for an Agent Arbiter MCP server.",
    )
    parser.add_argument(
        "--url",
        default=_default_url(),
        help=f"Agent Arbiter MCP URL (default: {DEFAULT_URL})",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    tools = subcommands.add_parser("tools", help="inspect or call MCP tools")
    tools_subcommands = tools.add_subparsers(dest="tools_command", required=True)

    tools_list = tools_subcommands.add_parser("list", help="list available MCP tools")
    tools_list.add_argument(
        "--json",
        action="store_true",
        help="print the full tool metadata as JSON",
    )

    tools_call = tools_subcommands.add_parser("call", help="call an MCP tool")
    tools_call.add_argument("name", help="tool name")
    tools_call.add_argument(
        "--args",
        default={},
        type=_parse_json_object,
        help='tool arguments as a JSON object, for example \'{"account": "primary"}\'',
    )

    accounts = subcommands.add_parser("accounts", help="inspect configured accounts")
    accounts_subcommands = accounts.add_subparsers(
        dest="accounts_command",
        required=True,
    )
    accounts_subcommands.add_parser("list", help="call the list_accounts MCP tool")

    return parser


async def _run_async(namespace: argparse.Namespace) -> int:
    if namespace.command == "tools" and namespace.tools_command == "list":
        tools = await list_tools(namespace.url)
        if namespace.json:
            _print_json({"tools": tools})
        else:
            for tool in tools:
                print(tool["name"])
        return 0
    if namespace.command == "tools" and namespace.tools_command == "call":
        result = await call_tool(namespace.url, namespace.name, namespace.args)
        _print_json(result)
        return 0
    if namespace.command == "accounts" and namespace.accounts_command == "list":
        result = await call_tool(namespace.url, "list_accounts", {})
        _print_json(_tool_result_payload(result))
        return 0
    raise RuntimeError("unhandled command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    namespace = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return anyio.run(_run_async, namespace)
    except KeyboardInterrupt:
        print("Agent Arbiter client stopped.", file=sys.stderr)
        return 130
    except BaseException as exc:
        if _contains_exception(exc, httpx.ConnectError):
            print(
                f"Could not connect to Agent Arbiter at {namespace.url}. "
                "Is agent-arbiter serve running?",
                file=sys.stderr,
            )
            return 1
        raise
