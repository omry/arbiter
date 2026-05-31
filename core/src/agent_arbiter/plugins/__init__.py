from __future__ import annotations

from importlib.metadata import entry_points
import logging
from typing import cast

from ..services import (
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    ServicePlugin,
    ServicePluginFactory,
    validate_service_plugin_compatibility,
)


LOGGER = logging.getLogger(__name__)


def discover_service_plugins(
    group: str = SERVICE_PLUGIN_ENTRY_POINT_GROUP,
) -> list[ServicePlugin]:
    discovered: list[ServicePlugin] = []
    for entry_point in entry_points().select(group=group):
        try:
            plugin_factory = cast(ServicePluginFactory, entry_point.load())
        except ModuleNotFoundError as exc:
            LOGGER.warning(
                "Skipping unavailable service plugin entry point %s=%s: %s",
                entry_point.name,
                entry_point.value,
                exc,
            )
            continue
        service_plugin = plugin_factory()
        validate_service_plugin_compatibility(service_plugin)
        discovered.append(service_plugin)
    return sorted(discovered, key=lambda plugin: plugin.name)
