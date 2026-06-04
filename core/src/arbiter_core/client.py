from __future__ import annotations

import argparse
import json
import os
import re
import string
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

from .cli_errors import print_cli_error
from .version import arbiter_core_version


DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
MCP_URL_ENV_VAR = "ARBITER_MCP_URL"
DEFAULT_CONFIG_DIR = "~/.arbiter"
DEFAULT_CLIENT_CONFIG_NAME = "arbiter-client"
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
    local_version = arbiter_core_version()
    if (
        not isinstance(remote_version, str)
        or remote_version == "unknown"
        or local_version == "unknown"
        or remote_version == local_version
    ):
        return
    print(
        "Arbiter core version warning: "
        f"local CLI core version {local_version} does not match "
        f"remote server core version {remote_version}.",
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
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
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
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
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
        prog="arbiter",
        description="Client CLI for an Arbiter MCP server.",
        epilog=(
            f"Uses {DEFAULT_CONFIG_DIR}/{DEFAULT_CLIENT_CONFIG_NAME}.yaml by "
            "default. "
            "Override client config values with Hydra-style KEY=VALUE "
            "arguments after the command, for example: "
            "arbiter cap arbiter.mcp_url=http://127.0.0.1:8000/mcp"
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
        version=f"%(prog)s {arbiter_core_version()}",
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
    info_subcommands = info.add_subparsers(dest="info_command")

    info_subcommands.add_parser(
        "plugins",
        help="list installed service plugins",
    )
    info_plugin = info_subcommands.add_parser(
        "plugin",
        help="describe one service plugin",
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
        if arg.startswith("arbiter.mcp_url="):
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
    print("usage: arbiter {info,op,mcp} ...")
    print("Run 'arbiter --help' for full help.")


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


async def _run_async(namespace: argparse.Namespace) -> int:
    if namespace.command == "info":
        result = await call_tool(namespace.mcp_url, "info", _info_arguments(namespace))
        payload = _with_server_url(_tool_result_payload(result), namespace.mcp_url)
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
    args = _normalize_command_aliases(args)
    namespace, overrides = parser.parse_known_args(args)
    namespace.overrides = [*extracted_overrides, *overrides]
    try:
        _apply_capability_query(namespace)
    except ValueError as exc:
        print_cli_error(str(exc), area="usage")
        return 2
    if namespace.command == "bootstrap" and namespace.bootstrap_command == "client":
        return _run_bootstrap_client(namespace)
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
        print_cli_error(str(exc), area="tool")
        return 1
    except BaseException as exc:
        if _contains_exception(exc, httpx.TransportError):
            print_cli_error(_connection_error_message(namespace), area="connection")
            return 1
        raise
