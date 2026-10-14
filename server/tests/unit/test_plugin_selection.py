from pathlib import Path
from copy import deepcopy
import json

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.config_store import ConfigStore
from hydra.errors import MissingConfigException
from omegaconf import OmegaConf

from arbiter_server.main import compose_config, main, build_app, ensure_runnable_config
from arbiter_server.config import (
    AppConfig,
    ArbiterConfig,
    StorageConfig,
    register_configs,
    configured_service_names,
)
from arbiter_server.plugin_selection import selected_service_names
from arbiter_server.plugins import discover_service_plugins
from arbiter_imap import IMAPServicePlugin
from arbiter_imap.config import (
    IMAPConfig,
    IMAPAccessPolicyConfig,
    IMAPFolderAccessConfig,
    IMAPFolderAccessRuleConfig,
)
from arbiter_smtp import SMTPServicePlugin


def _write(root: Path, path: str, text: str) -> None:
    file = root / f"{path}.yaml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


def _select(root: Path, overrides: list[str] | None = None) -> list[str]:
    register_configs()
    with initialize_config_dir(version_base=None, config_dir=str(root)):
        return selected_service_names(config_name="config", overrides=overrides or [])


def test_raw_defaults_follow_selected_files_without_resolving_secrets(tmp_path: Path):
    _write(tmp_path, "config", "defaults: [plugins, _self_]\n")
    _write(
        tmp_path,
        "plugins",
        "defaults:\n  - arbiter/account: [imap/default]\n"
        "  - arbiter/policy: [imap/readonly]\n",
    )
    _write(
        tmp_path,
        "arbiter/account/imap/default",
        "# @package arbiter.account.imap.default\n"
        "defaults: [schema@_here_, _self_]\npassword: ${oc.env:ABSENT_SECRET}\n",
    )
    _write(tmp_path, "arbiter/policy/imap/readonly", "{}\n")
    _write(tmp_path, "unused", "arbiter: {account: {smtp: {default: {}}}}\n")
    assert _select(tmp_path) == ["imap"]


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ([], ["imap"]),
        (["arbiter/account=smtp/default"], ["smtp"]),
        (["~arbiter/account"], []),
        (
            ["+arbiter/account/smtp@arbiter.account.smtp.default=schema"],
            ["imap", "smtp"],
        ),
    ],
)
def test_raw_group_overrides_are_applied_before_import(
    tmp_path: Path, overrides: list[str], expected: list[str]
):
    _write(tmp_path, "config", "defaults:\n  - arbiter/account: imap/default\n")
    _write(tmp_path, "arbiter/account/imap/default", "{}\n")
    _write(tmp_path, "arbiter/account/smtp/default", "{}\n")
    assert _select(tmp_path, overrides) == expected


def test_raw_inline_accounts_and_explicit_config_overrides(tmp_path: Path):
    _write(
        tmp_path,
        "config",
        "arbiter: {account: {imap: {primary: {host: mail}}, smtp: {}}}\n",
    )
    assert _select(tmp_path) == ["imap"]
    assert _select(tmp_path, ["+arbiter.account.smtp.primary.host=mail"]) == [
        "imap",
        "smtp",
    ]
    assert _select(tmp_path, ["~arbiter.account.imap"]) == []
    assert _select(tmp_path, ["~arbiter.account.imap.primary"]) == []
    assert _select(tmp_path, ["+arbiter.account={smtp:{primary:{host:mail}}}"]) == [
        "imap",
        "smtp",
    ]


def test_null_group_does_not_select_plugin(tmp_path: Path):
    _write(tmp_path, "config", "defaults:\n  - arbiter/account/imap: null\n")
    assert _select(tmp_path) == []


def test_missing_selected_config_keeps_hydra_error(tmp_path: Path):
    _write(tmp_path, "config", "defaults: [missing]\n")
    with pytest.raises(MissingConfigException, match="missing"):
        _select(tmp_path)


def _poison_inactive(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    loaded: list[str] = []

    class Point:
        def __init__(self, name: str):
            self.name = name
            self.value = f"{name}:plugin"

        def load(self):
            loaded.append(self.name)
            if self.name == "poison":
                raise AssertionError("inactive plugin was executed")
            return {"imap": IMAPServicePlugin, "smtp": SMTPServicePlugin}[self.name]

    class Points(list):
        def select(self, *, group: str):
            assert group == "arbiter.services"
            return self

    points = Points([Point("imap"), Point("smtp"), Point("poison")])
    monkeypatch.setattr("arbiter_server.config.entry_points", lambda: points)
    monkeypatch.setattr("arbiter_server.plugins.entry_points", lambda: points)
    return loaded


def test_composition_skips_inactive_entry_points_even_with_schemas_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults:\n  - arbiter_app_config_schema\n"
        "  - /arbiter/account/imap@arbiter.account.imap.primary: schema\n"
        "  - _self_\n",
    )
    cfg = compose_config(config_dir=tmp_path, config_name="config")
    assert cfg.arbiter.account.imap.primary.host == "localhost"
    assert loaded == ["imap"]
    loaded.clear()
    assert [p.name for p in discover_service_plugins(names=["imap"])] == ["imap"]
    assert loaded == ["imap"]


def test_empty_config_and_targeted_bootstrap_do_not_import_other_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    assert main(["--config-dir", str(tmp_path), "bootstrap", "--server"]) == 0
    assert loaded == []
    assert (
        main(
            [
                "--config-dir",
                str(tmp_path),
                "bootstrap",
                "--plugin",
                "imap",
                "--account",
                "primary",
            ]
        )
        == 0
    )
    assert loaded and set(loaded) == {"imap"}


def test_runtime_and_empty_config_diagnostic_skip_inactive_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    cfg = AppConfig(
        arbiter=ArbiterConfig(
            account={"imap": {"primary": IMAPConfig()}},
            policy={
                "imap": {
                    "bot": IMAPAccessPolicyConfig(
                        folder_access=IMAPFolderAccessConfig(
                            rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
                        )
                    )
                }
            },
            storage=StorageConfig(plugin_data_dir=str(tmp_path / "data")),
        )
    )
    app = build_app(cfg)
    assert set(app.runtime_registry.keys()) == {"imap"}
    assert loaded == ["imap"]
    loaded.clear()
    with pytest.raises(ValueError, match="config must define at least one"):
        ensure_runnable_config(AppConfig())
    assert loaded == []


@pytest.mark.parametrize("removed", [False, True])
def test_optional_schema_selection_respects_group_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed: bool
):
    loaded = _poison_inactive(monkeypatch)
    _write(tmp_path, "config", "defaults:\n  - optional arbiter/account/imap: schema\n")
    overrides = ["~arbiter/account/imap"] if removed else []
    assert _select(tmp_path, overrides) == ([] if removed else ["imap"])
    assert loaded == ([] if removed else ["imap"])


def test_invalid_cli_null_selection_does_not_load_schema_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(tmp_path, "config", "defaults:\n  - arbiter/account/imap: schema\n")
    with pytest.raises(ValueError, match="Config group override must be"):
        _select(tmp_path, ["arbiter/account/imap=null"])
    assert loaded == []


def test_nested_default_override_does_not_import_replaced_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults:\n  - choice: first\n  - override arbiter/account: smtp/default\n",
    )
    _write(
        tmp_path,
        "choice/first",
        "# @package _global_\ndefaults:\n  - /arbiter/account: imap/default\n",
    )
    _write(tmp_path, "arbiter/account/imap/default", "{}\n")
    _write(tmp_path, "arbiter/account/smtp/default", "{}\n")
    assert _select(tmp_path) == ["smtp"]
    assert loaded == ["smtp"]


def test_searchpath_raw_activation_and_defaults_interpolation(tmp_path: Path):
    extra = tmp_path / "extra"
    _write(
        extra,
        "selected",
        "# @package _global_\narbiter: {account: {imap: {primary: {host: mail}}}}\n",
    )
    _write(
        tmp_path,
        "config",
        f"defaults: [selected, _self_]\nhydra:\n  searchpath: [file://{extra}]\n",
    )
    assert _select(tmp_path) == ["imap"]
    _write(
        tmp_path,
        "config",
        "defaults:\n  - choice: smtp\n  - arbiter/account: ${choice}/default\n",
    )
    _write(tmp_path, "choice/smtp", "{}\n")
    _write(tmp_path, "arbiter/account/smtp/default", "{}\n")
    assert _select(tmp_path) == ["smtp"]


@pytest.mark.parametrize("deleted", [False, True])
def test_cold_schema_group_selection_does_not_require_import_for_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deleted: bool
):
    # Initialize Hydra's own one-time plugin schemas before isolating the
    # service schema store, so restoring the fixture cannot discard them.
    with initialize_config_dir(version_base=None, config_dir=str(tmp_path)):
        pass
    store = ConfigStore.instance()
    repository = deepcopy(store.repo)
    for domain in ("account", "policy"):
        repository.get("arbiter", {}).get(domain, {}).pop("imap", None)
    monkeypatch.setattr(store, "repo", repository)
    monkeypatch.setattr("arbiter_server.config._CONFIG_REGISTERED", False)
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults:\n  - arbiter_app_config_schema\n"
        "  - arbiter/account/imap@arbiter.account.imap.primary: schema\n"
        "  - _self_\n",
    )
    overrides = (
        ["~arbiter/account/imap@arbiter.account.imap.primary"] if deleted else []
    )
    cfg = compose_config(config_dir=tmp_path, config_name="config", overrides=overrides)
    assert loaded == ([] if deleted else ["imap"])
    if deleted:
        assert dict(cfg.arbiter.account) == {}
        assert store.list("arbiter/account/imap") == []
    else:
        assert cfg.arbiter.account.imap.primary.host == "localhost"


@pytest.mark.parametrize("inactive", [None, {}])
def test_interpolated_inactive_account_map_does_not_import_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inactive: object
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, _self_]\n"
        "arbiter:\n  account:\n    imap: ${arbiter.etc.inactive}\n"
        f"  etc:\n    inactive: {json.dumps(inactive)}\n",
    )
    cfg = compose_config(config_dir=tmp_path, config_name="config")
    assert loaded == []
    assert configured_service_names(cfg.arbiter.account) == []
    with pytest.raises(ValueError, match="config must define at least one"):
        ensure_runnable_config(cfg)
    assert loaded == []


def test_interpolated_active_account_map_loads_only_its_runtime_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, _self_]\n"
        "arbiter:\n  account:\n    imap: ${arbiter.etc.accounts}\n"
        "  etc:\n    accounts:\n      primary:\n        policy: bot\n",
    )
    cfg = compose_config(config_dir=tmp_path, config_name="config")
    assert loaded == []
    cfg.arbiter.etc.accounts.primary = IMAPConfig()
    cfg.arbiter.policy = {
        "imap": {
            "bot": IMAPAccessPolicyConfig(
                folder_access=IMAPFolderAccessConfig(
                    rules=[IMAPFolderAccessRuleConfig(allow_glob="*")]
                )
            )
        }
    }
    cfg.arbiter.storage.plugin_data_dir = str(tmp_path / "data")
    app = build_app(cfg)
    assert set(app.runtime_registry.keys()) == {"imap"}
    assert loaded == ["imap"]


def _native_compose(root: Path, overrides: list[str] | None = None):
    register_configs()
    with initialize_config_dir(version_base=None, config_dir=str(root)):
        return compose(config_name="config", overrides=overrides or [])


def test_whole_account_interpolation_replaces_discarded_inline_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, base, _self_]\n"
        "arbiter:\n"
        "  account: ${arbiter.etc.empty}\n"
        "  etc: {empty: {}}\n",
    )
    _write(
        tmp_path,
        "base",
        "arbiter: {account: {imap: {primary: {host: mail}}}}\n",
    )

    cfg = compose_config(config_dir=tmp_path, config_name="config")
    native = _native_compose(tmp_path)

    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == {}
    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == (
        OmegaConf.to_container(native.arbiter.account, resolve=False)
    )
    assert loaded == []


def test_parent_account_override_replaces_discarded_inline_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, base, _self_]\n"
        "arbiter:\n"
        "  etc: {empty: {}}\n",
    )
    _write(
        tmp_path,
        "base",
        "arbiter: {account: {imap: {primary: {host: mail}}}}\n",
    )
    overrides = ["arbiter={account:${arbiter.etc.empty}}"]

    cfg = compose_config(config_dir=tmp_path, config_name="config", overrides=overrides)
    native = _native_compose(tmp_path, overrides)

    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == {}
    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == (
        OmegaConf.to_container(native.arbiter.account, resolve=False)
    )
    assert loaded == []


def test_whole_policy_interpolation_replaces_discarded_inline_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, base, _self_]\n"
        "arbiter:\n"
        "  policy: ${arbiter.etc.empty}\n"
        "  etc: {empty: {}}\n",
    )
    _write(
        tmp_path,
        "base",
        "arbiter: {policy: {imap: {readonly: {allow: true}}}}\n",
    )

    cfg = compose_config(config_dir=tmp_path, config_name="config")
    native = _native_compose(tmp_path)

    assert OmegaConf.to_container(cfg.arbiter.policy, resolve=False) == {}
    assert OmegaConf.to_container(cfg.arbiter.policy, resolve=False) == (
        OmegaConf.to_container(native.arbiter.policy, resolve=False)
    )
    assert loaded == []


def test_whole_arbiter_interpolation_replaces_discarded_account_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [base, _self_]\n"
        "arbiter: ${replacement}\n"
        "replacement: {account: {}, policy: {}}\n",
    )
    _write(
        tmp_path,
        "base",
        "arbiter: {account: {imap: {primary: {host: mail}}}}\n",
    )

    cfg = compose_config(config_dir=tmp_path, config_name="config")
    native = _native_compose(tmp_path)

    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == {}
    assert OmegaConf.to_container(cfg.arbiter.account, resolve=False) == (
        OmegaConf.to_container(native.arbiter.account, resolve=False)
    )
    assert loaded == []


def test_whole_arbiter_interpolation_replaces_discarded_policy_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [base, _self_]\n"
        "arbiter: ${replacement}\n"
        "replacement:\n"
        "  account: {}\n"
        "  policy: {}\n"
        '  secret: "${oc.env:UNUSED_SECRET}"\n',
    )
    _write(
        tmp_path,
        "base",
        "arbiter: {policy: {imap: {readonly: {allow: true}}}}\n",
    )

    cfg = compose_config(config_dir=tmp_path, config_name="config")
    native = _native_compose(tmp_path)

    assert OmegaConf.to_container(cfg.arbiter.policy, resolve=False) == {}
    assert OmegaConf.to_container(cfg.arbiter.policy, resolve=False) == (
        OmegaConf.to_container(native.arbiter.policy, resolve=False)
    )
    raw = OmegaConf.to_container(cfg, resolve=False)
    assert isinstance(raw, dict)
    replacement = raw["replacement"]
    assert isinstance(replacement, dict)
    assert replacement["secret"] == "${oc.env:UNUSED_SECRET}"
    assert loaded == []


@pytest.mark.parametrize(
    "override, path",
    [
        ("~arbiter.account.imap.primary", "arbiter.account.imap"),
        ("~arbiter.account.imap.primary.host", "arbiter.account.imap"),
        ("~arbiter.policy.imap.primary", "arbiter.policy.imap"),
        ("~arbiter.policy.imap.primary.allow", "arbiter.policy.imap"),
    ],
)
def test_interpolated_deletions_match_native_without_resolving_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str,
    path: str,
):
    loaded = _poison_inactive(monkeypatch)
    _write(
        tmp_path,
        "config",
        "defaults: [arbiter_app_config_schema, _self_]\n"
        "arbiter:\n"
        "  account:\n"
        "    imap: ${arbiter.etc.accounts}\n"
        "  policy:\n"
        "    imap: ${arbiter.etc.policies}\n"
        "  etc:\n"
        "    accounts:\n"
        "      primary:\n"
        "        host: localhost\n"
        '        secret: "${oc.env:UNUSED_SECRET}"\n'
        "      secondary:\n"
        "        host: localhost\n"
        "    policies:\n"
        "      primary: {allow: true}\n"
        "      secondary: {allow: false}\n",
    )

    cfg = compose_config(
        config_dir=tmp_path, config_name="config", overrides=[override]
    )
    native = _native_compose(tmp_path, [override])

    actual = OmegaConf.to_container(OmegaConf.select(cfg, path), resolve=False)
    expected = OmegaConf.to_container(OmegaConf.select(native, path), resolve=False)
    assert actual == expected
    raw = OmegaConf.to_container(cfg, resolve=False)
    native_raw = OmegaConf.to_container(native, resolve=False)
    assert isinstance(raw, dict)
    assert isinstance(native_raw, dict)
    raw_arbiter = raw["arbiter"]
    native_raw_arbiter = native_raw["arbiter"]
    assert isinstance(raw_arbiter, dict)
    assert isinstance(native_raw_arbiter, dict)
    raw_etc = raw_arbiter["etc"]
    native_raw_etc = native_raw_arbiter["etc"]
    assert isinstance(raw_etc, dict)
    assert isinstance(native_raw_etc, dict)
    assert raw_etc["accounts"] == native_raw_etc["accounts"]
    assert loaded == []
