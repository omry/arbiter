from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from arbiter_server.services import (
    CapabilityDescriptor,
    OperationCatalog,
    OperationDescriptor,
    RuntimeRegistry,
    ServicePluginContext,
    ServiceRuntimeContext,
    ServicePlugin,
    operation_input_schema,
)


@dataclass(frozen=True)
class ExampleInput:
    name: str = field(metadata={"description": "Item name."})
    limit: int = field(
        default=10,
        metadata={
            "description": "Maximum items.",
            "minimum": 1,
            "maximum": 100,
        },
    )
    enabled: bool = field(
        default=True,
        metadata={"description": "Whether the operation is enabled."},
    )
    tags: list[str] = field(
        default_factory=list,
        metadata={"description": "Tags to attach."},
    )


class ExamplePlugin(ServicePlugin):
    name = "example"
    version = "0.9.0"
    server_api_version = "0.9"

    def register_configs(self, config_store: object) -> None:
        return None

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
        return None

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> object:
        return object()

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name=self.name,
            description="Example plugin.",
        )

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> tuple[OperationDescriptor, ...]:
        return (
            OperationDescriptor(
                name="run",
                description="Run an example operation.",
                input_schema=ExampleInput,
            ),
        )

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        context: ServicePluginContext,
    ) -> object:
        return dict(arguments)


def test_dataclass_operation_input_schema_describes_client_schema() -> None:
    assert operation_input_schema(ExampleInput) == {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Item name.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum items.",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether the operation is enabled.",
                "default": True,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to attach.",
                "default": [],
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }


def test_operation_catalog_validates_and_coerces_dataclass_input() -> None:
    plugin = ExamplePlugin()
    catalog = OperationCatalog(
        [plugin],
        ServicePluginContext(runtimes=RuntimeRegistry({})),
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )

    assert catalog.invoke_operation(
        "example:run",
        {"name": "demo", "limit": "5", "enabled": "false"},
    ) == {
        "name": "demo",
        "limit": 5,
        "enabled": False,
        "tags": [],
    }


def test_operation_catalog_rejects_invalid_dataclass_input() -> None:
    plugin = ExamplePlugin()
    catalog = OperationCatalog(
        [plugin],
        ServicePluginContext(runtimes=RuntimeRegistry({})),
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )

    with pytest.raises(
        ValueError,
        match="example:run missing required argument\\(s\\): name",
    ):
        catalog.invoke_operation("example:run", {"limit": 5})

    with pytest.raises(
        ValueError,
        match="example:run received unknown argument\\(s\\): extra",
    ):
        catalog.invoke_operation("example:run", {"name": "demo", "extra": True})

    with pytest.raises(ValueError, match="example:run invalid argument"):
        catalog.invoke_operation("example:run", {"name": "demo", "limit": "many"})


@dataclass(frozen=True)
class NestedPayload:
    body: str = field(metadata={"description": "Payload text."})
    preview: bool = False


@dataclass(frozen=True)
class NestedInput:
    payload: NestedPayload
    quote: NestedPayload | None = None
    items: list[NestedPayload] = field(default_factory=list)


@dataclass(frozen=True)
class RecursiveInput:
    child: RecursiveInput | None = None


def test_nested_catalog_schema_preserves_fields_requiredness_and_null():
    schema: Any = operation_input_schema(NestedInput)
    nested = schema["properties"]["payload"]
    assert nested == {
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "Payload text."},
            "preview": {"type": "boolean", "default": False},
        },
        "required": ["body"],
        "additionalProperties": False,
    }
    assert schema["required"] == ["payload"]
    assert schema["properties"]["quote"] == {"anyOf": [nested, {"type": "null"}]}
    assert schema["properties"]["items"]["items"] == nested


def test_recursive_and_excessively_deep_schemas_reject_before_publication():
    from dataclasses import make_dataclass

    with pytest.raises(TypeError, match="acyclic"):
        operation_input_schema(RecursiveInput)
    schema = NestedPayload
    for index in range(7):
        schema = make_dataclass(f"Level{index}", [("child", schema)])
    operation_input_schema(schema)
    schema = make_dataclass("TooDeep", [("child", schema)])
    with pytest.raises(TypeError, match="8 objects"):
        operation_input_schema(schema)


def test_existing_validator_accepts_nested_dataclasses_and_rejects_unknown_fields():
    from arbiter_server.services import _validate_operation_arguments
    from arbiter_server.errors import OperationValidationError

    assert _validate_operation_arguments(
        "example:nested", NestedInput, {"payload": {"body": "ok"}}
    ) == {
        "payload": {"body": "ok", "preview": False},
        "quote": None,
        "items": [],
    }
    for arguments in [
        {"payload": {"body": "ok", "unexpected": True}},
        {"payload": {"body": "ok"}, "quote": {"unexpected": "bad"}},
        {"payload": {"body": "ok"}, "items": [{"body": "ok", "unexpected": True}]},
    ]:
        with pytest.raises(OperationValidationError):
            _validate_operation_arguments("example:nested", NestedInput, arguments)
