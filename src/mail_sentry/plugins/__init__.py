from __future__ import annotations

from importlib.metadata import entry_points
from typing import cast

from ..services import (
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    ServicePlugin,
    ServicePluginFactory,
)


def discover_service_plugins(
    group: str = SERVICE_PLUGIN_ENTRY_POINT_GROUP,
) -> list[ServicePlugin]:
    discovered: list[ServicePlugin] = []
    for entry_point in entry_points().select(group=group):
        plugin_factory = cast(ServicePluginFactory, entry_point.load())
        discovered.append(plugin_factory())
    return sorted(discovered, key=lambda plugin: plugin.name)
