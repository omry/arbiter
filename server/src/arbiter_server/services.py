from __future__ import annotations

from collections.abc import Callable, ItemsView, KeysView, Mapping, Sequence
from dataclasses import MISSING, Field, asdict, dataclass, field, fields, is_dataclass
from types import UnionType
from typing import Any, Protocol, TypeVar, Union, cast, get_args, get_origin
from typing import get_type_hints

from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .version import arbiter_server_version, compatibility_line, server_api_version


RuntimeT = TypeVar("RuntimeT")
SERVER_VERSION = arbiter_server_version()
SERVER_API_VERSION = server_api_version()
ACCOUNT_TEST_STATUSES = {"ok", "failed", "skipped"}


class ToolServer(Protocol):
    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: Any = None,
        icons: Any = None,
        meta: Any = None,
        structured_output: bool | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


@dataclass(frozen=True)
class RuntimeRegistry:
    runtimes: Mapping[str, object]

    def require_object(self, service_name: str) -> object:
        runtime = self.runtimes.get(service_name)
        if runtime is None:
            raise RuntimeError(f"service runtime is not configured: {service_name}")
        return runtime

    def require(self, service_name: str, runtime_type: type[RuntimeT]) -> RuntimeT:
        runtime = self.require_object(service_name)
        if not isinstance(runtime, runtime_type):
            raise RuntimeError(f"service runtime is not configured: {service_name}")
        return runtime

    def items(self) -> ItemsView[str, object]:
        return self.runtimes.items()

    def keys(self) -> KeysView[str]:
        return self.runtimes.keys()


@dataclass(frozen=True)
class ServiceRuntimeContext:
    dependencies: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ServicePluginContext:
    runtimes: RuntimeRegistry


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    description: str


OperationInputSchema = type[object]


@dataclass(frozen=True)
class OperationDescriptor:
    name: str
    description: str
    input_schema: OperationInputSchema


@dataclass(frozen=True)
class ConfigCheckIssue:
    message: str
    account: str | None = None
    policy: str | None = None


@dataclass(frozen=True)
class ConfigCheckWarning(ConfigCheckIssue):
    pass


class ConfigCheckError(ValueError):
    def __init__(self, issues: Sequence[ConfigCheckIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "; ".join(issue.message for issue in self.issues)
            if self.issues
            else "config check failed"
        )


def operation_id(capability: str, operation: str) -> str:
    return f"{capability}:{operation}"


def parse_operation_id(value: str) -> tuple[str, str]:
    capability, separator, operation = value.partition(":")
    if not capability or not separator or not operation:
        raise ValueError(
            "operation id must use CAPABILITY:OPERATION syntax: " f"{value}"
        )
    return capability, operation


class ServicePlugin(Protocol):
    name: str
    version: str
    server_api_version: str

    # Called before Hydra composes application config. Plugins register all
    # service-owned schema and example options in their ConfigStore groups here.
    def register_configs(self, config_store: Any) -> None: ...

    def bootstrap_config(
        self,
        *,
        kind: str,
        name: str,
    ) -> object | None: ...

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> object: ...

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor: ...

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> Sequence[OperationDescriptor]: ...

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        context: ServicePluginContext,
    ) -> object: ...


class ServicePluginConfigChecker(Protocol):
    def check_config(
        self,
        *,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> Sequence[ConfigCheckWarning] | None: ...


@dataclass(frozen=True)
class ServicePluginRuntimeInfo:
    name: str
    version: str
    server_api_version: str


def service_plugin_runtime_info(
    service_plugin: ServicePlugin,
) -> ServicePluginRuntimeInfo:
    try:
        plugin_version = service_plugin.version
    except AttributeError as exc:
        raise RuntimeError(
            f"service plugin {service_plugin.name} does not declare a version"
        ) from exc

    try:
        plugin_server_api_version = service_plugin.server_api_version
    except AttributeError as exc:
        raise RuntimeError(
            f"service plugin {service_plugin.name} does not declare "
            "an Arbiter server API version"
        ) from exc

    return ServicePluginRuntimeInfo(
        name=service_plugin.name,
        version=plugin_version,
        server_api_version=plugin_server_api_version,
    )


def validate_service_plugin_compatibility(
    service_plugin: ServicePlugin,
) -> None:
    info = service_plugin_runtime_info(service_plugin)
    if info.server_api_version != SERVER_API_VERSION:
        raise RuntimeError(
            f"service plugin {info.name} targets Arbiter server API "
            f"{info.server_api_version}, but loaded server API is {SERVER_API_VERSION}"
        )

    plugin_line = compatibility_line(info.version)
    if plugin_line != SERVER_API_VERSION:
        raise RuntimeError(
            f"service plugin {info.name} version {info.version} is not on "
            f"loaded server API line {SERVER_API_VERSION}"
        )


def validate_service_plugins(
    service_plugins: Sequence[ServicePlugin],
) -> None:
    for service_plugin in service_plugins:
        validate_service_plugin_compatibility(service_plugin)


def check_service_plugin_config(
    service_plugin: ServicePlugin,
    *,
    accounts: Mapping[str, object],
    policies: Mapping[str, object],
) -> tuple[ConfigCheckWarning, ...]:
    check_config = getattr(service_plugin, "check_config", None)
    if not callable(check_config):
        return ()
    warnings = cast(ServicePluginConfigChecker, service_plugin).check_config(
        accounts=accounts,
        policies=policies,
    )
    return tuple(warnings or ())


class OperationCatalog:
    def __init__(
        self,
        service_plugins: Sequence[ServicePlugin],
        context: ServicePluginContext,
        *,
        max_account_preview_limit: int,
        max_operation_preview_limit: int,
    ) -> None:
        if max_account_preview_limit < 1:
            raise ValueError("max_account_preview_limit must be >= 1")
        if max_operation_preview_limit < 1:
            raise ValueError("max_operation_preview_limit must be >= 1")
        self._context = context
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._operations: dict[str, dict[str, OperationDescriptor]] = {}
        self._plugins: dict[str, ServicePlugin] = {}
        self._max_account_preview_limit = max_account_preview_limit
        self._max_operation_preview_limit = max_operation_preview_limit

        for plugin in service_plugins:
            capability = plugin.describe_capability(context)
            if capability.name in self._capabilities:
                raise RuntimeError(f"duplicate capability: {capability.name}")
            operations: dict[str, OperationDescriptor] = {}
            for operation in plugin.describe_operations(context):
                if operation.name in operations:
                    raise RuntimeError(
                        f"duplicate operation for {capability.name}: {operation.name}"
                    )
                operations[operation.name] = operation
            self._capabilities[capability.name] = capability
            self._operations[capability.name] = operations
            self._plugins[capability.name] = plugin

    def list_capabilities(self) -> dict[str, object]:
        return {"capabilities": sorted(self._capabilities)}

    def describe_capabilities(
        self,
        *,
        operation_preview_limit: int = 8,
        account_preview_limit: int = 8,
    ) -> dict[str, object]:
        if operation_preview_limit < 0:
            raise ValueError("operation_preview_limit must be >= 0")
        if account_preview_limit < 0:
            raise ValueError("account_preview_limit must be >= 0")
        effective_operation_preview_limit = min(
            operation_preview_limit,
            self._max_operation_preview_limit,
        )
        effective_account_preview_limit = min(
            account_preview_limit,
            self._max_account_preview_limit,
        )
        return {
            "capabilities": [
                self._capability_summary(
                    capability,
                    operation_preview_limit=effective_operation_preview_limit,
                    account_preview_limit=effective_account_preview_limit,
                )
                for capability in sorted(self._capabilities)
            ]
        }

    def describe_capability(self, capability: str) -> dict[str, object]:
        descriptor = self._require_capability(capability)
        return {
            "id": descriptor.name,
            "description": descriptor.description,
            "accounts": self._account_summaries(capability),
            "operations": [
                self._operation_summary(capability, operation)
                for operation in sorted(self._operations[capability])
            ],
        }

    def describe_operation(self, operation_ref: str) -> dict[str, object]:
        capability, operation = parse_operation_id(operation_ref)
        descriptor = self._require_operation(capability, operation)
        return {
            "id": operation_id(capability, operation),
            "capability": capability,
            "name": descriptor.name,
            "description": descriptor.description,
            "input_schema": operation_input_schema(descriptor.input_schema),
        }

    def info(
        self,
        *,
        kind: str = "overview",
        plugin: str | None = None,
        account: str | None = None,
        operation: str | None = None,
        version_info: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if kind == "overview":
            return self._info_overview(version_info or {})
        if kind == "plugins":
            return {
                "kind": "plugins",
                "plugins": [
                    self._info_plugin_summary(capability, include_accounts=False)
                    for capability in sorted(self._capabilities)
                ],
            }
        if kind == "plugin":
            if plugin is None:
                raise ValueError("info plugin requires plugin")
            return self._info_plugin(plugin)
        if kind == "accounts":
            if plugin is None:
                raise ValueError("info accounts requires plugin")
            return {
                "kind": "accounts",
                "plugin": plugin,
                "accounts": self._info_account_summaries(plugin),
            }
        if kind == "account":
            if plugin is None or account is None:
                raise ValueError("info account requires plugin and account")
            return self._info_account(plugin, account)
        if kind == "tests":
            return {
                "kind": "tests",
                "plugins": [
                    self._info_plugin_tests(capability)
                    for capability in sorted(self._capabilities)
                ],
            }
        if kind == "test":
            if plugin is None:
                raise ValueError("info test requires plugin")
            if account is not None:
                return self._info_account_test(plugin, account)
            plugin_tests = self._info_plugin_tests(plugin)
            plugin_tests["kind"] = "test"
            return plugin_tests
        if kind == "ops":
            if plugin is None:
                raise ValueError("info ops requires plugin")
            self._require_capability(plugin)
            return {
                "kind": "ops",
                "plugin": plugin,
                "operations": [
                    self._operation_summary(plugin, operation_name)
                    for operation_name in sorted(self._operations[plugin])
                ],
            }
        if kind == "op":
            if plugin is None or operation is None:
                raise ValueError("info op requires plugin and operation")
            operation_info = self.describe_operation(operation_id(plugin, operation))
            operation_info["kind"] = "op"
            return operation_info
        supported = "account, accounts, op, ops, overview, plugin, plugins, test, tests"
        raise ValueError(f"unknown info kind: {kind}; supported kinds: {supported}")

    def invoke_operation(
        self,
        operation_ref: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> object:
        capability, operation = parse_operation_id(operation_ref)
        descriptor = self._require_operation(capability, operation)
        operation_arguments = _validate_operation_arguments(
            operation_id(capability, operation),
            descriptor.input_schema,
            dict(arguments or {}),
        )
        return self._plugins[capability].invoke_operation(
            operation,
            operation_arguments,
            self._context,
        )

    def check_operation(
        self,
        operation_ref: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> object:
        capability, operation = parse_operation_id(operation_ref)
        descriptor = self._require_operation(capability, operation)
        operation_arguments = _validate_operation_arguments(
            operation_id(capability, operation),
            descriptor.input_schema,
            dict(arguments or {}),
        )
        plugin = self._plugins[capability]
        check_operation = getattr(plugin, "check_operation", None)
        if not callable(check_operation):
            return {
                "operation": operation_id(capability, operation),
                "allowed": None,
                "why_not": "operation check is not supported by this capability",
            }
        return check_operation(operation, operation_arguments, self._context)

    def _require_capability(self, capability: str) -> CapabilityDescriptor:
        descriptor = self._capabilities.get(capability)
        if descriptor is None:
            raise ValueError(f"unknown capability: {capability}")
        return descriptor

    def _require_operation(
        self,
        capability: str,
        operation: str,
    ) -> OperationDescriptor:
        self._require_capability(capability)
        descriptor = self._operations[capability].get(operation)
        if descriptor is None:
            raise ValueError(
                f"unknown operation: {operation_id(capability, operation)}"
            )
        return descriptor

    def _info_overview(
        self,
        version_info: Mapping[str, object],
    ) -> dict[str, object]:
        overview: dict[str, object] = {
            "kind": "overview",
            "deployment_scope": version_info.get("deployment_scope", "unknown"),
            "plugins": [
                self._info_plugin_summary(capability, include_accounts=True)
                for capability in sorted(self._capabilities)
            ],
        }
        server = version_info.get("server")
        if server is not None:
            overview["server"] = server
        source = version_info.get("source")
        if source is not None:
            overview["source"] = source
        return overview

    def _info_plugin_summary(
        self,
        capability: str,
        *,
        include_accounts: bool,
    ) -> dict[str, object]:
        descriptor = self._capabilities[capability]
        account_summaries = self._account_summaries(capability)
        operation_names = sorted(self._operations[capability])
        summary: dict[str, object] = {
            "id": descriptor.name,
            "description": descriptor.description,
            "version": self._plugins[capability].version,
            "account_count": len(account_summaries),
            "operations": operation_names,
        }
        if include_accounts:
            summary["accounts"] = self._info_account_summaries(capability)
        return summary

    def _info_plugin(self, capability: str) -> dict[str, object]:
        self._require_capability(capability)
        summary = self._info_plugin_summary(capability, include_accounts=True)
        summary["kind"] = "plugin"
        summary["operations"] = [
            self._operation_summary(capability, operation)
            for operation in sorted(self._operations[capability])
        ]
        return summary

    def _info_account_summaries(self, capability: str) -> list[dict[str, object]]:
        self._require_capability(capability)
        summaries = self._account_summaries(capability)
        return [
            self._info_account_summary(capability, account_name, account)
            for account_name, account in sorted(summaries.items())
            if isinstance(account_name, str)
        ]

    def _info_account_summary(
        self,
        capability: str,
        account_name: str,
        account: object,
    ) -> dict[str, object]:
        description = ""
        guidance = ""
        if isinstance(account, Mapping):
            raw_description = account.get("description")
            if isinstance(raw_description, str):
                description = raw_description
            raw_guidance = account.get("guidance")
            if isinstance(raw_guidance, str):
                guidance = raw_guidance
        return {
            "plugin": capability,
            "name": account_name,
            "description": description,
            "guidance": guidance,
        }

    def _info_account(self, capability: str, account_name: str) -> dict[str, object]:
        self._require_capability(capability)
        accounts = self._account_summaries(capability)
        account = accounts.get(account_name)
        if account is None:
            raise ValueError(f"unknown account for {capability}: {account_name}")
        details: dict[str, object] = {
            "kind": "account",
            "plugin": capability,
            "account": account_name,
            "guidance": "",
        }
        if isinstance(account, Mapping):
            details.update(account)
            if "guidance" not in details:
                details["guidance"] = ""
        else:
            details["details"] = account
        return details

    def _info_plugin_tests(self, capability: str) -> dict[str, object]:
        self._require_capability(capability)
        account_summaries = self._account_summaries(capability)
        account_tests = self._account_tests(capability)
        account_names_set: set[str] = set()
        for account_name in account_summaries:
            if isinstance(account_name, str):
                account_names_set.add(account_name)
        for account_name in account_tests:
            if isinstance(account_name, str):
                account_names_set.add(account_name)
        account_names = sorted(account_names_set)
        return {
            "plugin": capability,
            "accounts": [
                self._info_account_test_summary(
                    capability,
                    account_name,
                    account_tests.get(
                        account_name,
                        {
                            "status": "skipped",
                            "reason": "account test did not return a result",
                        },
                    ),
                )
                for account_name in account_names
            ],
        }

    def _info_account_test(
        self,
        capability: str,
        account_name: str,
    ) -> dict[str, object]:
        self._require_capability(capability)
        account_summaries = self._account_summaries(capability)
        if account_name not in account_summaries:
            raise ValueError(f"unknown account for {capability}: {account_name}")
        account_tests = self._account_tests(capability)
        return {
            "kind": "test",
            **self._info_account_test_summary(
                capability,
                account_name,
                account_tests.get(
                    account_name,
                    {
                        "status": "skipped",
                        "reason": "account test did not return a result",
                    },
                ),
            ),
        }

    def _info_account_test_summary(
        self,
        capability: str,
        account_name: str,
        account_test: object,
    ) -> dict[str, object]:
        summary: dict[str, object] = {
            "plugin": capability,
            "account": account_name,
        }
        if isinstance(account_test, Mapping):
            summary.update(account_test)
        else:
            summary["status"] = "ok"
            summary["details"] = account_test
        status = summary.get("status", "ok")
        if not isinstance(status, str) or status not in ACCOUNT_TEST_STATUSES:
            raise RuntimeError(
                "account test status must be one of "
                f"{', '.join(sorted(ACCOUNT_TEST_STATUSES))}: "
                f"{capability}:{account_name}"
            )
        summary["status"] = status
        return summary

    def _capability_summary(
        self,
        capability: str,
        *,
        operation_preview_limit: int,
        account_preview_limit: int,
    ) -> dict[str, object]:
        descriptor = self._capabilities[capability]
        account_names = sorted(self._account_summaries(capability))
        operation_names = sorted(self._operations[capability])
        return {
            "id": descriptor.name,
            "description": descriptor.description,
            "version": self._plugins[capability].version,
            "account_count": len(account_names),
            "accounts": account_names[:account_preview_limit],
            "accounts_truncated": len(account_names) > account_preview_limit,
            "operation_count": len(operation_names),
            "operations": operation_names[:operation_preview_limit],
            "operations_truncated": len(operation_names) > operation_preview_limit,
        }

    def _operation_summary(
        self,
        capability: str,
        operation: str,
    ) -> dict[str, object]:
        descriptor = self._operations[capability][operation]
        return {
            "id": operation_id(capability, operation),
            "name": descriptor.name,
            "description": descriptor.description,
        }

    def _account_summaries(self, capability: str) -> Mapping[str, object]:
        runtime = self._context.runtimes.require_object(capability)
        account_summaries = getattr(runtime, "account_summaries", None)
        if not callable(account_summaries):
            return {}
        result = account_summaries()
        if not isinstance(result, Mapping):
            raise RuntimeError(
                f"capability account summaries must be a mapping: {capability}"
            )
        return result

    def _account_tests(self, capability: str) -> Mapping[str, object]:
        runtime = self._context.runtimes.require_object(capability)
        test_accounts = getattr(runtime, "test_accounts", None)
        if not callable(test_accounts):
            return {
                account_name: {
                    "status": "skipped",
                    "reason": "runtime does not implement account tests",
                }
                for account_name in self._account_summaries(capability)
            }
        result = test_accounts()
        if not isinstance(result, Mapping):
            raise RuntimeError(f"account tests must be a mapping: {capability}")
        return result


def operation_input_schema(schema: OperationInputSchema) -> dict[str, object]:
    if _is_dataclass_schema_type(schema):
        return _dataclass_input_schema(schema)
    raise TypeError(f"operation input schema must be a dataclass: {schema}")


def _is_dataclass_schema_type(value: object) -> bool:
    return isinstance(value, type) and is_dataclass(value)


def _dataclass_input_schema(schema_type: type[object]) -> dict[str, object]:
    type_hints = get_type_hints(schema_type)
    properties: dict[str, object] = {}
    required: list[str] = []
    for schema_field in fields(cast(Any, schema_type)):
        field_name = schema_field.name
        field_schema = _field_input_schema(
            type_hints.get(field_name, object),
            schema_field,
        )
        properties[field_name] = field_schema
        if _field_is_required(schema_field):
            required.append(field_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _field_input_schema(
    annotation: object,
    schema_field: Field[object],
) -> dict[str, object]:
    field_schema = _type_input_schema(annotation)
    description = schema_field.metadata.get("description")
    if isinstance(description, str):
        field_schema["description"] = description
    for metadata_key in ("minimum", "maximum"):
        metadata_value = schema_field.metadata.get(metadata_key)
        if isinstance(metadata_value, int):
            field_schema[metadata_key] = metadata_value
    default_value = _field_default(schema_field)
    if default_value is not MISSING and default_value is not None:
        field_schema["default"] = default_value
    return field_schema


def _type_input_schema(annotation: object) -> dict[str, object]:
    optional_type = _optional_inner_type(annotation)
    if optional_type is not None:
        return _type_input_schema(optional_type)

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        item_type = args[0] if args else object
        return {
            "type": "array",
            "items": _type_input_schema(item_type),
        }
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is bool:
        return {"type": "boolean"}
    raise TypeError(f"unsupported operation input field type: {annotation}")


def _optional_inner_type(annotation: object) -> object | None:
    origin = get_origin(annotation)
    if origin not in (UnionType, Union):
        return None
    args = get_args(annotation)
    non_none_args = tuple(arg for arg in args if arg is not type(None))
    if len(non_none_args) == 1 and len(non_none_args) != len(args):
        return non_none_args[0]
    return None


def _field_is_required(schema_field: Field[object]) -> bool:
    return schema_field.default is MISSING and schema_field.default_factory is MISSING


def _field_default(schema_field: Field[object]) -> object:
    if schema_field.default is not MISSING:
        return schema_field.default
    if schema_field.default_factory is not MISSING:
        return schema_field.default_factory()
    return MISSING


def _validate_operation_arguments(
    operation_ref: str,
    schema: OperationInputSchema,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    if _is_dataclass_schema_type(schema):
        return _validate_dataclass_operation_arguments(
            operation_ref,
            schema,
            arguments,
        )
    raise TypeError(f"operation input schema must be a dataclass: {schema}")


def _validate_dataclass_operation_arguments(
    operation_ref: str,
    schema_type: type[object],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    schema_fields = {
        schema_field.name: schema_field
        for schema_field in fields(cast(Any, schema_type))
    }
    missing = [
        field_name
        for field_name, schema_field in schema_fields.items()
        if _field_is_required(schema_field) and field_name not in arguments
    ]
    if missing:
        raise ValueError(
            f"{operation_ref} missing required argument(s): {', '.join(missing)}"
        )

    unknown = sorted(str(key) for key in arguments if key not in schema_fields)
    if unknown:
        raise ValueError(
            f"{operation_ref} received unknown argument(s): {', '.join(unknown)}"
        )

    try:
        argument_values = {key: value for key, value in arguments.items()}
        merged = OmegaConf.merge(OmegaConf.structured(schema_type), argument_values)
        operation_input = OmegaConf.to_object(merged)
    except OmegaConfBaseException as exc:
        raise ValueError(f"{operation_ref} invalid argument(s): {exc}") from exc
    if not is_dataclass(operation_input):
        raise RuntimeError(f"{operation_ref} input schema did not produce a dataclass")
    return asdict(operation_input)


ServicePluginFactory = Callable[[], ServicePlugin]
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "arbiter.services"
