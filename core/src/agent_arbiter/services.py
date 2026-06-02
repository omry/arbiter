from __future__ import annotations

from collections.abc import Callable, ItemsView, KeysView, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, TypeVar

from .version import arbiter_core_version, compatibility_line, core_api_version


RuntimeT = TypeVar("RuntimeT")
CORE_VERSION = arbiter_core_version()
CORE_API_VERSION = core_api_version()


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
    core_api_version: str

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
    core_api_version: str


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
        plugin_core_api_version = service_plugin.core_api_version
    except AttributeError as exc:
        raise RuntimeError(
            f"service plugin {service_plugin.name} does not declare "
            "an Arbiter core API version"
        ) from exc

    return ServicePluginRuntimeInfo(
        name=service_plugin.name,
        version=plugin_version,
        core_api_version=plugin_core_api_version,
    )


def validate_service_plugin_compatibility(
    service_plugin: ServicePlugin,
) -> None:
    info = service_plugin_runtime_info(service_plugin)
    if info.core_api_version != CORE_API_VERSION:
        raise RuntimeError(
            f"service plugin {info.name} targets Arbiter core API "
            f"{info.core_api_version}, but loaded core API is {CORE_API_VERSION}"
        )

    plugin_line = compatibility_line(info.version)
    if plugin_line != CORE_API_VERSION:
        raise RuntimeError(
            f"service plugin {info.name} version {info.version} is not on "
            f"loaded core API line {CORE_API_VERSION}"
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
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise ValueError(f"{operation_ref} argument {name} must be >= {minimum}")
        if isinstance(maximum, int) and value > maximum:
            raise ValueError(f"{operation_ref} argument {name} must be <= {maximum}")
        return
    if expected_type == "boolean":
        if not isinstance(value, bool):
            _raise_argument_type_error(operation_ref, name, "boolean")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            _raise_argument_type_error(operation_ref, name, "array")
        items = schema.get("items")
        if isinstance(items, Mapping) and items.get("type") == "string":
            for index, item in enumerate(value):
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
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "agent_arbiter.services"
