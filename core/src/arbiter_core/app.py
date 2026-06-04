from __future__ import annotations

from typing import Protocol, cast

from .services import RuntimeRegistry


CORE_TOOL_NAMES = (
    "info",
    "version_info",
    "list_caps",
    "describe_caps",
    "describe_cap",
    "describe_op",
    "run_op",
)


class AccountSummariesRuntime(Protocol):
    def account_summaries(self) -> dict[str, object]: ...


class ArbiterApp:
    """Core facade over entry-point supplied service runtimes."""

    def __init__(
        self,
        runtime_registry: RuntimeRegistry,
    ) -> None:
        self.runtime_registry = runtime_registry

    def tool_names(self) -> list[str]:
        return list(CORE_TOOL_NAMES)

    def list_accounts(self) -> dict[str, object]:
        summaries: dict[str, object] = {}
        for service_name, runtime in sorted(self.runtime_registry.items()):
            if not hasattr(runtime, "account_summaries"):
                continue
            summaries[service_name] = cast(
                AccountSummariesRuntime,
                runtime,
            ).account_summaries()
        return summaries
