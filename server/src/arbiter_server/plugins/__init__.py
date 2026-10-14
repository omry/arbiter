from __future__ import annotations

from importlib.metadata import entry_points
from collections.abc import Collection
import logging
from typing import cast

from ..services import (
    SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    ServicePlugin,
    ServicePluginFactory,
    validate_service_plugin_compatibility,
)


LOGGER = logging.getLogger(__name__)


def installed_service_names() -> list[str]:
    """List advertised plugin names without executing their entry points."""
    return sorted(
        point.name
        for point in entry_points().select(group=SERVICE_PLUGIN_ENTRY_POINT_GROUP)
    )


def discover_service_plugins(
    group: str = SERVICE_PLUGIN_ENTRY_POINT_GROUP,
    *,
    names: Collection[str] | None = None,
) -> list[ServicePlugin]:
    discovered: list[ServicePlugin] = []
    for entry_point in entry_points().select(group=group):
        if names is not None and entry_point.name not in names:
            continue
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
        if service_plugin.name != entry_point.name:
            raise RuntimeError(
                f"service plugin entry point {entry_point.name} returned "
                f"plugin {service_plugin.name}"
            )
        discovered.append(service_plugin)
    return sorted(discovered, key=lambda plugin: plugin.name)
