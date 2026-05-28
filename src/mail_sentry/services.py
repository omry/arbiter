from __future__ import annotations

from collections.abc import Callable, ItemsView, KeysView, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar


RuntimeT = TypeVar("RuntimeT")


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

    def require(self, service_name: str, runtime_type: type[RuntimeT]) -> RuntimeT:
        runtime = self.runtimes.get(service_name)
        if not isinstance(runtime, runtime_type):
            raise RuntimeError(f"service runtime is not configured: {service_name}")
        return runtime

    def items(self) -> ItemsView[str, object]:
        return self.runtimes.items()

    def keys(self) -> KeysView[str]:
        return self.runtimes.keys()


@dataclass(frozen=True)
class ServiceRuntimeContext:
    mail_config: Any
    dependencies: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ServicePluginContext:
    runtimes: RuntimeRegistry


class ServicePlugin(Protocol):
    name: str

    def build_runtime(
        self,
        config: object,
        context: ServiceRuntimeContext,
    ) -> object: ...

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None: ...


ServicePluginFactory = Callable[[], ServicePlugin]
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "mail_sentry.services"
