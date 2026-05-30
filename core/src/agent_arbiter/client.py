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


def _print_account_summary(accounts: Mapping[str, object]) -> None:
    for capability, names in accounts.items():
        print(capability)
        if isinstance(names, Sequence) and not isinstance(names, str):
            for name in names:
                print(f"  {name}")


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
        prog="arbiter",
        description="Client CLI for an Agent Arbiter MCP server.",
        epilog=(
            f"Uses {DEFAULT_CONFIG_DIR}/{DEFAULT_CLIENT_CONFIG_NAME}.yaml by "
            "default. "
            "Override client config values with Hydra-style KEY=VALUE "
            "arguments after the command, for example: "
            "arbiter cap mcp_url=http://127.0.0.1:8000/mcp"
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
        version=f"%(prog)s {package_version()}",
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
        help="discover Agent Arbiter capabilities (alias: capabilities)",
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
        help="inspect or run Agent Arbiter operations (alias: operation)",
    )
    operation_subcommands = operation.add_subparsers(
        dest="operation_command",
        required=True,
    )
    operation_describe = operation_subcommands.add_parser(
        "desc",
        help="describe one operation (alias: describe)",
    )
    operation_describe.add_argument("id", help="operation id, such as smtp:send_email")
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
    for arg in args:
        if skip_next:
            remaining.append(arg)
            skip_next = False
            continue
        if arg == "--args":
            remaining.append(arg)
            skip_next = True
            continue
        if arg.startswith("mcp_url="):
            overrides.append(arg)
            continue
        remaining.append(arg)
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


def _print_short_usage() -> None:
    print("usage: arbiter {cap,op,accounts} ...")
    print("Run 'arbiter --help' for full help.")


async def _run_async(namespace: argparse.Namespace) -> int:
    if namespace.command in {"capabilities", "cap"} and (
        namespace.capabilities_command is None
        or namespace.capabilities_command == "list"
    ):
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
    if namespace.command in {"operation", "op"} and namespace.operation_command in {
        "describe",
        "desc",
    }:
        result = await call_tool(
            namespace.mcp_url,
            "describe_op",
            {"id": namespace.id},
        )
        _print_json(_tool_result_payload(result))
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
        details = await call_tool(
            namespace.mcp_url,
            "describe_cap",
            {"capability": namespace.capability},
        )
        payload = _tool_result_payload(details)
        if (
            namespace.account is not None
            and isinstance(payload, Mapping)
            and isinstance(payload.get("accounts"), Mapping)
        ):
            account = payload["accounts"].get(namespace.account)
            _print_json(
                {
                    "capability": namespace.capability,
                    "account": namespace.account,
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
    args = _normalize_command_aliases(args)
    namespace, overrides = parser.parse_known_args(args)
    namespace.overrides = [*extracted_overrides, *overrides]
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
