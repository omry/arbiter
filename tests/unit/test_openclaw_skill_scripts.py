from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACTIVE_PATH = (
    REPO_ROOT
    / "openclaw_skills/send-email-interactive/scripts/send_email_interactive.py"
)
PREDEFINED_PATH = (
    REPO_ROOT / "openclaw_skills/send-email-predefined/scripts/send_email_predefined.py"
)
SHARED_PATH = REPO_ROOT / "openclaw_skills/_shared/scripts/agent_arbiter_client.py"


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_interactive_build_arguments_normalizes_optional_lists() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script")

    class Args:
        to = "a@example.com, b@example.com"
        subject = "Hello"
        text_body = "Body"
        html_body = None
        cc = "cc@example.com"
        bcc = None

    assert module.build_arguments_with_bodies(
        Args(),
        account="primary",
        text_body="Body",
        html_body=None,
    ) == {
        "account": "primary",
        "to": ["a@example.com", "b@example.com"],
        "subject": "Hello",
        "text_body": "Body",
        "cc": ["cc@example.com"],
    }


def test_interactive_resolve_bodies_uses_stdin_for_text_body() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_stdin_text")

    class Args:
        text_body = None
        html_body = None
        text_stdin = True
        html_stdin = False

    text_body, html_body = module.resolve_bodies(
        Args(),
        stdin_text="Hello\n\nWorld",
        stdin_is_tty=False,
    )

    assert text_body == "Hello\n\nWorld"
    assert html_body is None


def test_interactive_resolve_bodies_uses_stdin_for_html_body() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_stdin_html")

    class Args:
        text_body = None
        html_body = None
        text_stdin = False
        html_stdin = True

    text_body, html_body = module.resolve_bodies(
        Args(),
        stdin_text="<p>Hello</p>",
        stdin_is_tty=False,
    )

    assert text_body is None
    assert html_body == "<p>Hello</p>"


def test_interactive_resolve_bodies_rejects_combining_stdin_with_body_args() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_conflict")

    class Args:
        text_body = "Body"
        html_body = None
        text_stdin = True
        html_stdin = False

    with pytest.raises(ValueError, match="cannot combine stdin body input"):
        module.resolve_bodies(
            Args(),
            stdin_text="Hello",
            stdin_is_tty=False,
        )


def test_interactive_resolve_bodies_requires_explicit_stdin_flag_when_stdin_is_used() -> (
    None
):
    module = _load_module(
        INTERACTIVE_PATH, "interactive_skill_script_missing_stdin_flag"
    )

    class Args:
        text_body = None
        html_body = None
        text_stdin = False
        html_stdin = False

    with pytest.raises(
        ValueError, match="pass exactly one of --text-stdin or --html-stdin"
    ):
        module.resolve_bodies(
            Args(),
            stdin_text="Hello",
            stdin_is_tty=False,
        )


def test_interactive_resolve_bodies_rejects_both_stdin_flags() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_both_stdin_flags")

    class Args:
        text_body = None
        html_body = None
        text_stdin = True
        html_stdin = True

    with pytest.raises(
        ValueError, match="cannot use both --text-stdin and --html-stdin"
    ):
        module.resolve_bodies(
            Args(),
            stdin_text="Hello",
            stdin_is_tty=False,
        )


def test_interactive_resolve_bodies_requires_body_when_no_stdin_or_args() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_missing_body")

    class Args:
        text_body = None
        html_body = None
        text_stdin = False
        html_stdin = False

    with pytest.raises(
        ValueError, match="at least one of --text-body or --html-body is required"
    ):
        module.resolve_bodies(
            Args(),
            stdin_text=None,
            stdin_is_tty=True,
        )


def test_interactive_list_smtp_accounts_filters_disallowed_smtp_entries() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_accounts")

    def fake_call_tool_sync(config, tool_name, arguments):
        assert tool_name == "list_accounts"
        assert arguments == {}
        return {
            "accounts": [
                {
                    "name": "primary",
                    "smtp": {"send": "allowed", "require_confirmation": False},
                },
                {
                    "name": "personal",
                    "smtp": {"send": "unavailable", "require_confirmation": True},
                },
                {
                    "name": "secondary",
                    "smtp": {"send": "unavailable", "require_confirmation": True},
                },
            ]
        }

    module.call_tool_sync = fake_call_tool_sync

    assert module.list_smtp_accounts(object()) == [
        {
            "name": "primary",
            "smtp": {"send": "allowed", "require_confirmation": False},
        }
    ]


def test_interactive_list_smtp_accounts_rejects_invalid_account_shapes() -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_invalid_accounts")

    def fake_call_tool_sync(config, tool_name, arguments):
        assert tool_name == "list_accounts"
        assert arguments == {}
        return {
            "accounts": [
                {
                    "name": "primary",
                    "smtp": {"send": "allowed", "require_confirmation": False},
                },
                {
                    "name": "broken",
                    "smtp": "allowed",
                },
            ]
        }

    module.call_tool_sync = fake_call_tool_sync

    with pytest.raises(ValueError, match="list_accounts returned an invalid response"):
        module.list_smtp_accounts(object())


def test_interactive_select_account_requires_explicit_choice_when_multiple_accounts() -> (
    None
):
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_multi_accounts")
    accounts = [
        {
            "name": "primary",
            "smtp": {"require_confirmation": False},
            "description": "Bot",
        },
        {
            "name": "personal",
            "smtp": {"require_confirmation": True},
            "description": "Personal",
        },
    ]

    with pytest.raises(
        ValueError, match="multiple SMTP-enabled accounts are available"
    ):
        module.select_account(None, accounts)


def test_interactive_select_account_allows_single_smtp_account_without_explicit_choice() -> (
    None
):
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_single_account")
    accounts = [
        {
            "name": "primary",
            "smtp": {"require_confirmation": False},
            "description": "Bot",
        },
    ]

    assert module.select_account(None, accounts) == accounts[0]


def test_interactive_run_passes_selected_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_run_account")

    class Args:
        list_accounts = False
        account = "primary"
        to = "a@example.com"
        subject = "Hello"
        text_body = "Body"
        html_body = None
        text_stdin = False
        html_stdin = False
        cc = None
        bcc = None
        confirm_smtp_send = False

    captured: dict[str, object] = {}

    monkeypatch.setattr(module, "config_from_env", lambda: object())
    monkeypatch.setattr(
        module,
        "list_smtp_accounts",
        lambda config: [
            {
                "name": "primary",
                "smtp": {"require_confirmation": False},
                "description": "Bot",
            }
        ],
    )

    def fake_call_tool_sync(config, tool_name, arguments):
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {"ok": True}

    monkeypatch.setattr(module, "call_tool_sync", fake_call_tool_sync)

    result = module.run(
        Args(),
        stdin_reader=lambda: "",
        stdin_is_tty=True,
    )

    assert captured == {
        "tool_name": "send_email",
        "arguments": {
            "account": "primary",
            "to": ["a@example.com"],
            "subject": "Hello",
            "text_body": "Body",
        },
    }
    assert result == {
        "ok": True,
        "account": "primary",
        "account_description": "Bot",
        "account_smtp_requires_confirmation": False,
    }


def test_interactive_run_requires_confirmation_for_confirmed_smtp_send_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        INTERACTIVE_PATH, "interactive_skill_script_sensitive_account"
    )

    class Args:
        list_accounts = False
        account = "personal"
        to = "a@example.com"
        subject = "Hello"
        text_body = "Body"
        html_body = None
        text_stdin = False
        html_stdin = False
        cc = None
        bcc = None
        confirm_smtp_send = False

    monkeypatch.setattr(module, "config_from_env", lambda: object())
    monkeypatch.setattr(
        module,
        "list_smtp_accounts",
        lambda config: [
            {
                "name": "personal",
                "smtp": {"require_confirmation": True},
                "description": "Personal",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"selected account personal \(Personal\) requires explicit confirmation for SMTP send",
    ):
        module.run(
            Args(),
            stdin_reader=lambda: "",
            stdin_is_tty=True,
        )


def test_interactive_run_lists_accounts_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(INTERACTIVE_PATH, "interactive_skill_script_list_mode")

    class Args:
        list_accounts = True
        account = None
        to = None
        subject = None
        text_body = None
        html_body = None
        text_stdin = False
        html_stdin = False
        cc = None
        bcc = None
        confirm_smtp_send = False

    monkeypatch.setattr(module, "config_from_env", lambda: object())
    monkeypatch.setattr(
        module,
        "list_smtp_accounts",
        lambda config: [
            {
                "name": "primary",
                "smtp": {"require_confirmation": False},
                "description": "Bot",
            }
        ],
    )

    assert module.run(
        Args(),
        stdin_reader=lambda: "",
        stdin_is_tty=True,
    ) == {
        "accounts": [
            {
                "name": "primary",
                "smtp": {"require_confirmation": False},
                "description": "Bot",
            }
        ]
    }


def test_predefined_build_payload_renders_template_values() -> None:
    module = _load_module(PREDEFINED_PATH, "predefined_skill_script")

    template = {
        "account": "primary",
        "subject": "Alert: {title}",
        "text_body": "Severity: {severity}",
        "to": ["ops+{severity}@example.com"],
        "cc": ["audit@example.com"],
    }

    assert module.build_payload(
        template,
        {"title": "Disk Full", "severity": "critical"},
    ) == {
        "account": "primary",
        "to": ["ops+critical@example.com"],
        "cc": ["audit@example.com"],
        "subject": "Alert: Disk Full",
        "text_body": "Severity: critical",
    }


def test_predefined_build_payload_rejects_unexpected_params() -> None:
    module = _load_module(PREDEFINED_PATH, "predefined_skill_script_unexpected")

    template = {
        "account": "primary",
        "subject": "Alert: {title}",
        "text_body": "{summary}",
        "to": ["ops@example.com"],
        "allowed_params": ["title"],
    }

    with pytest.raises(ValueError, match="unexpected template parameters"):
        module.build_payload(
            template,
            {"title": "Disk Full", "summary": "bad"},
        )


def test_predefined_build_payload_requires_account() -> None:
    module = _load_module(PREDEFINED_PATH, "predefined_skill_script_missing_account")

    template = {
        "subject": "Alert: {title}",
        "text_body": "{summary}",
        "to": ["ops@example.com"],
    }

    with pytest.raises(ValueError, match="template account is required"):
        module.build_payload(
            template,
            {"title": "Disk Full", "summary": "bad"},
        )


def test_predefined_default_registry_path_points_next_to_skill() -> None:
    module = _load_module(PREDEFINED_PATH, "predefined_skill_script_registry_path")

    assert (
        module.default_registry_path() == PREDEFINED_PATH.parents[1] / "templates.json"
    )


def test_shared_parse_json_argument_requires_object() -> None:
    module = _load_module(SHARED_PATH, "shared_skill_client")

    with pytest.raises(ValueError, match="decode to an object"):
        module.parse_json_argument('["not-an-object"]')
