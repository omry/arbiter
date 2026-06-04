from __future__ import annotations

from pathlib import Path
import sys

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT / "src"))

from arbiter_core.services import (  # noqa: E402
    OperationCatalog,
    RuntimeRegistry,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_echo_example import (  # noqa: E402
    EchoMessageResult,
    EchoRuntime,
    _ensure_template_not_copied,
    plugin,
)
from arbiter_echo_example.config import EchoConfig, EchoPolicyConfig  # noqa: E402


def test_echo_runtime_enforces_policy() -> None:
    runtime = EchoRuntime(
        accounts={
            "demo": EchoConfig(
                policy="limited",
                prefix="[",
                suffix="]",
            )
        },
        policies={
            "limited": EchoPolicyConfig(
                max_message_length=20,
                allowed_prefixes=["ticket:"],
            )
        },
    )

    result = runtime.echo_message(
        account="demo",
        message="ticket: hello",
        uppercase=True,
    )

    assert result == EchoMessageResult(
        tool="echo_message",
        account="demo",
        message="[TICKET: HELLO]",
    )
    with pytest.raises(ValueError, match="allowed prefix"):
        runtime.echo_message(account="demo", message="note: hello")


def test_echo_plugin_describes_and_invokes_operation() -> None:
    service_plugin = plugin()
    runtime = service_plugin.build_runtime(
        accounts={"demo": EchoConfig(description="Demo account")},
        policies={"example_policy": EchoPolicyConfig()},
        context=ServiceRuntimeContext(),
    )
    context = ServicePluginContext(
        runtimes=RuntimeRegistry({service_plugin.name: runtime})
    )
    catalog = OperationCatalog(
        [service_plugin],
        context,
        max_account_preview_limit=8,
        max_operation_preview_limit=8,
    )

    assert catalog.list_capabilities() == {"capabilities": ["echo"]}
    assert catalog.describe_operation("echo:echo_message")["input_schema"] == {
        "type": "object",
        "properties": {
            "account": {
                "type": "string",
                "description": "Configured echo account name.",
            },
            "message": {
                "type": "string",
                "description": "Message to echo back.",
            },
            "uppercase": {
                "type": "boolean",
                "description": "Return the echoed message in uppercase.",
            },
        },
        "required": ["account", "message"],
        "additionalProperties": False,
    }
    assert catalog.info(kind="account", plugin="echo", account="demo") == {
        "kind": "account",
        "plugin": "echo",
        "account": "demo",
        "guidance": "",
        "description": "Demo account",
        "policy": "example_policy",
        "enabled": True,
        "max_message_length": 200,
    }

    result = catalog.invoke_operation(
        "echo:echo_message",
        {"account": "demo", "message": "hello"},
    )

    assert result == EchoMessageResult(
        tool="echo_message",
        account="demo",
        message="echo: hello",
    )


def test_echo_plugin_rejects_copied_template() -> None:
    with pytest.raises(RuntimeError, match="Rename the distribution"):
        _ensure_template_not_copied(
            "/tmp/copied-plugin/src/arbiter_echo_example/__init__.py"
        )
