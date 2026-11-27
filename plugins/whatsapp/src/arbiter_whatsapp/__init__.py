from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from time import monotonic, sleep
from typing import cast

from arbiter_server.errors import OperationError
from arbiter_server.services import (
    SERVER_API_VERSION,
    CapabilityDescriptor,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.storage import PluginStorage
from arbiter_server.version import distribution_version

from .config import EFFECTS, WhatsAppPolicy, plain, register_configs, validate_config
from .credentials import AccountAdministrator, BindingStore, update_page
from .state import StateStore, reference_key
from .transport import ProviderClient, failure
from .updates import RECEIVE_UPDATES, check_receive, receive_updates


class WhatsAppRuntime:
    def __init__(self, accounts, policies, context: ServiceRuntimeContext):
        self.accounts, self.policies = validate_config(accounts, policies)
        storage = context.dependencies.get("plugin_storage")
        if not isinstance(storage, PluginStorage):
            raise failure("plugin_error", "private_state_unavailable")
        self.state = StateStore(storage)
        self.bindings = BindingStore(self.state)
        self.clock = cast(
            Callable[[], float], context.dependencies.get("whatsapp_clock", monotonic)
        )
        self.wait = cast(
            Callable[[float], None], context.dependencies.get("whatsapp_wait", sleep)
        )
        self.client = cast(
            ProviderClient, context.dependencies.get("whatsapp_client")
        ) or ProviderClient(clock=self.clock)

    def account(self, account: str):
        if account not in self.accounts:
            raise failure("validation_error", "unknown_account")
        selected = self.accounts[account]
        return selected, self.policies[selected.policy]

    def authorize(self, operation: str, account: str, *, media: bool = False):
        if operation not in EFFECTS:
            raise failure("validation_error", "unknown_operation")
        selected, policy = self.account(account)
        effects = EFFECTS[operation] + (
            ("media_egress",) if operation == "send_message" and media else ()
        )
        if not all(getattr(policy, effect) for effect in effects):
            raise failure("policy_denied", "operation_denied")
        return self.bindings.snapshot(selected.credential)

    def test_accounts(self, *, progress=None):
        result = {}
        for name, account in self.accounts.items():
            if progress is not None:
                progress(name)
            try:
                # Readiness never initializes an absent cursor or creates a challenge.
                self.authorize("receive_updates", name)
                with self.state.lock(reference_key(account.credential) + ".poll"):
                    with self.state.lock("root-binding"):
                        self.bindings.bound_identities()
                        binding = self.bindings.snapshot(account.credential)
                        with self.state.database() as db:
                            offset = db.execute(
                                "SELECT normal_offset FROM agents WHERE provider_id=?",
                                (binding.provider_id,),
                            ).fetchone()[0]
                    response = self.client.request(
                        "GET",
                        "/agent/v1/updates",
                        binding.token,
                        query={
                            "offset": offset if offset is not None else 0,
                            "limit": 100,
                            "timeout": 0,
                        },
                        deadline=self.clock() + 30,
                        response_limit=4 * 1024 * 1024,
                    )
                    provider_id, _, _ = update_page(
                        response, offset if offset is not None else 0
                    )
                    if provider_id is not None and provider_id != binding.provider_id:
                        raise failure("upstream_protocol_error", "identity_changed")
                result[name] = {"status": "ok"}
            except OperationError as error:
                result[name] = {"status": "failed", "message": error.message}
        return result


class WhatsAppServicePlugin:
    name = "whatsapp"
    version = distribution_version("arbiter-whatsapp", package_file=__file__)
    server_api_version = SERVER_API_VERSION
    activation_guidance = "Enroll this account explicitly before use. Policies are shared by all local callers."

    def register_configs(self, config_store):
        register_configs(config_store)

    def bootstrap_config(self, *, kind: str, name: str):
        if kind == "account":
            return f"# @package arbiter.account.whatsapp.{name}\ndefaults:\n  - schema@_here_\n  - _self_\n\ncredential: whatsapp/{name}\npolicy: {name}_policy\n"
        if kind == "policy":
            return (
                f"# @package arbiter.policy.whatsapp.{name}\ndefaults:\n  - schema@_here_\n  - _self_\n\n"
                + "\n".join(f"{key}: true" for key in asdict(WhatsAppPolicy()))
                + "\n"
            )
        return None

    def check_config(self, *, accounts, policies):
        validate_config(accounts, policies)

    def build_runtime(self, accounts, policies, context):
        return WhatsAppRuntime(accounts, policies, context)

    def public_account_config(self, config: object) -> Mapping[str, object]:
        values = plain(config)
        return {"credential": values.get("credential"), "policy": values.get("policy")}

    def public_policy_config(self, config: object) -> Mapping[str, object]:
        return plain(config)

    def describe_capability(self, context: ServicePluginContext):
        return CapabilityDescriptor(
            "whatsapp", "WhatsApp Agent Platform account operations."
        )

    def describe_operations(self, context: ServicePluginContext):
        if self.name not in context.runtimes.keys():
            return ()
        return (RECEIVE_UPDATES,)

    def invoke_operation(self, operation, arguments, context):
        if operation != "receive_updates":
            raise failure("validation_error", "unknown_operation")
        runtime = context.runtimes.require(self.name, WhatsAppRuntime)
        return receive_updates(runtime, **arguments)

    def check_operation(self, operation, arguments, context):
        if operation != "receive_updates":
            raise failure("validation_error", "unknown_operation")
        runtime = context.runtimes.require(self.name, WhatsAppRuntime)
        return check_receive(
            runtime,
            arguments["account"],
            arguments.get("offset"),
            arguments.get("replay", False),
        )

    def administer_account(
        self,
        *,
        action,
        account,
        resolved_accounts,
        resolved_policies,
        runtime_context,
        secret_input,
        challenge_output,
    ):
        runtime = self.build_runtime(
            resolved_accounts, resolved_policies, runtime_context
        )
        selected, _ = runtime.account(account)
        admin = AccountAdministrator(
            runtime.bindings,
            runtime.client,
            clock=runtime.clock,
            wait=runtime.wait,
        )
        return admin.run(action, selected.credential, secret_input, challenge_output)


def plugin():
    return WhatsAppServicePlugin()
