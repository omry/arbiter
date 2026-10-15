from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from arbiter_server.config import (
    AppConfig,
    ServerTlsSource,
    _register_resolvers,
    config_for_display,
    display_config_select,
    display_config_yaml,
)
from arbiter_server.main import _run_config_show, compose_config


@pytest.mark.parametrize(
    ("resolve", "package", "value"),
    [
        (True, None, False),
        (True, "values", False),
        (False, "values.env", True),
        (False, "values.file", True),
        (False, "values.env", False),
        (False, "values.indirect", True),
        (True, "values.embedded", True),
        (True, "values.nested", False),
    ],
)
def test_config_show_masks_resolvers_before_selection(
    resolve: bool,
    package: str | None,
    value: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_SECRET", "synthetic-env-secret")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("synthetic-file-secret\n", encoding="utf-8")
    cfg = OmegaConf.create(
        {
            "values": {
                "env": "${oc.env:ARBITER_DISPLAY_TEST_SECRET}",
                "file": "${secret_file:" + str(secret_file) + "}",
                "indirect": "${.env}",
                "embedded": "Bearer ${.env}",
                "nested": [{"credential": "${oc.env:ARBITER_DISPLAY_TEST_SECRET}"}],
            }
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=resolve,
            package=package,
            value=value,
        )
        == 0
    )
    output = capsys.readouterr()
    assert "<REDACTED>" in output.out
    assert "synthetic-env-secret" not in output.out + output.err
    assert "synthetic-file-secret" not in output.out + output.err
    # Display must not replace real values or leave redaction enabled at runtime.
    assert cfg["values"].env == "synthetic-env-secret"
    assert cfg["values"].file == "synthetic-file-secret"


@pytest.mark.parametrize("whole_config", [False, True])
def test_config_show_masks_typed_environment_port_without_changing_runtime(
    whole_config: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_PORT", "18075")
    cfg = OmegaConf.structured(AppConfig())
    cfg.arbiter.server.bind.port = "${oc.env:ARBITER_DISPLAY_TEST_PORT}"
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=whole_config,
            package=None if whole_config else "arbiter.server.bind.port",
            value=not whole_config,
        )
        == 0
    )
    output = capsys.readouterr()
    assert "<REDACTED>" in output.out
    assert "18075" not in output.out + output.err
    assert cfg.arbiter.server.bind.port == 18075
    assert isinstance(cfg.arbiter.server.bind.port, int)


def test_config_show_preserves_non_secret_typed_interpolations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = OmegaConf.structured(AppConfig())
    cfg.arbiter.etc.port = "18075"
    cfg.arbiter.server.bind.port = "${arbiter.etc.port}"
    cfg.arbiter.etc.source = "SELF_SIGNED"
    cfg.arbiter.server.tls.source = "${arbiter.etc.source}"
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)

    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    assert capsys.readouterr().out == OmegaConf.to_yaml(cfg, resolve=True)

    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package="arbiter.server.bind",
            value=False,
        )
        == 0
    )
    assert capsys.readouterr().out == OmegaConf.to_yaml(
        cfg.arbiter.server.bind, resolve=True
    )

    with config_for_display(cfg) as display:
        port = display_config_select(display, "arbiter.server.bind.port", object())
        source = display_config_select(display, "arbiter.server.tls.source", object())
    assert port == cfg.arbiter.server.bind.port == 18075
    assert isinstance(port, int)
    assert source is cfg.arbiter.server.tls.source is ServerTlsSource.SELF_SIGNED
    assert isinstance(source, ServerTlsSource)


def test_display_yaml_preserves_nullable_typed_containers_and_empty_collections() -> (
    None
):
    @dataclass
    class OptionalCollections:
        values: list[str] | None = None
        mapping: dict[str, str] | None = None

    cfg = OmegaConf.create(
        {
            "typed": OmegaConf.structured(OptionalCollections()),
            "value_alias": "${typed.values}",
            "mapping_alias": "${typed.mapping}",
            "nested": {
                "value_alias": "${typed.values}",
                "mapping_alias": "${typed.mapping}",
            },
            "empty_list": [],
            "empty_map": {},
            "typed_empty": OmegaConf.structured(
                OptionalCollections(values=[], mapping={})
            ),
        }
    )
    native = OmegaConf.to_yaml(cfg, resolve=True)
    with config_for_display(cfg) as display:
        actual = display_config_yaml(display, resolve=True)
        typed = display_config_yaml(display.typed, resolve=True)
        nested = display_config_yaml(display.nested, resolve=True)
        value_alias = display_config_select(display, "value_alias", object())
        mapping_alias = display_config_select(display, "mapping_alias", object())

    assert actual == native
    assert typed == OmegaConf.to_yaml(cfg.typed, resolve=True)
    assert nested == OmegaConf.to_yaml(cfg.nested, resolve=True)
    assert value_alias is None
    assert mapping_alias is None
    assert "empty_list: []" in actual
    assert "empty_map: {}" in actual
    assert "typed_empty:\n  values: []\n  mapping: {}" in actual


@pytest.mark.parametrize("whole_config", [False, True])
def test_config_show_masks_nested_decode_environment_port(
    whole_config: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_NESTED_PORT", "18075")
    cfg = OmegaConf.structured(AppConfig())
    cfg.arbiter.server.bind.port = "${oc.decode:${oc.env:ARBITER_DISPLAY_NESTED_PORT}}"
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=whole_config,
            package=None if whole_config else "arbiter.server.bind.port",
            value=not whole_config,
        )
        == 0
    )
    output = capsys.readouterr()
    assert "<REDACTED>" in output.out
    assert "18075" not in output.out + output.err
    assert cfg.arbiter.server.bind.port == 18075
    assert isinstance(cfg.arbiter.server.bind.port, int)


def test_config_show_propagates_redaction_through_nested_expressions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_NESTED_SECRET", "synthetic-secret")
    OmegaConf.register_new_resolver(
        "display_literal_passthrough", lambda value: f"literal={value}"
    )
    try:
        cfg = OmegaConf.create(
            {
                "literal": "<REDACTED>",
                "direct": "${oc.env:ARBITER_DISPLAY_NESTED_SECRET}",
                "indirect": "${.direct}",
                "embedded": "Bearer ${.direct}",
                "container": "${oc.create:{value: ${oc.env:ARBITER_DISPLAY_NESTED_SECRET}}}",
                "nested": {
                    "list": [
                        "${oc.env:ARBITER_DISPLAY_NESTED_SECRET}",
                        "${oc.decode:${oc.env:ARBITER_DISPLAY_NESTED_SECRET}}",
                    ],
                    "dict": {"value": "${oc.env:ARBITER_DISPLAY_NESTED_SECRET}"},
                },
                "ordinary": "${display_literal_passthrough:${.literal}}",
            }
        )
        monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
        assert (
            _run_config_show(
                config_dir=str(tmp_path),
                config_name="unused",
                overrides=[],
                resolve=True,
                package=None,
                value=False,
            )
            == 0
        )
        output = capsys.readouterr()
        assert "synthetic-secret" not in output.out + output.err
        assert "container: <REDACTED>" in output.out
        assert "ordinary: literal=<REDACTED>" in output.out
        assert "literal: <REDACTED>" in output.out
    finally:
        OmegaConf.clear_resolver("display_literal_passthrough")


def test_config_show_keeps_ordinary_interpolation_errors_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    cfg = OmegaConf.create({"value": "${oc.decode:[1,foo]}"})
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 1
    )
    output = capsys.readouterr()
    assert "oc.decode" in output.err
    assert "<REDACTED>" not in output.out + output.err


def test_config_show_does_not_invoke_sensitive_callbacks_for_missing_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from omegaconf.basecontainer import BaseContainer

    _register_resolvers()
    monkeypatch.delenv("ARBITER_DISPLAY_CALLBACK_MISSING", raising=False)
    monkeypatch.setattr(BaseContainer, "_resolvers", BaseContainer._resolvers.copy())
    file_calls: list[str] = []

    def secret_file(path: str) -> str:
        file_calls.append(path)
        return "secret"

    OmegaConf.register_new_resolver("secret_file", secret_file, replace=True)
    cfg = OmegaConf.create(
        {
            "default": "${oc.env:ARBITER_DISPLAY_CALLBACK_MISSING,fallback}",
            "file": "${secret_file:/missing/secret}",
            "nested_file": "${secret_file:${oc.env:ARBITER_DISPLAY_CALLBACK_MISSING}}",
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("<REDACTED>") == 3
    assert file_calls == []


@pytest.mark.parametrize("value", [False, True])
def test_config_show_preserves_literal_enum_scalars(
    value: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = OmegaConf.structured(AppConfig())
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package="arbiter.server.tls.source",
            value=value,
        )
        == 0
    )
    assert capsys.readouterr().out == str(ServerTlsSource.SELF_SIGNED) + "\n"


@pytest.mark.parametrize(
    "literal",
    [r"\${", r"\${not valid}", r"literal \${unfinished"],
)
def test_config_show_resolved_yaml_preserves_escaped_literals(
    literal: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = OmegaConf.create(
        {
            "literal": literal,
            "none": None,
            "missing": "???",
            "unicode": "東京",
            "nested": {"literal": literal},
            "list": [None, literal],
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)

    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    assert capsys.readouterr().out == OmegaConf.to_yaml(cfg, resolve=True)

    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package="nested",
            value=False,
        )
        == 0
    )
    assert capsys.readouterr().out == OmegaConf.to_yaml(cfg.nested, resolve=True)


def test_config_show_resolved_yaml_preserves_enum_keys_and_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DisplayKey(Enum):
        FIRST = "first"

    cfg = OmegaConf.create(
        {
            DisplayKey.FIRST: {
                DisplayKey.FIRST: ServerTlsSource.SELF_SIGNED,
            },
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)

    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    assert capsys.readouterr().out == OmegaConf.to_yaml(cfg, resolve=True)


def test_config_show_does_not_read_missing_secret_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.delenv("ARBITER_DISPLAY_MISSING", raising=False)
    cfg = OmegaConf.create(
        {
            "env": "${oc.env:ARBITER_DISPLAY_MISSING}",
            "file": "${secret_file:" + str(tmp_path / "absent") + "}",
            "nested_file": "${secret_file:${oc.env:ARBITER_DISPLAY_MISSING}}",
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("<REDACTED>") == 3


def test_config_show_does_not_reuse_resolved_secret_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_SECRET", "synthetic-cached-secret")
    OmegaConf.register_new_resolver(
        "display_test_cached", lambda value: value, use_cache=True
    )
    try:
        cfg = OmegaConf.create(
            {"value": "${display_test_cached:${oc.env:ARBITER_DISPLAY_TEST_SECRET}}"}
        )
        assert cfg.value == "synthetic-cached-secret"
        monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
        assert (
            _run_config_show(
                config_dir=str(tmp_path),
                config_name="unused",
                overrides=[],
                resolve=True,
                package=None,
                value=False,
            )
            == 0
        )
        output = capsys.readouterr()
        assert "<REDACTED>" in output.out
        assert "synthetic-cached-secret" not in output.out + output.err
        assert cfg.value == "synthetic-cached-secret"
    finally:
        OmegaConf.clear_resolver("display_test_cached")


def test_config_show_restores_runtime_resolution_after_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_SECRET", "synthetic-runtime-secret")
    cfg = OmegaConf.create({"secret": "${oc.env:ARBITER_DISPLAY_TEST_SECRET}"})
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package="missing",
            value=True,
        )
        == 1
    )
    output = capsys.readouterr()
    assert "config package not found" in output.err
    assert "synthetic-runtime-secret" not in output.out + output.err
    assert cfg.secret == "synthetic-runtime-secret"


def test_display_does_not_redact_other_runtime_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_resolvers()
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_SECRET", "synthetic-runtime-secret")
    cfg = OmegaConf.create({"secret": "${oc.env:ARBITER_DISPLAY_TEST_SECRET}"})
    with config_for_display(cfg) as display:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert (
                pool.submit(lambda: cfg.secret).result() == "synthetic-runtime-secret"
            )
        assert display_config_select(display, "secret", object()) == "<REDACTED>"
    assert cfg.secret == "synthetic-runtime-secret"


def test_default_unresolved_display_preserves_references_and_literal_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_resolvers()
    monkeypatch.delenv("ARBITER_DISPLAY_MISSING", raising=False)
    cfg = OmegaConf.create(
        {
            "secret": "${oc.env:ARBITER_DISPLAY_MISSING}",
            "literal": "synthetic-literal-secret",
        }
    )
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=False,
            package=None,
            value=False,
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "${oc.env:ARBITER_DISPLAY_MISSING}" in output.out
    assert "synthetic-literal-secret" in output.out


@pytest.mark.parametrize("scalar", [False, True])
def test_real_composition_masks_display_but_preserves_runtime_secrets(
    scalar: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ARBITER_DISPLAY_TEST_SECRET", "synthetic-composed-secret")
    secret = tmp_path / "secret.txt"
    secret.write_text("synthetic-composed-file\n", encoding="utf-8")
    (tmp_path / "arbiter-server.yaml").write_text(
        "defaults:\n  - _self_\n"
        "credential: ${oc.env:ARBITER_DISPLAY_TEST_SECRET}\n"
        "file: ${secret_file:" + str(secret) + "}\n",
        encoding="utf-8",
    )
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="arbiter-server",
            overrides=[],
            resolve=not scalar,
            package="credential" if scalar else None,
            value=scalar,
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert "<REDACTED>" in output.out
    assert "synthetic-composed-secret" not in output.out
    assert "synthetic-composed-file" not in output.out
    runtime = compose_config(config_dir=tmp_path, config_name="arbiter-server")
    assert runtime.credential == "synthetic-composed-secret"
    assert runtime.file == "synthetic-composed-file"


@pytest.mark.parametrize("late_registration", [False, True])
def test_display_preserves_registered_resolvers_and_their_caches(
    late_registration: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from omegaconf.basecontainer import BaseContainer

    import arbiter_server.config as config_module

    monkeypatch.setattr(BaseContainer, "_resolvers", BaseContainer._resolvers.copy())
    monkeypatch.setattr(config_module, "_RESOLVERS_REGISTERED", False)
    if late_registration:
        _register_resolvers()
    calls: list[str] = []

    def env(_name: str) -> str:
        calls.append("env")
        return "custom-env-secret"

    def file(_path: str) -> str:
        calls.append("file")
        return "custom-file-secret"

    OmegaConf.register_new_resolver("oc.env", env, replace=True, use_cache=True)
    OmegaConf.register_new_resolver("secret_file", file, replace=True, use_cache=True)
    _register_resolvers()
    cfg = OmegaConf.create({"env": "${oc.env:custom}", "file": "${secret_file:custom}"})
    assert cfg.env == "custom-env-secret"
    assert cfg.file == "custom-file-secret"
    assert calls == ["env", "file"]
    monkeypatch.setattr("arbiter_server.main.compose_config", lambda **_kwargs: cfg)
    assert (
        _run_config_show(
            config_dir=str(tmp_path),
            config_name="unused",
            overrides=[],
            resolve=True,
            package=None,
            value=False,
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.out.count("<REDACTED>") == 2
    assert "custom-env-secret" not in output.out + output.err
    assert "custom-file-secret" not in output.out + output.err
    assert cfg.env == "custom-env-secret"
    assert cfg.file == "custom-file-secret"
    assert calls == ["env", "file"]


def test_display_yaml_reuses_native_cache_for_nested_aliases() -> None:
    calls: list[int] = []

    def stamp() -> int:
        calls.append(len(calls) + 1)
        return calls[-1]

    OmegaConf.register_new_resolver(
        "display_stamp_cache", stamp, replace=True, use_cache=False
    )
    try:
        cfg = OmegaConf.create(
            {
                "alias": "${source}",
                "source": "${display_stamp_cache:}",
                "independent": "${display_stamp_cache:}",
                "nested": {
                    "alias": "${source}",
                    "list": ["${source}", "${display_stamp_cache:}"],
                },
            }
        )
        native = OmegaConf.to_yaml(cfg, resolve=True)
        native_calls = list(calls)
        calls.clear()
        with config_for_display(cfg) as display:
            actual = display_config_yaml(display, resolve=True)
        assert actual == native
        assert native_calls == [1, 2, 3]
        assert calls == native_calls
    finally:
        OmegaConf.clear_resolver("display_stamp_cache")


def test_display_yaml_supports_once_only_uncached_alias_source() -> None:
    calls: list[int] = []

    def once() -> int:
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("ordinary resolver evaluated twice")
        return 18075

    OmegaConf.register_new_resolver(
        "display_once_cache", once, replace=True, use_cache=False
    )
    try:
        cfg = OmegaConf.create(
            {
                "alias": "${source}",
                "source": "${display_once_cache:}",
                "nested": [{"alias": "${source}"}],
            }
        )
        native = OmegaConf.to_yaml(cfg, resolve=True)
        assert calls == [1]
        calls.clear()
        with config_for_display(cfg) as display:
            actual = display_config_yaml(display, resolve=True)
        assert actual == native
        assert calls == [1]
    finally:
        OmegaConf.clear_resolver("display_once_cache")
