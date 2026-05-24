#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


_SHARED_SCRIPTS = Path(__file__).resolve().parents[2] / "_shared" / "scripts"
sys.path.insert(0, str(_SHARED_SCRIPTS))

from mail_sentry_client import (  # noqa: E402
    MailSentryClientConfig,
    call_tool_sync,
    config_from_env,
)


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_bodies(
    args: argparse.Namespace,
    *,
    stdin_text: str | None,
    stdin_is_tty: bool,
) -> tuple[str | None, str | None]:
    text_body = args.text_body
    html_body = args.html_body
    text_stdin = args.text_stdin
    html_stdin = args.html_stdin

    if text_stdin and html_stdin:
        raise ValueError("cannot use both --text-stdin and --html-stdin")

    if text_stdin or html_stdin:
        if stdin_is_tty:
            raise ValueError("stdin body flag was provided but stdin is not available")
        if text_body is not None or html_body is not None:
            raise ValueError(
                "cannot combine stdin body input with --text-body or --html-body"
            )

        body_from_stdin = stdin_text if stdin_text is not None else ""
        if not body_from_stdin:
            raise ValueError("stdin body input was empty")

        if html_stdin:
            return None, body_from_stdin
        return body_from_stdin, None

    if not stdin_is_tty:
        raise ValueError(
            "when using stdin body input, pass exactly one of --text-stdin or --html-stdin"
        )

    if text_body is None and html_body is None:
        raise ValueError(
            "at least one of --text-body or --html-body is required when stdin is not provided"
        )

    return text_body, html_body


def build_arguments(args: argparse.Namespace, *, account: str) -> dict[str, object]:
    return build_arguments_with_bodies(
        args,
        account=account,
        text_body=args.text_body,
        html_body=args.html_body,
    )


def build_arguments_with_bodies(
    args: argparse.Namespace,
    *,
    account: str,
    text_body: str | None,
    html_body: str | None,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "account": account,
        "to": _csv_list(args.to),
        "subject": args.subject,
    }

    if args.cc:
        arguments["cc"] = _csv_list(args.cc)
    if args.bcc:
        arguments["bcc"] = _csv_list(args.bcc)
    if text_body is not None:
        arguments["text_body"] = text_body
    if html_body is not None:
        arguments["html_body"] = html_body

    return arguments


def list_smtp_accounts(config: MailSentryClientConfig) -> list[dict[str, object]]:
    result = call_tool_sync(config, "list_accounts", {})
    accounts = result.get("accounts")
    if not isinstance(accounts, list):
        raise ValueError("list_accounts returned an invalid response")

    smtp_accounts: list[dict[str, object]] = []
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("list_accounts returned an invalid response")
        smtp = account.get("smtp")
        if not isinstance(smtp, dict):
            raise ValueError("list_accounts returned an invalid response")
        if smtp.get("send") != "allowed":
            continue
        smtp_accounts.append(account)

    if not smtp_accounts:
        raise ValueError("no SMTP-enabled accounts are available")

    return smtp_accounts


def _format_account_choices(accounts: list[dict[str, object]]) -> str:
    formatted: list[str] = []
    for account in accounts:
        name = str(account.get("name", "<unknown>"))
        tier = str(account.get("sensitivity_tier", "standard"))
        description = str(account.get("description", "")).strip()
        if description:
            formatted.append(f"{name} ({tier}) - {description}")
        else:
            formatted.append(f"{name} ({tier})")
    return "; ".join(formatted)


def _account_description(account: dict[str, object]) -> str | None:
    description = str(account.get("description", "")).strip()
    return description or None


def select_account(
    requested_account: str | None,
    accounts: list[dict[str, object]],
) -> dict[str, object]:
    if requested_account:
        for account in accounts:
            if account.get("name") == requested_account:
                return account
        raise ValueError(
            "unknown or non-SMTP account: "
            f"{requested_account}. Available accounts: {_format_account_choices(accounts)}"
        )

    if len(accounts) == 1:
        return accounts[0]

    raise ValueError(
        "multiple SMTP-enabled accounts are available; choose one explicitly. "
        f"Available accounts: {_format_account_choices(accounts)}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit an interactive Mail Sentry send_email request."
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List SMTP-enabled Mail Sentry accounts and exit.",
    )
    parser.add_argument("--account", help="Mail Sentry account name.")
    parser.add_argument("--to", help="Comma-separated recipient list.")
    parser.add_argument("--subject", help="Email subject.")
    parser.add_argument("--text-body", help="Plain-text body.")
    parser.add_argument("--html-body", help="HTML body.")
    parser.add_argument(
        "--text-stdin",
        action="store_true",
        help="Read the plain-text body from stdin.",
    )
    parser.add_argument(
        "--html-stdin",
        action="store_true",
        help="Read the HTML body from stdin.",
    )
    parser.add_argument("--cc", help="Optional comma-separated CC recipient list.")
    parser.add_argument("--bcc", help="Optional comma-separated BCC recipient list.")
    parser.add_argument(
        "--confirm-sensitive-account",
        action="store_true",
        help="Required when sending from a sensitive account after explicit user confirmation.",
    )
    return parser


def run(
    args: argparse.Namespace,
    *,
    stdin_reader: Callable[[], str],
    stdin_is_tty: bool,
) -> dict[str, object]:
    config = config_from_env()
    accounts = list_smtp_accounts(config)

    if args.list_accounts:
        return {"accounts": accounts}

    if not args.to:
        raise ValueError("--to is required unless --list-accounts is used")
    if not args.subject:
        raise ValueError("--subject is required unless --list-accounts is used")

    stdin_text = stdin_reader() if not stdin_is_tty else None
    text_body, html_body = resolve_bodies(
        args,
        stdin_text=stdin_text,
        stdin_is_tty=stdin_is_tty,
    )

    selected_account = select_account(args.account, accounts)
    selected_account_name = str(selected_account["name"])
    sensitivity_tier = str(selected_account.get("sensitivity_tier", "standard"))
    description = _account_description(selected_account)
    if sensitivity_tier == "sensitive" and not args.confirm_sensitive_account:
        descriptor = f" ({description})" if description else ""
        raise ValueError(
            f"selected account {selected_account_name}{descriptor} is sensitive; "
            "require explicit confirmation and --confirm-sensitive-account before sending"
        )

    result = call_tool_sync(
        config,
        "send_email",
        build_arguments_with_bodies(
            args,
            account=selected_account_name,
            text_body=text_body,
            html_body=html_body,
        ),
    )
    result.setdefault("account", selected_account_name)
    if description is not None:
        result.setdefault("account_description", description)
    result.setdefault("account_sensitivity_tier", sensitivity_tier)
    return result


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        result = run(
            args,
            stdin_reader=sys.stdin.read,
            stdin_is_tty=sys.stdin.isatty(),
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
