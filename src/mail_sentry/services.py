from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ServicePluginContext:
    runtimes: RuntimeRegistry


class ServicePlugin(Protocol):
    name: str

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None: ...


ServicePluginFactory = Callable[[], ServicePlugin]
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "mail_sentry.services"
