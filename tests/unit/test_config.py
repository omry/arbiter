import pytest
from hydra import compose, initialize_config_dir, initialize_config_module
from omegaconf import DictConfig, OmegaConf
from pathlib import Path
from typing import Any, cast

from mail_sentry.config import (
    AccountConfig,
    AccountAccessProfileConfig,
    AccountSensitivityTier,
    AppConfig,
    ImapAccessPolicyConfig,
    ImapConfig,
    ImapFlagMode,
    ImapFolderConfig,
    MailTlsMode,
    MailConfig,
    resolve_imap_flag_mode,
    SmtpConfig,
    validate_app_config,
    register_configs,
)


def _compose_config(overrides: list[str] | None = None) -> DictConfig:
    register_configs()
    with initialize_config_module(version_base=None, config_module="mail_sentry.conf"):
        return compose(config_name="config", overrides=overrides or [])


def test_compose_config_returns_hydra_config() -> None:
    cfg = _compose_config()

    assert isinstance(cfg, DictConfig)
    assert cfg.server.name == "mail-sentry"
    assert cfg.server.transport == "streamable-http"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.server.path == "/mcp"
    assert cfg.mail.accounts.primary.smtp.host == "localhost"
    assert cfg.mail.accounts.primary.smtp.port == 587
    assert cfg.mail.accounts.primary.smtp.from_email == "agent@example.com"
    assert cfg.mail.accounts.primary.sensitivity_tier == AccountSensitivityTier.standard
    assert cfg.mail.accounts.primary.smtp.tls == MailTlsMode.starttls
    assert cfg.mail.accounts.primary.smtp.verify_peer is True
    assert cfg.mail.account_access_profiles.bot.allow_smtp_send is True
    assert cfg.mail.account_access_profiles.bot.imap.allow_read is True
    assert cfg.mail.account_access_profiles.bot.imap.allow_search is True
    assert cfg.mail.account_access_profiles.bot.imap.allow_move is True
    assert cfg.mail.account_access_profiles.bot.imap.allow_delete is True
    assert (
        cfg.mail.account_access_profiles.bot.imap.system_flags.seen
        == ImapFlagMode.read_write
    )


def test_compose_config_applies_overrides() -> None:
    cfg = _compose_config(
        [
            "server.transport=stdio",
            "server.port=9000",
            "mail.accounts.primary.smtp.host=smtp.example.com",
            "mail.accounts.primary.smtp.port=2525",
            "mail.accounts.primary.smtp.from_name=Agent Team",
            "mail.accounts.primary.smtp.tls=implicit",
            "mail.accounts.primary.smtp.verify_peer=false",
            "mail.account_access_profiles.bot.allow_smtp_send=false",
            "mail.account_access_profiles.bot.imap.system_flags.seen=read_write",
        ]
    )

    assert cfg.server.transport == "stdio"
    assert cfg.server.port == 9000
    assert cfg.mail.accounts.primary.smtp.host == "smtp.example.com"
    assert cfg.mail.accounts.primary.smtp.port == 2525
    assert cfg.mail.accounts.primary.smtp.from_name == "Agent Team"
    assert cfg.mail.accounts.primary.smtp.tls == MailTlsMode.implicit
    assert cfg.mail.accounts.primary.smtp.verify_peer is False
    assert cfg.mail.account_access_profiles.bot.allow_smtp_send is False
    assert (
        cfg.mail.account_access_profiles.bot.imap.system_flags.seen
        == ImapFlagMode.read_write
    )


def test_hydra_config_preserves_lazy_interpolations() -> None:
    app_config = _compose_config(
        ["mail.accounts.primary.smtp.from_name=${server.name}"]
    )

    assert app_config.server.name == "mail-sentry"
    assert app_config.mail.accounts.primary.smtp.from_name == "mail-sentry"


def test_mailgateway_schema_alias_composes(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
defaults:
  - mailgateway_app_config_schema
  - _self_

server:
  name: mailgateway-mcp
mail:
  account_access_profiles:
    bot:
      read_only: false
      allow_smtp_send: true
      imap:
        allow_read: true
        allow_search: true
        allow_move: true
        allow_delete: true
  accounts:
    primary:
      description: Bot account.
      account_access_profile: bot
      smtp:
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

    assert cfg.server.name == "mailgateway-mcp"
    assert cfg.mail.account_access_profiles.bot.read_only is False
    assert cfg.mail.accounts.primary.smtp.from_email == "bot@example.com"


def test_standard_deployment_config_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAILGATEWAY_BOT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MAILGATEWAY_BOT_SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("MAILGATEWAY_BOT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("MAILGATEWAY_BOT_SMTP_FROM_EMAIL", "bot@example.com")
    monkeypatch.setenv("MAILGATEWAY_BOT_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAILGATEWAY_BOT_IMAP_USERNAME", "bot@example.com")
    monkeypatch.setenv("MAILGATEWAY_BOT_IMAP_PASSWORD", "secret")
    monkeypatch.setenv("MAILGATEWAY_PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MAILGATEWAY_PERSONAL_SMTP_USERNAME", "omry@example.com")
    monkeypatch.setenv("MAILGATEWAY_PERSONAL_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("MAILGATEWAY_PERSONAL_SMTP_FROM_EMAIL", "omry@example.com")

    deploy_config_dir = Path(__file__).parents[2] / "deploy"
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    assert cfg.server.name == "mailgateway-mcp"
    assert cfg.mail.account_access_profiles.bot.read_only is False
    assert cfg.mail.account_access_profiles.personal.read_only is False
    assert set(cfg.mail.accounts) == {"primary", "personal"}
    assert cfg.mail.accounts.primary.smtp.host == "smtp.example.com"
    assert cfg.mail.accounts.primary.imap.host == "imap.example.com"
    assert cfg.mail.accounts.primary.imap.default_folder == "INBOX"
    assert cfg.mail.accounts.personal.smtp.from_name == "Omry"


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
                    allow_smtp_send=False,
                    imap=ImapAccessPolicyConfig(
                        allow_read=True,
                        allow_search=True,
                        allow_move=False,
                        allow_delete=False,
                    ),
                )
            },
            accounts={
                "primary": AccountConfig(
                    description="Read-only alerts",
                    account_access_profile="alerts_readonly",
                    sensitivity_tier=AccountSensitivityTier.sensitive,
                    imap=ImapConfig(
                        host="imap.example.com",
                        username="user@example.com",
                        password="secret",
                        default_folder=folder,
                        folders={folder: ImapFolderConfig(description="Alerts")},
                    ),
                )
            },
        )
    )

    account = cfg.mail.accounts["primary"]
    policy = cfg.mail.account_access_profiles["alerts_readonly"].imap

    assert account.smtp is None
    assert account.imap is not None
    assert account.imap.default_folder == folder
    assert list(account.imap.folders) == [folder]
    assert policy.allow_read is True
    assert policy.allow_search is True
    assert policy.allow_move is False
    assert policy.allow_delete is False
    assert policy.system_flags.seen is ImapFlagMode.read_only


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

    monkeypatch.setenv("MAIL_SENTRY_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAIL_SENTRY_IMAP_USERNAME_FILE", str(username_file))
    monkeypatch.setenv("MAIL_SENTRY_IMAP_PASSWORD_FILE", str(password_file))

    deploy_config_dir = Path(__file__).parents[2] / "deploy" / "readonly-imap"
    register_configs()
    with initialize_config_dir(
        version_base=None,
        config_dir=str(deploy_config_dir),
    ):
        cfg = compose(config_name="config")

    account = cfg.mail.accounts.primary
    policy = cfg.mail.account_access_profiles.alerts_readonly.imap

    assert cfg.server.host == "0.0.0.0"
    assert account.smtp is None
    assert account.imap.host == "imap.example.com"
    assert account.imap.username == "user@example.com"
    assert account.imap.password == "secret"
    assert account.imap.default_folder == "TARGET_IMAP_FOLDER"
    assert list(account.imap.folders) == ["TARGET_IMAP_FOLDER"]
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
                        smtp=SmtpConfig(),
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
        SmtpConfig(authenticate=True, username=username, password=password)


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
        SmtpConfig(authenticate=False, username=username, password=password)


def test_smtp_config_rejects_unknown_tls_mode() -> None:
    with pytest.raises(ValueError, match="smtp config tls"):
        SmtpConfig(tls=cast(Any, "bogus"))


def test_imap_config_requires_default_folder_to_exist() -> None:
    with pytest.raises(ValueError, match="default_folder"):
        ImapConfig(
            default_folder="INBOX",
            folders={"Archive": ImapFolderConfig(description="Archive")},
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
        ImapConfig(username=username, password=password)


def test_imap_access_policy_requires_read_for_search() -> None:
    with pytest.raises(ValueError, match="allow_search requires allow_read"):
        ImapAccessPolicyConfig(
            allow_read=False,
            allow_search=True,
            allow_move=False,
            allow_delete=False,
        )


def test_imap_access_policy_requires_read_for_move() -> None:
    with pytest.raises(ValueError, match="allow_move requires allow_read"):
        ImapAccessPolicyConfig(
            allow_read=False,
            allow_search=False,
            allow_move=True,
            allow_delete=False,
        )


def test_imap_access_policy_requires_read_for_delete() -> None:
    with pytest.raises(ValueError, match="allow_delete requires allow_read"):
        ImapAccessPolicyConfig(
            allow_read=False,
            allow_search=False,
            allow_move=False,
            allow_delete=True,
        )


def test_imap_access_policy_rejects_unknown_user_flag_mode() -> None:
    with pytest.raises(ValueError, match="imap user_flags.bot.followed_up"):
        ImapAccessPolicyConfig(user_flags={"bot.followed_up": cast(Any, "bogus")})


def test_imap_access_policy_accepts_hidden_user_flag_mode_as_noop() -> None:
    policy = ImapAccessPolicyConfig(user_flags={"bot.followed_up": ImapFlagMode.hidden})

    assert policy.user_flags["bot.followed_up"] is ImapFlagMode.hidden


def test_imap_access_policy_rejects_legacy_mutate_alias() -> None:
    with pytest.raises(ValueError, match="imap user_flags.bot.followed_up"):
        ImapAccessPolicyConfig(user_flags={"bot.followed_up": cast(Any, "mutate")})


def test_resolve_imap_flag_mode_defaults_system_flags_to_read_only() -> None:
    policy = ImapAccessPolicyConfig()

    assert resolve_imap_flag_mode(policy, "\\Seen") is ImapFlagMode.read_only
    assert resolve_imap_flag_mode(policy, "\\Recent") is ImapFlagMode.read_only


def test_resolve_imap_flag_mode_defaults_user_flags_to_hidden() -> None:
    policy = ImapAccessPolicyConfig()

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is ImapFlagMode.hidden


def test_resolve_imap_flag_mode_returns_configured_user_flag_mode() -> None:
    policy = ImapAccessPolicyConfig(
        user_flags={"bot.followed_up": ImapFlagMode.read_write}
    )

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is ImapFlagMode.read_write


def test_resolve_imap_flag_mode_returns_explicit_hidden_user_flag_mode() -> None:
    policy = ImapAccessPolicyConfig(user_flags={"bot.followed_up": ImapFlagMode.hidden})

    assert resolve_imap_flag_mode(policy, "bot.followed_up") is ImapFlagMode.hidden


def test_app_config_rejects_account_without_any_protocols() -> None:
    with pytest.raises(ValueError, match="must enable smtp, imap, or both"):
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="Primary account",
                        account_access_profile="bot",
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            )
        )


def test_app_config_accepts_smtp_only_account() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="SMTP account",
                        account_access_profile="bot",
                        smtp=SmtpConfig(),
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
                        imap=ImapConfig(
                            default_folder="INBOX",
                            folders={"INBOX": ImapFolderConfig(description="Inbox")},
                        ),
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            )
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
                        smtp=SmtpConfig(),
                        imap=ImapConfig(
                            default_folder="INBOX",
                            folders={"INBOX": ImapFolderConfig(description="Inbox")},
                        ),
                    )
                },
                account_access_profiles={"bot": AccountAccessProfileConfig()},
            )
        )
    )


def test_app_config_accepts_disabled_smtp_send_policy() -> None:
    validate_app_config(
        AppConfig(
            mail=MailConfig(
                accounts={
                    "primary": AccountConfig(
                        description="SMTP account",
                        account_access_profile="bot",
                        smtp=SmtpConfig(),
                    )
                },
                account_access_profiles={
                    "bot": AccountAccessProfileConfig(allow_smtp_send=False)
                },
            )
        )
    )
