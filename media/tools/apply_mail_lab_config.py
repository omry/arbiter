#!/usr/bin/env python3
"""Apply local mail-lab settings to staged Arbiter recording config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a mapping: {path}")
    return data


def write_yaml(path: Path, data: dict[str, Any], *, package: str | None = None) -> None:
    content = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    if package is not None:
        content = f"# @package {package}\n" + content
    path.write_text(content, encoding="utf-8")


def update_account_files(config_dir: Path) -> None:
    imap_account = config_dir / "arbiter" / "account" / "imap" / "bot.yaml"
    smtp_account = config_dir / "arbiter" / "account" / "smtp" / "bot.yaml"

    imap = load_yaml(imap_account)
    imap.update(
        {
            "host": require_env("MAIL_LAB_IMAP_HOST"),
            "port": int(require_env("MAIL_LAB_IMAP_PORT")),
            "username": "${oc.env:IMAP_BOT_ACCOUNT_USERNAME}",
            "password": "${oc.env:IMAP_BOT_ACCOUNT_PASSWORD}",
            "tls": "none",
            "verify_peer": False,
            "default_folder": "INBOX",
            "folders": {
                "INBOX": {
                    "description": "Local recording inbox.",
                },
                "Sent": {
                    "description": "Local recording sent mail.",
                },
            },
        }
    )
    write_yaml(imap_account, imap, package="arbiter.account.imap.bot")

    smtp = load_yaml(smtp_account)
    smtp.update(
        {
            "host": require_env("MAIL_LAB_SMTP_HOST"),
            "port": int(require_env("MAIL_LAB_SMTP_PORT")),
            "authenticate": True,
            "username": "${oc.env:SMTP_BOT_ACCOUNT_USERNAME}",
            "password": "${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}",
            "from_email": os.environ.get("BOT_FROM_EMAIL")
            or require_env("BOT_EMAIL"),
            "from_name": "Arbiter",
            "tls": "none",
            "verify_peer": False,
        }
    )
    write_yaml(smtp_account, smtp, package="arbiter.account.smtp.bot")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered_keys = [
        "IMAP_BOT_ACCOUNT_USERNAME",
        "IMAP_BOT_ACCOUNT_PASSWORD",
        "SMTP_BOT_ACCOUNT_USERNAME",
        "SMTP_BOT_ACCOUNT_PASSWORD",
    ]
    lines = ["# arbiter-imap"]
    for key in ordered_keys[:2]:
        lines.append(f"{key}={values[key]}")
    lines.extend(["", "# arbiter-smtp"])
    for key in ordered_keys[2:]:
        lines.append(f"{key}={values[key]}")
    extra_keys = sorted(key for key in values if key not in ordered_keys)
    if extra_keys:
        lines.extend(["", "# misc"])
        for key in extra_keys:
            lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_file(config_dir: Path) -> None:
    env_file = config_dir / ".env"
    values = read_env_file(env_file)
    values.update(
        {
            "IMAP_BOT_ACCOUNT_USERNAME": require_env("IMAP_BOT_ACCOUNT_USERNAME"),
            "IMAP_BOT_ACCOUNT_PASSWORD": require_env("IMAP_BOT_ACCOUNT_PASSWORD"),
            "SMTP_BOT_ACCOUNT_USERNAME": require_env("SMTP_BOT_ACCOUNT_USERNAME"),
            "SMTP_BOT_ACCOUNT_PASSWORD": require_env("SMTP_BOT_ACCOUNT_PASSWORD"),
        }
    )
    write_env_file(env_file, values)


def apply_mail_lab_config(config_dir: Path, *, update_env: bool) -> None:
    update_account_files(config_dir)
    if update_env:
        update_env_file(config_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Also write bot credentials into the staged config .env file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        apply_mail_lab_config(args.config_dir, update_env=args.update_env)
    except (ConfigError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
