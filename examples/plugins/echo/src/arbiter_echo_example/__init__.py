from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from hydra.core.config_store import ConfigStore

from arbiter_server.services import (
    CapabilityDescriptor,
    OperationDescriptor,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.version import distribution_version

from .config import (
    EchoConfig,
    EchoPolicyConfig,
    register_configs as register_echo_configs,
)

SERVER_API_VERSION = "0.9"
_RENAME_TEMPLATE_ERROR = (
    "arbiter-echo-example is a copyable template, not a production plugin name. "
    "Rename the distribution, import package, entry point, and service capability "
    "before using it outside the Arbiter repository example."
)

ECHO_MESSAGE_DESCRIPTION = (
    "Return a policy-checked echo response for the selected account."
)


@dataclass(frozen=True)
class EchoMessageInput:
    account: str = field(
        metadata={"description": "Configured echo account name."},
    )
    message: str = field(metadata={"description": "Message to echo back."})
    uppercase: bool = field(
        default=False,
        metadata={"description": "Return the echoed message in uppercase."},
    )


@dataclass(frozen=True)
class EchoMessageResult:
    tool: str
    account: str
    message: str


class EchoRuntime:
    service_name = "echo"

    def __init__(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> None:
        self._accounts = cast(Mapping[str, EchoConfig], accounts)
        self._policies = cast(Mapping[str, EchoPolicyConfig], policies)
        self._validate_config()

    def account_summaries(self) -> dict[str, object]:
        return {
            account_name: {
                "description": account.description,
                "guidance": account.guidance,
                "policy": account.policy,
                "enabled": True,
                "max_message_length": self._policies[account.policy].max_message_length,
            }
            for account_name, account in sorted(self._accounts.items())
        }

    def test_accounts(self) -> dict[str, object]:
        return {
            account_name: {
                "status": "ok",
                "stage": "config_validation",
                "checks": ["policy_reference", "policy_limits"],
            }
            for account_name in sorted(self._accounts)
        }

    def echo_message(
        self,
        *,
        account: str,
        message: str,
        uppercase: bool = False,
    ) -> EchoMessageResult:
        account_config = self._accounts.get(account)
        if account_config is None:
            raise ValueError(f"echo_message requires an echo account: {account}")

        policy = self._policies[account_config.policy]
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("echo_message requires a non-empty message")
        if len(normalized_message) > policy.max_message_length:
            raise ValueError(
                "echo_message exceeds max_message_length for account: " f"{account}"
            )
        if policy.allowed_prefixes and not any(
            normalized_message.startswith(prefix) for prefix in policy.allowed_prefixes
        ):
            raise ValueError(
                "echo_message message does not match an allowed prefix for account: "
                f"{account}"
            )

        if uppercase:
            normalized_message = normalized_message.upper()
        response = f"{account_config.prefix}{normalized_message}{account_config.suffix}"
        return EchoMessageResult(
            tool="echo_message",
            account=account,
            message=response,
        )

    def _validate_config(self) -> None:
        for account_name, account_config in sorted(self._accounts.items()):
            if account_config.policy not in self._policies:
                raise ValueError(
                    "echo account references an unknown policy: "
                    f"{account_name} -> {account_config.policy}"
                )
        for policy_name, policy in sorted(self._policies.items()):
            if policy.max_message_length < 1:
                raise ValueError(
                    "echo max_message_length must be positive: " f"{policy_name}"
                )


def _echo_account_bootstrap_template(*, name: str, policy_name: str) -> str:
    return f"""# @package arbiter.account.echo.{name}
defaults:
  - schema@_here_
  - _self_

# Human-facing summary shown by account listing tools.
description: Example echo account

# Operator guidance shown to agents during discovery.
guidance: Use this account for safe plugin wiring tests.

# Matching policy generated alongside this account.
policy: {policy_name}

# Text wrapped around echoed messages.
prefix: "echo: "
suffix: ""
"""


def _echo_policy_bootstrap_template(*, name: str) -> str:
    return f"""# @package arbiter.policy.echo.{name}
defaults:
  - schema@_here_
  - _self_

# Maximum accepted message length.
max_message_length: 200

# Empty list permits every prefix. Add values to require one of them.
allowed_prefixes: []
"""


class EchoServicePlugin:
    name = "echo"
    version = distribution_version("arbiter-echo-example", package_file=__file__)
    server_api_version = SERVER_API_VERSION

    def register_configs(self, config_store: ConfigStore) -> None:
        register_echo_configs(config_store)

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
        if kind == "account":
            return _echo_account_bootstrap_template(
                name=name,
                policy_name=f"{name}_policy",
            )
        if kind == "policy":
            return _echo_policy_bootstrap_template(name=name)
        return None

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> object:
        return EchoRuntime(accounts=accounts, policies=policies)

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name=self.name,
            description="Echo messages through configured example accounts.",
        )

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> tuple[OperationDescriptor, ...]:
        return (
            OperationDescriptor(
                name="echo_message",
                description=ECHO_MESSAGE_DESCRIPTION,
                input_schema=EchoMessageInput,
            ),
        )

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
        context: ServicePluginContext,
    ) -> object:
        if operation != "echo_message":
            raise ValueError(f"unknown echo operation: {operation}")

        runtime = context.runtimes.require(self.name, EchoRuntime)
        return runtime.echo_message(
            account=cast(str, arguments["account"]),
            message=cast(str, arguments["message"]),
            uppercase=cast(bool, arguments.get("uppercase", False)),
        )


def _is_repo_example_plugin(package_file: str) -> bool:
    package_dir = Path(package_file).resolve().parent
    expected_suffix = Path("examples/plugins/echo/src/arbiter_echo_example")
    if package_dir.parts[-len(expected_suffix.parts) :] != expected_suffix.parts:
        return False

    for parent in package_dir.parents:
        if (parent / "server/src/arbiter_server").is_dir() and (
            parent / "examples/plugins/echo/pyproject.toml"
        ).is_file():
            return True
    return False


def _ensure_template_not_copied(package_file: str) -> None:
    if not _is_repo_example_plugin(package_file):
        raise RuntimeError(_RENAME_TEMPLATE_ERROR)


def plugin() -> EchoServicePlugin:
    _ensure_template_not_copied(__file__)
    return EchoServicePlugin()
