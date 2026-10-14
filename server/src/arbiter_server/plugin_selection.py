"""Select plugins from Hydra's raw defaults graph, before full composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import cast

from hydra._internal.config_loader_impl import ConfigLoaderImpl
from hydra._internal.defaults_list import create_defaults_list
from hydra.core.default_element import DefaultsTreeNode
from hydra.errors import MissingConfigException
from hydra.core.global_hydra import GlobalHydra
from hydra.core.override_parser.overrides_parser import OverridesParser
from omegaconf import DictConfig, OmegaConf

from .config import register_configs
from .plugins import installed_service_names


def _path_service(path: str) -> str | None:
    parts = PurePosixPath(path.lstrip("/")).parts
    if len(parts) >= 3 and parts[:2] in {
        ("arbiter", "account"),
        ("arbiter", "policy"),
    }:
        return parts[2]
    return None


def selected_service_names(
    *,
    config_name: str,
    overrides: Sequence[str],
) -> list[str]:
    """Use the initialized Hydra loader; do not compose plugin configuration.

    Missing selected schemas identify their plugin. Optional selected schemas
    are inspected from Hydra's defaults tree. Each plugin loads once per pass.
    Defaults, overrides, search paths and packages remain Hydra's responsibility.
    This isolated adapter uses Hydra 1.3's repository API because the public
    defaults-list method does not expose the raw repository it selected from.
    """
    loader = cast(ConfigLoaderImpl, GlobalHydra.instance().config_loader())
    loader.ensure_main_config_source_available()
    installed = set(installed_service_names())
    loaded: set[str] = set()
    needed: set[str] = set()
    parsed = OverridesParser.create().parse_overrides(list(overrides))

    def activate(names: set[str]) -> None:
        fresh = names - loaded
        if fresh:
            register_configs(sorted(fresh))
            loaded.update(fresh)

    while True:
        _, repo = loader._parse_overrides_and_create_caching_repo(
            config_name, list(overrides)
        )
        try:
            defaults = create_defaults_list(
                repo=repo,
                config_name=config_name,
                overrides_list=parsed,
                prepend_hydra=True,
                skip_missing=False,
            )
        except MissingConfigException as exc:
            service = _path_service(exc.missing_cfg_file or "")
            if service is None or service not in installed or service in loaded:
                raise
            needed.add(service)
            activate({service})
            continue

        optional_services: set[str] = set()

        def collect_optional(tree: DefaultsTreeNode) -> None:
            node = tree.node
            if not node.is_virtual() and node.get_name() is not None:
                if node.is_optional() and not defaults.overrides.is_deleted(node):
                    service = _path_service(node.get_config_path())
                    if service is not None and service in installed:
                        optional_services.add(service)
            for child in tree.children or []:
                if isinstance(child, DefaultsTreeNode):
                    collect_optional(child)

        collect_optional(defaults.defaults_tree)
        fresh = optional_services - loaded
        if fresh:
            needed.update(fresh)
            activate(fresh)
            continue
        break

    inline: DictConfig = OmegaConf.create({"arbiter": {"account": {}, "policy": {}}})

    def collect(value: object, package: str) -> None:
        if package == "arbiter" and not isinstance(value, Mapping):
            # An unresolved replacement of the arbiter ancestor discards all
            # earlier account and policy data in native Hydra composition.
            OmegaConf.update(inline, package, value, merge=False, force_add=True)
            return
        if package in {"arbiter.account", "arbiter.policy"} or package.startswith(
            ("arbiter.account.", "arbiter.policy.")
        ):
            # A scalar at one of these paths is often an interpolation to a
            # map.  It replaces the raw shadow at that path; retaining an
            # earlier inline map would select a plugin that native Hydra
            # discards after resolving the interpolation.
            OmegaConf.update(
                inline,
                package,
                dict(value) if isinstance(value, Mapping) else value,
                merge=isinstance(value, Mapping),
                force_add=True,
            )
            return
        if not isinstance(value, Mapping):
            return
        for key, child in value.items():
            if key != "defaults":
                collect(child, f"{package}.{key}".strip("."))

    for default in defaults.defaults:
        path = default.config_path
        if path is None or path.startswith("hydra/"):
            continue
        service = _path_service(path)
        if service is not None:
            needed.add(service)
        result = repo.load_config(path)
        if result is not None:
            collect(
                OmegaConf.to_container(result.config, resolve=False),
                default.package or "",
            )

    for override in parsed:
        key = override.key_or_group
        if "/" in key:
            continue
        if key.startswith(("arbiter.account", "arbiter.policy")):
            if override.is_delete():
                parent, _, leaf = key.rpartition(".")
                raw = OmegaConf.to_container(inline, resolve=False)
                node: object = raw
                for component in parent.split("."):
                    if not isinstance(node, Mapping):
                        break
                    node = node.get(component)
                else:
                    if isinstance(node, dict) and leaf in node:
                        # Recreate the shadow after mutating its unresolved
                        # container.  Traversing an interpolation here would
                        # resolve an incomplete shadow and diverge from Hydra.
                        del node[leaf]
                        inline = cast(DictConfig, OmegaConf.create(raw))
            else:
                OmegaConf.update(
                    inline, key, override.value(), merge=True, force_add=True
                )
        elif not override.is_delete():
            collect(override.value(), key)
    raw_inline = OmegaConf.to_container(inline, resolve=False)
    assert isinstance(raw_inline, dict)
    raw_arbiter = raw_inline.get("arbiter")
    if isinstance(raw_arbiter, Mapping):
        for domain in ("account", "policy"):
            services = raw_arbiter.get(domain, {})
            if isinstance(services, dict):
                needed.update(
                    str(name)
                    for name, objects in services.items()
                    if isinstance(objects, Mapping)
                    and objects
                    and not str(name).startswith("_")
                )
    activate(needed)
    return sorted(needed)
