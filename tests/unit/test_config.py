import pytest
from hydra import compose, initialize_config_dir, initialize_config_module
from omegaconf import DictConfig, OmegaConf
from pathlib import Path
from typing import Any, cast

from agent_arbiter.config import (
    AccountConfig,
    AccountAccessProfileConfig,
    AccountServicesConfig,
    AppConfig,
    IMAPAccessPolicyConfig,
    IMAPConfirmationAction,
    IMAPConfig,
    IMAPFlagMode,
    IMAPFolderConfig,
    IMAPServiceConfig,
    IMAPSystemFlagsPolicyConfig,
    MailTlsMode,
    MailConfig,
    resolve_imap_flag_mode,
    ServicesConfig,
    SMTPConfig,
    SMTPIdempotencyConfig,
    SMTPLimitsConfig,
    SMTPRecipientPolicyConfig,
    SMTPServiceConfig,
    SMTPServicePolicyConfig,
    validate_app_config,
    register_configs,
)


def _compose_config(overrides: list[str] | None = None) -> DictConfig:
    register_configs()
    with initialize_config_module(
        version_base=None, config_module="agent_arbiter.conf"
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
    assert cfg.services.smtp.accounts.primary.host == "localhost"
    assert cfg.services.smtp.accounts.primary.port == 587
    assert cfg.services.smtp.accounts.primary.from_email == "agent@example.com"
    assert cfg.services.smtp.accounts.primary.tls == MailTlsMode.starttls
    assert cfg.services.smtp.accounts.primary.verify_peer is True
    assert cfg.services.imap is None
    assert cfg.etc == {}
    assert (
        cfg.mail.account_access_profiles.bot.services.smtp.require_confirmation is False
    )
    assert (
        cfg.mail.account_access_profiles.bot.services.imap.confirmation_required == []
    )
    assert cfg.mail.account_access_profiles.bot.services.imap.allow_read is True
    assert cfg.mail.account_access_profiles.bot.services.imap.allow_search is True
    assert cfg.mail.account_access_profiles.bot.services.imap.allow_move is True
    assert cfg.mail.account_access_profiles.bot.services.imap.allow_delete is True
    assert (
        cfg.mail.account_access_profiles.bot.services.imap.system_flags.seen
        == IMAPFlagMode.read_write
    )


def test_compose_config_applies_overrides() -> None:
    cfg = _compose_config(
        [
            "server.transport=stdio",
            "server.port=9000",
            "services.smtp.accounts.primary.host=smtp.example.com",
            "services.smtp.accounts.primary.port=2525",
            "services.smtp.accounts.primary.from_name=Agent Team",
            "services.smtp.accounts.primary.tls=implicit",
            "services.smtp.accounts.primary.verify_peer=false",
            "mail.account_access_profiles.bot.services.smtp.require_confirmation=true",
            "mail.account_access_profiles.bot.services.imap.system_flags.seen=read_write",
        ]
    )

    assert cfg.server.transport == "stdio"
    assert cfg.server.port == 9000
    assert cfg.services.smtp.accounts.primary.host == "smtp.example.com"
    assert cfg.services.smtp.accounts.primary.port == 2525
    assert cfg.services.smtp.accounts.primary.from_name == "Agent Team"
    assert cfg.services.smtp.accounts.primary.tls == MailTlsMode.implicit
    assert cfg.services.smtp.accounts.primary.verify_peer is False
    assert (
        cfg.mail.account_access_profiles.bot.services.smtp.require_confirmation is True
    )
    assert (
        cfg.mail.account_access_profiles.bot.services.imap.system_flags.seen
        == IMAPFlagMode.read_write
    )


def test_hydra_config_preserves_lazy_interpolations() -> None:
    app_config = _compose_config(
        ["services.smtp.accounts.primary.from_name=${server.name}"]
    )

    assert app_config.server.name == "agent-arbiter"
    assert app_config.services.smtp.accounts.primary.from_name == "agent-arbiter"


def test_mailgateway_schema_alias_composes(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
defaults:
  - mailgateway_app_config_schema
  - _self_

server:
  name: agent-arbiter-mcp
mail:
  account_access_profiles:
    bot:
      services:
        smtp:
          require_confirmation: false
        imap:
          allow_read: true
          allow_search: true
          allow_move: true
          allow_delete: true
  accounts:
    primary:
      description: Bot account.
      account_access_profile: bot

services:
  smtp:
    accounts:
      primary:
        host: localhost
        authenticate: false
        username: ""
        password: ""
        from_email: bot@example.com
""",
        encoding="utf-8",
    )

    register_configs()
    with initialize_config_dir(version_base=None, config_dir=str(tmp_path)):
        cfg = compose(config_name="config")

    assert cfg.server.name == "agent-arbiter-mcp"
    assert (
        cfg.mail.account_access_profiles.bot.services.smtp.require_confirmation is False
    )
    assert cfg.services.smtp.accounts.primary.from_email == "bot@example.com"


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

    deploy_config_dir = Path(__file__).parents[2] / "deploy"
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    assert cfg.server.name == "agent-arbiter-mcp"
    assert (
        cfg.mail.account_access_profiles.bot.services.smtp.require_confirmation is False
    )
    assert (
        cfg.mail.account_access_profiles.personal.services.smtp.require_confirmation
        is True
    )
    assert set(cfg.mail.accounts) == {"primary", "personal"}
    assert cfg.services.smtp.accounts.primary.host == "smtp.example.com"
    assert cfg.services.imap.accounts.primary.host == "imap.example.com"
    assert cfg.services.imap.accounts.primary.default_folder == "INBOX"
    assert cfg.services.smtp.accounts.personal.from_name == "Omry"


def test_secret_file_resolver_reads_secret_file(tmp_path) -> None:
    secret = tmp_path / "imap_password"
    secret.write_text("super-secret\n", encoding="utf-8")

    register_configs()
    cfg = OmegaConf.create({"password": f"${{secret_file:{secret}}}"})

    assert cfg.password == "super-secret"


def test_readonly_imap_account_policy_limits_mutation_and_folder() -> None:
    folder = "TARGET_IMAP_FOLDER"

    cfg = AppConfig(
        mail=MailConfig(
            account_access_profiles={
                "alerts_readonly": AccountAccessProfileConfig(
                    services=AccountServicesConfig(
                        imap=IMAPAccessPolicyConfig(
                            allow_read=True,
                            allow_search=True,
                            allow_move=False,
                            allow_delete=False,
                        )
                    ),
                )
            },
            accounts={
                "primary": AccountConfig(
                    description="Read-only alerts",
                    account_access_profile="alerts_readonly",
                )
            },
        ),
        services=ServicesConfig(
            smtp=None,
            imap=IMAPServiceConfig(
                accounts={
                    "primary": IMAPConfig(
                        host="imap.example.com",
                        username="user@example.com",
                        password="secret",
                        default_folder=folder,
                        folders={folder: IMAPFolderConfig(description="Alerts")},
                    )
                }
            ),
        ),
    )

    account = cfg.mail.accounts["primary"]
    assert cfg.services.imap is not None
    imap_account = cfg.services.imap.accounts["primary"]
    policy = cfg.mail.account_access_profiles["alerts_readonly"].services.imap

    assert account.description == "Read-only alerts"
    assert cfg.services.smtp is None
    assert policy is not None
    assert imap_account.default_folder == folder
    assert list(imap_account.folders) == [folder]
    assert policy.allow_read is True
    assert policy.allow_search is True
    assert policy.allow_move is False
    assert policy.allow_delete is False
    assert policy.system_flags.seen is IMAPFlagMode.read_only


def test_readonly_imap_deployment_config_composes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    username_file = secret_dir / "imap_username"
    password_file = secret_dir / "imap_password"
    username_file.write_text("user@example.com\n", encoding="utf-8")
    password_file.write_text("secret\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_ARBITER_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("AGENT_ARBITER_IMAP_USERNAME_FILE", str(username_file))
    monkeypatch.setenv("AGENT_ARBITER_IMAP_PASSWORD_FILE", str(password_file))

    deploy_config_dir = Path(__file__).parents[2] / "deploy" / "readonly-imap"
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    account = cfg.mail.accounts.primary
    policy = cfg.mail.account_access_profiles.alerts_readonly.services.imap

    assert cfg.server.host == "0.0.0.0"
    assert account.description == "Read-only view of a selected alert folder."
    assert cfg.services.smtp is None
    assert cfg.services.imap.accounts.primary.host == "imap.example.com"
    assert cfg.services.imap.accounts.primary.username == "user@example.com"
    assert cfg.services.imap.accounts.primary.password == "secret"
    assert cfg.services.imap.accounts.primary.default_folder == "TARGET_IMAP_FOLDER"
    assert list(cfg.services.imap.accounts.primary.folders) == ["TARGET_IMAP_FOLDER"]
    assert policy.allow_read is True
    assert policy.allow_search is True
    assert policy.allow_move is False
    assert policy.allow_delete is False


def test_app_config_rejects_unknown_account_access_profile() -> None:
    with pytest.raises(ValueError, match="unknown account_access_profile"):
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="Primary account",
                        account_access_profile="missing",
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            )
        )


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("user", ""),
        ("", "secret"),
    ],
)
def test_smtp_config_requires_username_and_password_together_when_auth_enabled(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValueError, match="username and password together"):
        SMTPConfig(authenticate=True, username=username, password=password)


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("user", ""),
        ("", "secret"),
    ],
)
def test_smtp_config_rejects_credentials_when_auth_is_disabled(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValueError, match="authenticate is false"):
        SMTPConfig(authenticate=False, username=username, password=password)


def test_smtp_config_rejects_unknown_tls_mode() -> None:
    with pytest.raises(ValueError, match="smtp config tls"):
        SMTPConfig(tls=cast(Any, "bogus"))


def test_imap_config_requires_default_folder_to_exist() -> None:
    with pytest.raises(ValueError, match="default_folder"):
        IMAPConfig(
            default_folder="INBOX",
            folders={"Archive": IMAPFolderConfig(description="Archive")},
        )


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("user", ""),
        ("", "secret"),
    ],
)
def test_imap_config_requires_username_and_password_together(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValueError, match="username and password together"):
        IMAPConfig(username=username, password=password)


def test_imap_access_policy_requires_read_for_search() -> None:
    with pytest.raises(ValueError, match="allow_search requires allow_read"):
        IMAPAccessPolicyConfig(
            allow_read=False,
            allow_search=True,
            allow_move=False,
            allow_delete=False,
        )


def test_imap_access_policy_requires_read_for_move() -> None:
    with pytest.raises(ValueError, match="allow_move requires allow_read"):
        IMAPAccessPolicyConfig(
            allow_read=False,
            allow_search=False,
            allow_move=True,
            allow_delete=False,
        )


def test_imap_access_policy_requires_read_for_delete() -> None:
    with pytest.raises(ValueError, match="allow_delete requires allow_read"):
        IMAPAccessPolicyConfig(
            allow_read=False,
            allow_search=False,
            allow_move=False,
            allow_delete=True,
        )


def test_account_access_profile_rejects_confirmation_for_disallowed_action() -> None:
    with pytest.raises(
        ValueError, match="confirmation_required contains an action that is not allowed"
    ):
        IMAPAccessPolicyConfig(
            allow_read=False,
            allow_search=False,
            allow_move=False,
            allow_delete=False,
            confirmation_required=[IMAPConfirmationAction.read],
        )


def test_account_access_profile_rejects_mark_read_confirmation_without_seen_write() -> (
    None
):
    with pytest.raises(
        ValueError, match="confirmation_required contains an action that is not allowed"
    ):
        IMAPAccessPolicyConfig(
            confirmation_required=[IMAPConfirmationAction.mark_read],
        )


def test_imap_access_policy_accepts_mark_read_confirmation_with_seen_write() -> None:
    policy = IMAPAccessPolicyConfig(
        confirmation_required=[IMAPConfirmationAction.mark_read],
        system_flags=IMAPSystemFlagsPolicyConfig(seen=IMAPFlagMode.read_write),
    )

    assert policy.confirmation_required == [IMAPConfirmationAction.mark_read]


def test_imap_access_policy_rejects_unknown_user_flag_mode() -> None:
    with pytest.raises(ValueError, match="imap user_flags.bot.followed_up"):
        IMAPAccessPolicyConfig(user_flags={"bot.followed_up": cast(Any, "bogus")})


def test_imap_access_policy_accepts_hidden_user_flag_mode_as_noop() -> None:
    policy = IMAPAccessPolicyConfig(user_flags={"bot.followed_up": IMAPFlagMode.hidden})

    assert policy.user_flags["bot.followed_up"] is IMAPFlagMode.hidden


def test_imap_access_policy_rejects_legacy_mutate_alias() -> None:
    with pytest.raises(ValueError, match="imap user_flags.bot.followed_up"):
        IMAPAccessPolicyConfig(user_flags={"bot.followed_up": cast(Any, "mutate")})


def test_resolve_imap_flag_mode_defaults_system_flags_to_read_only() -> None:
    policy = IMAPAccessPolicyConfig()

    assert resolve_imap_flag_mode(policy, "\\Seen") is IMAPFlagMode.read_only
    assert resolve_imap_flag_mode(policy, "\\Recent") is IMAPFlagMode.read_only


def test_resolve_imap_flag_mode_defaults_user_flags_to_hidden() -> None:
    policy = IMAPAccessPolicyConfig()

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is IMAPFlagMode.hidden


def test_resolve_imap_flag_mode_returns_configured_user_flag_mode() -> None:
    policy = IMAPAccessPolicyConfig(
        user_flags={"bot.followed_up": IMAPFlagMode.read_write}
    )

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is IMAPFlagMode.read_write


def test_resolve_imap_flag_mode_returns_explicit_hidden_user_flag_mode() -> None:
    policy = IMAPAccessPolicyConfig(user_flags={"bot.followed_up": IMAPFlagMode.hidden})

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is IMAPFlagMode.hidden


def test_app_config_rejects_account_without_any_services() -> None:
    with pytest.raises(ValueError, match="must configure at least one service"):
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="Primary account",
                        account_access_profile="bot",
                    ),
                    "secondary": AccountConfig(
                        description="Secondary account",
                        account_access_profile="bot",
                    ),
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            ),
            services=ServicesConfig(
                smtp=SMTPServiceConfig(accounts={"secondary": SMTPConfig()}),
                imap=None,
            ),
        )


def test_app_config_accepts_smtp_only_account() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="SMTP account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            )
        )
    )


def test_app_config_accepts_imap_only_account() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="IMAP account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={
                    "bot": AccountAccessProfileConfig(
                        services=AccountServicesConfig(
                            smtp=None, imap=IMAPAccessPolicyConfig()
                        )
                    )
                },
            ),
            services=ServicesConfig(
                smtp=None,
                imap=IMAPServiceConfig(
                    accounts={
                        "primary": IMAPConfig(
                            default_folder="INBOX",
                            folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                        )
                    }
                ),
            ),
        )
    )


def test_app_config_accepts_account_with_both_protocols() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="Full account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            ),
            services=ServicesConfig(
                smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                imap=IMAPServiceConfig(
                    accounts={
                        "primary": IMAPConfig(
                            default_folder="INBOX",
                            folders={"INBOX": IMAPFolderConfig(description="Inbox")},
                        )
                    }
                ),
            ),
        )
    )


def test_app_config_rejects_smtp_account_without_smtp_service_policy() -> None:
    with pytest.raises(ValueError, match="has no smtp service policy"):
        validate_app_config(
            AppConfig(
                mail=MailConfig(
                    accounts={
                        "primary": AccountConfig(
                            description="SMTP account",
                            account_access_profile="bot",
                        )
                    },
                    account_access_profiles={
                        "bot": AccountAccessProfileConfig(
                            services=AccountServicesConfig(
                                smtp=None, imap=IMAPAccessPolicyConfig()
                            )
                        )
                    },
                ),
                services=ServicesConfig(
                    smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                    imap=None,
                ),
            )
        )


def test_app_config_accepts_smtp_account_with_recipient_policy() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="SMTP account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={
                    "bot": AccountAccessProfileConfig(
                        services=AccountServicesConfig(
                            smtp=SMTPServicePolicyConfig(
                                recipient_policy=SMTPRecipientPolicyConfig(
                                    allowed_recipients=["ops@example.com"],
                                    allowed_domain_patterns=[
                                        "example.com",
                                        "*.example.org",
                                    ],
                                    blocked_recipients=["ceo@example.com"],
                                    blocked_domain_patterns=["*.blocked.example"],
                                )
                            ),
                            imap=IMAPAccessPolicyConfig(),
                        )
                    )
                },
            ),
            services=ServicesConfig(
                smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                imap=None,
            ),
        )
    )


def test_app_config_accepts_implemented_smtp_rate_limit() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="SMTP account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={
                    "bot": AccountAccessProfileConfig(
                        services=AccountServicesConfig(
                            smtp=SMTPServicePolicyConfig(
                                limits=SMTPLimitsConfig(max_messages_per_minute=5)
                            ),
                            imap=IMAPAccessPolicyConfig(),
                        )
                    )
                },
            ),
            services=ServicesConfig(
                smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                imap=None,
            ),
        )
    )


def test_app_config_rejects_non_positive_smtp_rate_limit() -> None:
    with pytest.raises(
        ValueError,
        match="limits\\.max_messages_per_minute must be at least 1",
    ):
        validate_app_config(
            AppConfig(
                mail=MailConfig(
                    accounts={
                        "primary": AccountConfig(
                            description="SMTP account",
                            account_access_profile="bot",
                        )
                    },
                    account_access_profiles={
                        "bot": AccountAccessProfileConfig(
                            services=AccountServicesConfig(
                                smtp=SMTPServicePolicyConfig(
                                    limits=SMTPLimitsConfig(max_messages_per_minute=0)
                                ),
                                imap=IMAPAccessPolicyConfig(),
                            )
                        )
                    },
                ),
                services=ServicesConfig(
                    smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                    imap=None,
                ),
            )
        )


def test_app_config_rejects_unimplemented_smtp_idempotency_config() -> None:
    with pytest.raises(
        ValueError,
        match="services\\.smtp\\.idempotency\\.expiration_days",
    ):
        validate_app_config(
            AppConfig(
                mail=MailConfig(
                    accounts={
                        "primary": AccountConfig(
                            description="SMTP account",
                            account_access_profile="bot",
                        )
                    },
                    account_access_profiles={
                        "bot": AccountAccessProfileConfig(
                            services=AccountServicesConfig(
                                smtp=SMTPServicePolicyConfig(
                                    idempotency=SMTPIdempotencyConfig(expiration_days=3)
                                ),
                                imap=IMAPAccessPolicyConfig(),
                            )
                        )
                    },
                ),
                services=ServicesConfig(
                    smtp=SMTPServiceConfig(accounts={"primary": SMTPConfig()}),
                    imap=None,
                ),
            )
        )
