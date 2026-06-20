from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import pytest
from hydra import compose, initialize_config_dir
from hydra.errors import ConfigCompositionException
from omegaconf import DictConfig, OmegaConf

from arbiter_server.config import (
    AppConfig,
    ArbiterConfig,
    DeploymentScope,
    ServerTlsSource,
    configured_service_names,
    register_configs,
    service_accounts_for,
    service_policies_for,
)
from arbiter_imap.config import (
    IMAPAccessPolicyConfig,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    IMAPFolderKind,
    register_configs as register_imap_configs,
)
from arbiter_smtp.config import (
    MailTlsMode as SMTPMailTlsMode,
    SMTPConfig,
    SMTPSentCopyFailureMode,
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
    with TemporaryDirectory() as tmp_dir:
        config_dir = Path(tmp_dir)
        (config_dir / "config.yaml").write_text(
            """
defaults:
  - arbiter_app_config_schema
  - /arbiter/server: http
  - _self_

arbiter:
  server:
    name: arbiter
  account: {}
  policy: {}
  etc: {}
""",
            encoding="utf-8",
        )
        with initialize_config_dir(version_base=None, config_dir=str(config_dir)):
            return compose(config_name="config", overrides=overrides or [])


def _compose_imap_folder_config(*, kind: str) -> DictConfig:
    _register_all_configs()
    with TemporaryDirectory() as tmp_dir:
        config_dir = Path(tmp_dir)
        (config_dir / "config.yaml").write_text(
            f"""
defaults:
  - arbiter_app_config_schema
  - /arbiter/account/imap/schema@arbiter.account.imap.primary
  - _self_

arbiter:
  server:
    name: arbiter
  account:
    imap:
      primary:
        folders:
          Sent:
            description: Sent mail.
            kind: {kind}
  policy: {{}}
  etc: {{}}
""",
            encoding="utf-8",
        )
        with initialize_config_dir(version_base=None, config_dir=str(config_dir)):
            return compose(config_name="config")


def test_compose_config_returns_hydra_config() -> None:
    cfg = _compose_config()

    assert isinstance(cfg, DictConfig)
    assert cfg.arbiter.server.name == "arbiter"
    assert cfg.arbiter.server.transport == "https"
    assert cfg.arbiter.server.bind.scheme == "https"
    assert cfg.arbiter.server.bind.host == "127.0.0.1"
    assert cfg.arbiter.server.bind.port == 8075
    assert cfg.arbiter.server.bind.path == ""
    assert cfg.arbiter.server.bind.base_url == "https://127.0.0.1:8075"
    assert cfg.arbiter.server.public.scheme == "https"
    assert cfg.arbiter.server.public.host == "127.0.0.1"
    assert cfg.arbiter.server.public.port == 8075
    assert cfg.arbiter.server.public.path == ""
    assert cfg.arbiter.server.public.base_url == "https://127.0.0.1:8075"
    assert cfg.arbiter.server.tls.source == ServerTlsSource.SELF_SIGNED
    assert cfg.arbiter.server.tls.cert_file is None
    assert cfg.arbiter.server.tls.key_file is None
    assert cfg.arbiter.deployment_scope == DeploymentScope.unknown
    assert cfg.arbiter.storage.server_data_dir is None
    assert cfg.arbiter.storage.plugin_data_dir is None
    assert cfg.arbiter.account == {}
    assert cfg.arbiter.policy == {}
    assert cfg.arbiter.etc == {}


def test_compose_config_applies_overrides() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@arbiter.account.smtp.primary=schema",
            "+arbiter/account/imap@arbiter.account.imap.primary=schema",
            "+arbiter/policy/smtp@arbiter.policy.smtp.bot=schema",
            "+arbiter/policy/imap@arbiter.policy.imap.bot=schema",
            "arbiter.server.transport=https",
            "arbiter.server.bind.port=9000",
            "arbiter.server.tls.source=CERT_FILES",
            "arbiter.server.tls.cert_file=/tls/cert.pem",
            "arbiter.server.tls.key_file=/tls/key.pem",
            "arbiter.server.public.scheme=https",
            "arbiter.server.public.host=arbiter.example.test",
            "arbiter.server.public.port=443",
            "arbiter.account.smtp.primary.host=smtp.example.com",
            "arbiter.account.smtp.primary.port=2525",
            "arbiter.account.smtp.primary.from_name=Agent Team",
            "arbiter.account.smtp.primary.guidance=Use for outbound notifications.",
            "arbiter.account.smtp.primary.tls=implicit",
            "arbiter.account.smtp.primary.verify_peer=false",
            "arbiter.account.imap.primary.guidance=Use for inbox triage.",
            "arbiter.policy.smtp.bot.limits.max_messages_per_minute=10",
            "arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN=read_write",
        ]
    )

    assert cfg.arbiter.server.transport == "https"
    assert cfg.arbiter.server.bind.port == 9000
    assert cfg.arbiter.server.tls.source == ServerTlsSource.CERT_FILES
    assert cfg.arbiter.server.tls.cert_file == "/tls/cert.pem"
    assert cfg.arbiter.server.tls.key_file == "/tls/key.pem"
    assert cfg.arbiter.server.public.base_url == "https://arbiter.example.test:443"
    assert cfg.arbiter.account.smtp.primary.host == "smtp.example.com"
    assert cfg.arbiter.account.smtp.primary.port == 2525
    assert cfg.arbiter.account.smtp.primary.from_name == "Agent Team"
    assert (
        cfg.arbiter.account.smtp.primary.guidance == "Use for outbound notifications."
    )
    assert cfg.arbiter.account.smtp.primary.tls == SMTPMailTlsMode.implicit
    assert cfg.arbiter.account.smtp.primary.verify_peer is False
    assert cfg.arbiter.account.imap.primary.guidance == "Use for inbox triage."
    assert cfg.arbiter.policy.smtp.bot.limits.max_messages_per_minute == 10
    assert (
        cfg.arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN
        == IMAPFlagMode.read_write
    )


def test_hydra_config_preserves_lazy_interpolations() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@arbiter.account.smtp.primary=schema",
            "+arbiter/policy/smtp@arbiter.policy.smtp.bot=schema",
            "arbiter.account.smtp.primary.from_name=${arbiter.server.name}",
        ]
    )

    assert cfg.arbiter.server.name == "arbiter"
    assert cfg.arbiter.account.smtp.primary.from_name == "arbiter"


def test_standard_deployment_config_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARBITER_BOT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ARBITER_BOT_SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("ARBITER_BOT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ARBITER_BOT_SMTP_FROM_EMAIL", "bot@example.com")
    monkeypatch.setenv("ARBITER_BOT_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("ARBITER_BOT_IMAP_USERNAME", "bot@example.com")
    monkeypatch.setenv("ARBITER_BOT_IMAP_PASSWORD", "secret")
    monkeypatch.setenv("ARBITER_PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ARBITER_PERSONAL_SMTP_USERNAME", "omry@example.com")
    monkeypatch.setenv("ARBITER_PERSONAL_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ARBITER_PERSONAL_SMTP_FROM_EMAIL", "omry@example.com")

    deploy_config_dir = Path(__file__).parents[3] / "deploy"
    _register_all_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    assert cfg.arbiter.server.name == "arbiter"
    assert cfg.arbiter.policy.smtp.bot.limits.max_messages_per_minute is None
    assert cfg.arbiter.policy.smtp.personal.limits.max_messages_per_minute is None
    assert set(cfg.arbiter.account.smtp) == {"primary", "personal"}
    assert set(cfg.arbiter.account.imap) == {"primary"}
    assert cfg.arbiter.account.smtp.primary.host == "smtp.example.com"
    assert cfg.arbiter.account.imap.primary.host == "imap.example.com"
    assert cfg.arbiter.account.imap.primary.default_folder == "INBOX"
    assert cfg.arbiter.account.smtp.personal.from_name == "Omry"


def test_secret_file_resolver_reads_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "imap_password"
    secret.write_text("super-secret\n", encoding="utf-8")

    _register_all_configs()
    cfg = OmegaConf.create({"password": f"${{secret_file:{secret}}}"})

    assert cfg.password == "super-secret"


def test_configured_service_names_uses_accounts() -> None:
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={
                "smtp": {},
                "imap": {
                    "primary": IMAPConfig(
                        default_folder="INBOX",
                        folders={"INBOX": IMAPFolderConfig()},
                    )
                },
            },
            policy={"imap": {"bot": IMAPAccessPolicyConfig()}, "smtp": {}},
        ),
    )

    assert configured_service_names(cfg.arbiter.account) == ["imap"]
    imap_accounts = service_accounts_for(cfg, "imap")
    assert imap_accounts is not None
    assert set(imap_accounts) == {"primary"}
    assert set(service_policies_for(cfg, "imap")) == {"bot"}
    assert service_accounts_for(cfg, "whatsapp") is None


def test_service_config_lookup_accepts_dynamic_service_accounts() -> None:
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={
                "smtp": {"primary": SMTPConfig(policy="bot")},
                "imap": {},
                "whatsapp": {"bot": {"policy": "bot", "phone_number": "+15555550100"}},
            },
            policy={
                "smtp": {"bot": SMTPServicePolicyConfig()},
                "imap": {},
                "whatsapp": {"bot": {"allow_send": True}},
            },
        ),
    )

    assert configured_service_names(cfg.arbiter.account) == ["smtp", "whatsapp"]
    whatsapp_accounts = service_accounts_for(cfg, "whatsapp")

    assert whatsapp_accounts is not None
    assert set(whatsapp_accounts) == {"bot"}
    assert set(service_policies_for(cfg, "whatsapp")) == {"bot"}


def test_structured_config_instantiation_allows_plugin_owned_policy_references() -> (
    None
):
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"smtp": {"primary": SMTPConfig(policy="bot")}, "imap": {}},
            policy={"smtp": {"bot": SMTPServicePolicyConfig()}, "imap": {}},
        ),
    )
    cast(SMTPConfig, cfg.arbiter.account["smtp"]["primary"]).policy = "missing"
    hydra_cfg = OmegaConf.structured(cfg)

    assert isinstance(OmegaConf.to_object(hydra_cfg), AppConfig)


def test_structured_config_instantiation_allows_reused_policy_for_same_service() -> (
    None
):
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={
                "smtp": {
                    "primary": SMTPConfig(policy="bot"),
                    "secondary": SMTPConfig(policy="bot"),
                },
                "imap": {},
            },
            policy={"smtp": {"bot": SMTPServicePolicyConfig()}, "imap": {}},
        ),
    )
    hydra_cfg = OmegaConf.structured(cfg)

    assert isinstance(OmegaConf.to_object(hydra_cfg), AppConfig)


def test_app_config_allows_no_configured_services() -> None:
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"smtp": {}, "imap": {}},
            policy={"smtp": {}, "imap": {}},
        )
    )

    assert configured_service_names(cfg.arbiter.account) == []


def test_hydra_rejects_invalid_plugin_enum_values() -> None:
    with pytest.raises(
        ConfigCompositionException, match="arbiter.account.smtp.primary.tls"
    ):
        _compose_config(
            [
                "+arbiter/account/smtp@arbiter.account.smtp.primary=schema",
                "arbiter.account.smtp.primary.tls=bogus",
            ]
        )

    with pytest.raises(
        ConfigCompositionException,
        match="arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN",
    ):
        _compose_config(
            [
                "+arbiter/policy/imap@arbiter.policy.imap.bot=schema",
                "arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN=bogus",
            ]
        )

    with pytest.raises(
        ConfigCompositionException,
        match="arbiter.account.imap.primary.folders.Sent.kind",
    ):
        _compose_imap_folder_config(kind="bogus")

    with pytest.raises(
        ConfigCompositionException,
        match="arbiter.policy.smtp.bot.sent_copy.on_failure",
    ):
        _compose_config(
            [
                "+arbiter/policy/smtp@arbiter.policy.smtp.bot=schema",
                "arbiter.policy.smtp.bot.sent_copy.on_failure=bogus",
            ]
        )


def test_hydra_coerces_plugin_enum_values() -> None:
    cfg = _compose_config(
        [
            "+arbiter/account/smtp@arbiter.account.smtp.primary=schema",
            "+arbiter/policy/smtp@arbiter.policy.smtp.bot=schema",
            "+arbiter/policy/imap@arbiter.policy.imap.bot=schema",
            "arbiter.account.smtp.primary.tls=implicit",
            "arbiter.account.smtp.primary.sent_copy.folder=Sent",
            "arbiter.policy.smtp.bot.sent_copy.on_failure=fail",
            "arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN=read_write",
            "+arbiter.policy.imap.bot.operation_defaults.user_flags.bot_followed_up=read_only",
        ]
    )

    assert cfg.arbiter.account.smtp.primary.tls == SMTPMailTlsMode.implicit
    assert cfg.arbiter.account.smtp.primary.sent_copy.folder == "Sent"
    assert (
        cfg.arbiter.policy.smtp.bot.sent_copy.on_failure == SMTPSentCopyFailureMode.fail
    )
    folder_cfg = _compose_imap_folder_config(kind="SENT")
    assert (
        folder_cfg.arbiter.account.imap.primary.folders.Sent.kind == IMAPFolderKind.SENT
    )
    assert (
        cfg.arbiter.policy.imap.bot.operation_defaults.system_flags.SEEN
        == IMAPFlagMode.read_write
    )
    assert (
        cfg.arbiter.policy.imap.bot.operation_defaults.user_flags.bot_followed_up
        == IMAPFlagMode.read_only
    )


def test_smtp_configstore_example_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in (
        "SMTP_BOT_ACCOUNT_USERNAME",
        "SMTP_BOT_ACCOUNT_PASSWORD",
    ):
        monkeypatch.delenv(env_name, raising=False)

    cfg = _compose_config(
        [
            "+arbiter/account/smtp@arbiter.account.smtp.primary=example",
            "+arbiter/policy/smtp@arbiter.policy.smtp.bot=example",
        ]
    )

    assert (
        cfg.arbiter.account.smtp.primary.description
        == "SMTP account for (agent@example.com)"
    )
    assert cfg.arbiter.account.smtp.primary.host == "smtp.example.com"
    assert cfg.arbiter.account.smtp.primary.port == 587
    assert cfg.arbiter.account.smtp.primary.authenticate is True
    assert cfg.arbiter.account.smtp.primary.tls == SMTPMailTlsMode.starttls
    assert cfg.arbiter.policy.smtp.bot.limits.max_messages_per_minute == 30
    assert cfg.arbiter.policy.smtp.bot.limits.max_recipients_per_message == 10
    assert cfg.arbiter.policy.smtp.bot.recipient_policy.allowed_domain_patterns == []
