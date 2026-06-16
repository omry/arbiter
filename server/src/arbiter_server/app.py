from __future__ import annotations

from typing import Protocol, cast

from .services import RuntimeRegistry


class AccountSummariesRuntime(Protocol):
    def account_summaries(self) -> dict[str, object]: ...


class ArbiterApp:
    """Server facade over entry-point supplied service runtimes."""

    def __init__(
        self,
        runtime_registry: RuntimeRegistry,
    ) -> None:
        self.runtime_registry = runtime_registry

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
