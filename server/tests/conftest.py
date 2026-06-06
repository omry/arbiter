from __future__ import annotations

import os

import pytest


_POSIX_DOCKER_HELPER_PREFIXES = (
    "test_cli_deploy_docker_generated_helper_",
    "test_cli_deploy_docker_helper_down_",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.name != "nt":
        return

    marker = pytest.mark.skip(
        reason="generated Docker helper execution requires a POSIX shell"
    )
    for item in items:
        if any(
            item.name.startswith(prefix) for prefix in _POSIX_DOCKER_HELPER_PREFIXES
        ):
            item.add_marker(marker)
