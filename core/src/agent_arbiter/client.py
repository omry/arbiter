from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from omegaconf import DictConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .version import package_version


DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
MCP_URL_ENV_VAR = "AGENT_ARBITER_MCP_URL"
DEFAULT_CONFIG_DIR = "~/.arbiter"
DEFAULT_CLIENT_CONFIG_NAME = "arbiter-client"
BOOTSTRAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ClientConfig:
    mcp_url: str | None = None


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

    allowed_keys = {"mcp_url"}
    loaded_keys = {str(key) for key in loaded.keys()}
    unknown_keys = sorted(loaded_keys - allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"unsupported client config key(s) in {path}: {', '.join(unknown_keys)}"
        )

    mcp_url = OmegaConf.select(loaded, "mcp_url", default=None)
    if mcp_url is not None and not isinstance(mcp_url, str):
        raise ValueError(f"client config mcp_url must be a string: {path}")
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

    allowed_keys = {"mcp_url"}
    loaded_keys = {str(key) for key in loaded.keys()}
    unknown_keys = sorted(loaded_keys - allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"unsupported client override key(s): {', '.join(unknown_keys)}"
        )

    mcp_url = OmegaConf.select(loaded, "mcp_url", default=None)
    if mcp_url is not None and not isinstance(mcp_url, str):
        raise ValueError("client override mcp_url must be a string")
    return ClientConfig(mcp_url=mcp_url)


def _resolve_mcp_url(namespace: argparse.Namespace) -> str:
    config_path = _client_config_path(namespace.config_dir, namespace.config_name)
    config = _load_client_config(config_path, explicit=False)
    override_config = _override_client_config(namespace.overrides)
    return (
        override_config.mcp_url
        or os.environ.get(MCP_URL_ENV_VAR)
        or config.mcp_url
        or DEFAULT_MCP_URL
    )


def _client_config_yaml(config: ClientConfig) -> str:
    return OmegaConf.to_yaml(
        OmegaConf.create({"mcp_url": config.mcp_url or DEFAULT_MCP_URL})
    )


def _run_bootstrap_client(namespace: argparse.Namespace) -> int:
    if not BOOTSTRAP_NAME_PATTERN.fullmatch(namespace.config_name):
        print(
            "config name must contain only letters, numbers, underscores, and "
            "dashes.",
            file=sys.stderr,
        )
        return 2
    try:
        override_config = _override_client_config(namespace.overrides)
    except ValueError as exc:
        print(f"Agent Arbiter client config error: {exc}", file=sys.stderr)
        return 1

    config_path = _client_config_path(namespace.config_dir, namespace.config_name)
    if config_path.exists() and not namespace.force:
        print(f"refusing to overwrite existing file: {config_path}", file=sys.stderr)
        return 1

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_client_config_yaml(override_config), encoding="utf-8")
    print(f"wrote {config_path}")
    return 0


def _warn_if_remote_version_mismatch(initialize_result: object) -> None:
    server_info = getattr(initialize_result, "serverInfo", None)
    remote_version = getattr(server_info, "version", None)
    local_version = package_version()
    if (
        not isinstance(remote_version, str)
        or remote_version == "unknown"
        or local_version == "unknown"
        or remote_version == local_version
    ):
        return
    print(
        "Agent Arbiter version warning: "
        f"local CLI version {local_version} does not match "
        f"remote server version {remote_version}.",
        file=sys.stderr,
    )


async def list_tools(url: str) -> list[Mapping[str, object]]:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            _warn_if_remote_version_mismatch(initialize_result)
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
            initialize_result = await session.initialize()
            _warn_if_remote_version_mismatch(initialize_result)
            return await session.call_tool(name, dict(arguments))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arbiter",
        description="Client CLI for an Agent Arbiter MCP server.",
        epilog=(
            "Override client config values with Hydra-style KEY=VALUE "
            "arguments after the command, for example: "
            "arbiter tools list mcp_url=http://127.0.0.1:8000/mcp"
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
    subcommands = parser.add_subparsers(dest="command", required=True)

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


def _extract_global_config_args(args: Sequence[str]) -> list[str]:
    extracted: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
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


async def _run_async(namespace: argparse.Namespace) -> int:
    if namespace.command == "tools" and namespace.tools_command == "list":
        tools = await list_tools(namespace.mcp_url)
        if namespace.json:
            _print_json({"tools": tools})
        else:
            for tool in tools:
                print(tool["name"])
        return 0
    if namespace.command == "tools" and namespace.tools_command == "call":
        result = await call_tool(namespace.mcp_url, namespace.name, namespace.args)
        _print_json(result)
        return 0
    if namespace.command == "accounts" and namespace.accounts_command == "list":
        result = await call_tool(namespace.mcp_url, "list_accounts", {})
        _print_json(_tool_result_payload(result))
        return 0
    raise RuntimeError("unhandled command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = _extract_global_config_args(list(sys.argv[1:] if argv is None else argv))
    namespace, overrides = parser.parse_known_args(args)
    namespace.overrides = overrides
    if namespace.command == "bootstrap" and namespace.bootstrap_command == "client":
        return _run_bootstrap_client(namespace)
    try:
        namespace.mcp_url = _resolve_mcp_url(namespace)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Agent Arbiter client config error: {exc}", file=sys.stderr)
        return 1
    try:
        return anyio.run(_run_async, namespace)
    except KeyboardInterrupt:
        print("Agent Arbiter client stopped.", file=sys.stderr)
        return 130
    except BaseException as exc:
        if _contains_exception(exc, httpx.ConnectError):
            print(
                f"Could not connect to Agent Arbiter at {namespace.mcp_url}. "
                "Is arbiter-server serve running?",
                file=sys.stderr,
            )
            return 1
        raise
