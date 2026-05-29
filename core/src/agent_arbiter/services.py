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


class ServicePlugin(Protocol):
    name: str

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

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None: ...


ServicePluginFactory = Callable[[], ServicePlugin]
SERVICE_PLUGIN_ENTRY_POINT_GROUP = "agent_arbiter.services"
