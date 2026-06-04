from __future__ import annotations

from dataclasses import dataclass, field

from hydra.core.config_store import ConfigStore

from arbiter_core.config import Policy


@dataclass
class EchoConfig(Policy):
    policy: str = "example_policy"
    description: str = ""
    guidance: str = ""
    prefix: str = "echo: "
    suffix: str = ""


@dataclass
class EchoPolicyConfig(Policy):
    max_message_length: int = 200
    allowed_prefixes: list[str] = field(default_factory=list)


ECHO_ACCOUNT_EXAMPLE = EchoConfig(
    policy="example_policy",
    description="Example echo account",
    guidance="Use this account for safe plugin wiring tests.",
    prefix="echo: ",
    suffix="",
)

ECHO_POLICY_EXAMPLE = EchoPolicyConfig(
    max_message_length=200,
    allowed_prefixes=[],
)


def register_configs(config_store: ConfigStore) -> None:
    config_store.store(
        group="arbiter/account/echo",
        name="schema",
        node=EchoConfig,
        provider="arbiter-echo-example",
    )
    config_store.store(
        group="arbiter/account/echo",
        name="example",
        node=ECHO_ACCOUNT_EXAMPLE,
        provider="arbiter-echo-example",
    )
    config_store.store(
        group="arbiter/policy/echo",
        name="schema",
        node=EchoPolicyConfig,
        provider="arbiter-echo-example",
    )
    config_store.store(
        group="arbiter/policy/echo",
        name="example",
        node=ECHO_POLICY_EXAMPLE,
        provider="arbiter-echo-example",
    )
