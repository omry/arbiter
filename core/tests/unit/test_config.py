from pathlib import Path
from typing import cast

import pytest
from hydra import compose, initialize_config_dir, initialize_config_module
from hydra.errors import ConfigCompositionException
from omegaconf import DictConfig, OmegaConf

from agent_arbiter.config import (
    AppConfig,
    configured_service_names,
    register_configs,
    service_accounts_for,
    service_policies_for,
)
from agent_arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfirmationAction,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    register_configs as register_imap_configs,
)
from agent_arbiter_smtp.config import (
    MailTlsMode as SMTPMailTlsMode,
    SMTPConfig,
    SMTPServicePolicyConfig,
    register_configs as register_smtp_configs,
)


def _register_all_configs() -> None:
    register_configs()
    from hydra.core.config_store import ConfigStore

    config_store = ConfigStore.instance()
    register_smtp_configs(config_store)
    register_imap_configs(config_store)


def _compose_config(overrides: list[str] | None = None) -> DictConfig:
    _register_all_configs()
    with initialize_config_module(
        version_base=None,
        config_module="agent_arbiter.conf",
    ):
        return compose(config_name="config", overrides=overrides or [])


def test_compose_config_returns_hydra_config() -> None:
    cfg = _compose_config()

    assert isinstance(cfg, DictConfig)
    assert cfg.server.name == "agent-arbiter"
    assert cfg.server.transport == "streamable-http"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.server.path == "/mcp"
    assert cfg.accounts == {}
    assert cfg.policies == {}
    assert cfg.etc == {}


def test_compose_config_applies_overrides() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@accounts.smtp.primary=schema",
            "+arbiter/policy/smtp@policies.smtp.bot=schema",
            "+arbiter/policy/imap@policies.imap.bot=schema",
            "server.transport=stdio",
            "server.port=9000",
            "accounts.smtp.primary.host=smtp.example.com",
            "accounts.smtp.primary.port=2525",
            "accounts.smtp.primary.from_name=Agent Team",
            "accounts.smtp.primary.tls=implicit",
            "accounts.smtp.primary.verify_peer=false",
            "policies.smtp.bot.require_confirmation=true",
            "policies.imap.bot.system_flags.seen=read_write",
        ]
    )

    assert cfg.server.transport == "stdio"
    assert cfg.server.port == 9000
    assert cfg.accounts.smtp.primary.host == "smtp.example.com"
    assert cfg.accounts.smtp.primary.port == 2525
    assert cfg.accounts.smtp.primary.from_name == "Agent Team"
    assert cfg.accounts.smtp.primary.tls == SMTPMailTlsMode.implicit
    assert cfg.accounts.smtp.primary.verify_peer is False
    assert cfg.policies.smtp.bot.require_confirmation is True
    assert cfg.policies.imap.bot.system_flags.seen == IMAPFlagMode.read_write


def test_hydra_config_preserves_lazy_interpolations() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@accounts.smtp.primary=schema",
            "+arbiter/policy/smtp@policies.smtp.bot=schema",
            "accounts.smtp.primary.from_name=${server.name}",
        ]
    )

    assert cfg.server.name == "agent-arbiter"
    assert cfg.accounts.smtp.primary.from_name == "agent-arbiter"


def test_mailgateway_schema_alias_composes(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
defaults:
  - mailgateway_app_config_schema
  - _self_

server:
  name: agent-arbiter-mcp
accounts:
  smtp:
    primary:
      policy: bot
      description: Bot account.
      host: localhost
      authenticate: false
      username: ""
      password: ""
      from_email: bot@example.com
  imap: {}
policies:
  smtp:
    bot:
      require_confirmation: false
  imap: {}
""",
        encoding="utf-8",
    )

    _register_all_configs()
    with initialize_config_dir(version_base=None, config_dir=str(tmp_path)):
        cfg = compose(config_name="config")

    assert cfg.server.name == "agent-arbiter-mcp"
    assert cfg.policies.smtp.bot.require_confirmation is False
    assert cfg.accounts.smtp.primary.from_email == "bot@example.com"


def test_standard_deployment_config_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_ARBITER_BOT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AGENT_ARBITER_BOT_SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("AGENT_ARBITER_BOT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("AGENT_ARBITER_BOT_SMTP_FROM_EMAIL", "bot@example.com")
    monkeypatch.setenv("AGENT_ARBITER_BOT_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("AGENT_ARBITER_BOT_IMAP_USERNAME", "bot@example.com")
    monkeypatch.setenv("AGENT_ARBITER_BOT_IMAP_PASSWORD", "secret")
    monkeypatch.setenv("AGENT_ARBITER_PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AGENT_ARBITER_PERSONAL_SMTP_USERNAME", "omry@example.com")
    monkeypatch.setenv("AGENT_ARBITER_PERSONAL_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("AGENT_ARBITER_PERSONAL_SMTP_FROM_EMAIL", "omry@example.com")

    deploy_config_dir = Path(__file__).parents[3] / "deploy"
    _register_all_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    assert cfg.server.name == "agent-arbiter-mcp"
    assert cfg.policies.smtp.bot.require_confirmation is False
    assert cfg.policies.smtp.personal.require_confirmation is True
    assert set(cfg.accounts.smtp) == {"primary", "personal"}
    assert set(cfg.accounts.imap) == {"primary"}
    assert cfg.accounts.smtp.primary.host == "smtp.example.com"
    assert cfg.accounts.imap.primary.host == "imap.example.com"
    assert cfg.accounts.imap.primary.default_folder == "INBOX"
    assert cfg.accounts.smtp.personal.from_name == "Omry"


def test_secret_file_resolver_reads_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "imap_password"
    secret.write_text("super-secret\n", encoding="utf-8")

    _register_all_configs()
    cfg = OmegaConf.create({"password": f"${{secret_file:{secret}}}"})

    assert cfg.password == "super-secret"


def test_readonly_imap_deployment_config_composes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    username_file = tmp_path / "imap_username"
    password_file = tmp_path / "imap_password"
    username_file.write_text("user@example.com\n", encoding="utf-8")
    password_file.write_text("secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ARBITER_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("AGENT_ARBITER_IMAP_USERNAME_FILE", str(username_file))
    monkeypatch.setenv("AGENT_ARBITER_IMAP_PASSWORD_FILE", str(password_file))

    config_dir = Path(__file__).parents[3] / "deploy" / "readonly-imap"
    _register_all_configs()
    with initialize_config_dir(version_base=None, config_dir=str(config_dir)):
        cfg = compose(config_name="config")

    assert cfg.accounts.smtp == {}
    assert cfg.policies.smtp == {}
    assert cfg.accounts.imap.primary.policy == "alerts_readonly"
    assert cfg.accounts.imap.primary.description == (
        "Read-only view of a selected alert folder."
    )
    assert cfg.accounts.imap.primary.host == "imap.example.com"
    assert cfg.accounts.imap.primary.username == "user@example.com"
    assert cfg.accounts.imap.primary.password == "secret"
    assert cfg.accounts.imap.primary.default_folder == "TARGET_IMAP_FOLDER"
    policy = cfg.policies.imap.alerts_readonly
    assert policy.allow_read is True
    assert policy.allow_search is True
    assert policy.allow_move is False
    assert policy.allow_delete is False
    assert policy.system_flags.seen == IMAPFlagMode.read_only


def test_configured_service_names_uses_accounts() -> None:
    cfg = AppConfig(
        accounts={
            "smtp": {},
            "imap": {
                "primary": IMAPConfig(
                    default_folder="INBOX",
                    folders={"INBOX": IMAPFolderConfig()},
                )
            },
        },
        policies={"imap": {"bot": IMAPAccessPolicyConfig()}, "smtp": {}},
    )

    assert configured_service_names(cfg.accounts) == ["imap"]
    imap_accounts = service_accounts_for(cfg, "imap")
    assert imap_accounts is not None
    assert set(imap_accounts) == {"primary"}
    assert set(service_policies_for(cfg, "imap")) == {"bot"}
    assert service_accounts_for(cfg, "whatsapp") is None


def test_service_config_lookup_accepts_dynamic_service_accounts() -> None:
    cfg = AppConfig(
        accounts={
            "smtp": {"primary": SMTPConfig(policy="bot")},
            "imap": {},
            "whatsapp": {"bot": {"policy": "bot", "phone_number": "+15555550100"}},
        },
        policies={
            "smtp": {"bot": SMTPServicePolicyConfig()},
            "imap": {},
            "whatsapp": {"bot": {"allow_send": True}},
        },
    )

    assert configured_service_names(cfg.accounts) == ["smtp", "whatsapp"]
    whatsapp_accounts = service_accounts_for(cfg, "whatsapp")

    assert whatsapp_accounts is not None
    assert set(whatsapp_accounts) == {"bot"}
    assert set(service_policies_for(cfg, "whatsapp")) == {"bot"}


def test_structured_config_instantiation_allows_plugin_owned_policy_references() -> (
    None
):
    cfg = AppConfig(
        accounts={"smtp": {"primary": SMTPConfig(policy="bot")}, "imap": {}},
        policies={"smtp": {"bot": SMTPServicePolicyConfig()}, "imap": {}},
    )
    cast(SMTPConfig, cfg.accounts["smtp"]["primary"]).policy = "missing"
    hydra_cfg = OmegaConf.structured(cfg)

    assert isinstance(OmegaConf.to_object(hydra_cfg), AppConfig)


def test_structured_config_instantiation_allows_reused_policy_for_same_service() -> (
    None
):
    cfg = AppConfig(
        accounts={
            "smtp": {
                "primary": SMTPConfig(policy="bot"),
                "secondary": SMTPConfig(policy="bot"),
            },
            "imap": {},
        },
        policies={"smtp": {"bot": SMTPServicePolicyConfig()}, "imap": {}},
    )
    hydra_cfg = OmegaConf.structured(cfg)

    assert isinstance(OmegaConf.to_object(hydra_cfg), AppConfig)


def test_app_config_allows_no_configured_services() -> None:
    cfg = AppConfig(
        accounts={"smtp": {}, "imap": {}}, policies={"smtp": {}, "imap": {}}
    )

    assert configured_service_names(cfg.accounts) == []


def test_hydra_rejects_invalid_plugin_enum_values() -> None:
    with pytest.raises(ConfigCompositionException, match="accounts.smtp.primary.tls"):
        _compose_config(
            [
                "+arbiter/account/smtp@accounts.smtp.primary=schema",
                "accounts.smtp.primary.tls=bogus",
            ]
        )

    with pytest.raises(
        ConfigCompositionException,
        match="policies.imap.bot.system_flags.seen",
    ):
        _compose_config(
            [
                "+arbiter/policy/imap@policies.imap.bot=schema",
                "policies.imap.bot.system_flags.seen=bogus",
            ]
        )


def test_hydra_coerces_plugin_enum_values() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@accounts.smtp.primary=schema",
            "+arbiter/policy/imap@policies.imap.bot=schema",
            "accounts.smtp.primary.tls=implicit",
            "policies.imap.bot.system_flags.seen=read_write",
            "+policies.imap.bot.user_flags.bot_followed_up=read_only",
            "+policies.imap.bot.confirmation_required=[read]",
        ]
    )

    assert cfg.accounts.smtp.primary.tls == SMTPMailTlsMode.implicit
    assert cfg.policies.imap.bot.system_flags.seen == IMAPFlagMode.read_write
    assert cfg.policies.imap.bot.user_flags.bot_followed_up == IMAPFlagMode.read_only
    assert cfg.policies.imap.bot.confirmation_required == [IMAPConfirmationAction.read]
