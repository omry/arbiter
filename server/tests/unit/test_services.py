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
