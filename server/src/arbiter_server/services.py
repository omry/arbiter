from __future__ import annotations

from collections.abc import Callable, ItemsView, KeysView, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, TypeVar, cast

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


@dataclass(frozen=True)
class OperationDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, object]


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

    def bootstrap_config(self, *, kind: str, name: str) -> object | None: ...

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
            "input_schema": dict(descriptor.input_schema),
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
        operation_arguments = dict(arguments or {})
        _validate_operation_arguments(
            operation_id(capability, operation),
            descriptor.input_schema,
            operation_arguments,
        )
        return self._plugins[capability].invoke_operation(
            operation,
            operation_arguments,
            self._context,
        )

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
            "operation_count": len(operation_names),
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


def _validate_operation_arguments(
    operation_ref: str,
    schema: Mapping[str, object],
    arguments: Mapping[str, Any],
) -> None:
    required = schema.get("required", [])
    if isinstance(required, Sequence) and not isinstance(required, str):
        missing = [
            key for key in required if isinstance(key, str) and key not in arguments
        ]
        if missing:
            raise ValueError(
                f"{operation_ref} missing required argument(s): {', '.join(missing)}"
            )

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return

    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in arguments if key not in properties)
        if unknown:
            raise ValueError(
                f"{operation_ref} received unknown argument(s): {', '.join(unknown)}"
            )

    for name, value in arguments.items():
        property_schema = properties.get(name)
        if not isinstance(property_schema, Mapping):
            continue
        _validate_argument_value(operation_ref, str(name), property_schema, value)


def _validate_argument_value(
    operation_ref: str,
    name: str,
    schema: Mapping[str, object],
    value: object,
) -> None:
    expected_type = schema.get("type")
    if expected_type == "string":
        if not isinstance(value, str):
            _raise_argument_type_error(operation_ref, name, "string")
        return
    if expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            _raise_argument_type_error(operation_ref, name, "integer")
        integer_value = cast(int, value)
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and integer_value < minimum:
            raise ValueError(f"{operation_ref} argument {name} must be >= {minimum}")
        if isinstance(maximum, int) and integer_value > maximum:
            raise ValueError(f"{operation_ref} argument {name} must be <= {maximum}")
        return
    if expected_type == "boolean":
        if not isinstance(value, bool):
            _raise_argument_type_error(operation_ref, name, "boolean")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            _raise_argument_type_error(operation_ref, name, "array")
        list_value = cast(list[object], value)
        items = schema.get("items")
        if isinstance(items, Mapping) and items.get("type") == "string":
            for index, item in enumerate(list_value):
                if not isinstance(item, str):
                    raise ValueError(
                        f"{operation_ref} argument {name}[{index}] must be string"
                    )


def _raise_argument_type_error(
    operation_ref: str,
    name: str,
    expected_type: str,
) -> NoReturn:
    raise ValueError(f"{operation_ref} argument {name} must be {expected_type}")


ServicePluginFactory = Callable[[], ServicePlugin]
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "arbiter.services"
