from __future__ import annotations

import importlib.util
import imaplib
from email.message import EmailMessage
from pathlib import Path
import smtplib
import sys
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_mail_lab_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "mail_lab.py"
    spec = importlib.util.spec_from_file_location("mail_lab", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mail_lab"] = module
    spec.loader.exec_module(module)
    return module


def load_apply_mail_lab_config_tool() -> Any:
    path = REPO_ROOT / "media" / "tools" / "apply_mail_lab_config.py"
    spec = importlib.util.spec_from_file_location("apply_mail_lab_config", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["apply_mail_lab_config"] = module
    spec.loader.exec_module(module)
    return module


mail_lab = load_mail_lab_tool()
apply_mail_lab_config = load_apply_mail_lab_config_tool()


def flatten_imap_data(parts: list[object]) -> bytes:
    payload = bytearray()
    for part in parts:
        if isinstance(part, bytes):
            payload.extend(part)
        elif isinstance(part, tuple):
            for value in part:
                if isinstance(value, bytes):
                    payload.extend(value)
    return bytes(payload)


@pytest.fixture
def lab() -> Any:
    instance = mail_lab.MailLab(
        username="bot@example.test",
        password="secret",
        smtp_port=0,
        imap_port=0,
    )
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


def test_smtp_delivery_is_visible_through_shared_imap_mailbox(lab: Any) -> None:
    message = EmailMessage()
    message["From"] = "Operator <operator@example.test>"
    message["To"] = "bot@example.test"
    message["Subject"] = "Install check"
    message.set_content("The local SMTP server delivered this to IMAP.")

    with smtplib.SMTP(lab.host, lab.smtp_port, timeout=5) as smtp:
        smtp.login("bot@example.test", "secret")
        smtp.send_message(message)

    with imaplib.IMAP4(lab.host, lab.imap_port, timeout=5) as imap:
        imap.login("bot@example.test", "secret")
        status, _select_data = imap.select("INBOX")
        assert status == "OK"
        status, search_data = imap.uid("SEARCH", None, "ALL")
        assert status == "OK"
        assert search_data == [b"1"]
        status, fetch_data = imap.uid("FETCH", b"1", "(RFC822)")
        assert status == "OK"

    payload = flatten_imap_data(fetch_data)
    assert b"Subject: Install check" in payload
    assert b"The local SMTP server delivered this to IMAP." in payload


def test_imap_and_smtp_share_credentials(lab: Any) -> None:
    with pytest.raises(smtplib.SMTPAuthenticationError):
        with smtplib.SMTP(lab.host, lab.smtp_port, timeout=5) as smtp:
            smtp.login("bot@example.test", "wrong")

    with pytest.raises(imaplib.IMAP4.error, match="LOGIN failed"):
        with imaplib.IMAP4(lab.host, lab.imap_port, timeout=5) as imap:
            imap.login("bot@example.test", "wrong")


def test_mail_lab_writes_recording_env_file(tmp_path: Path) -> None:
    lab = mail_lab.MailLab(username="bot@example.test", password="secret")
    lab.smtp_port = 2525
    lab.imap_port = 2143
    env_file = tmp_path / "mail-lab.env"

    mail_lab.write_env_file(
        env_file,
        lab.env_values(container_host="host.docker.internal"),
    )

    content = env_file.read_text(encoding="utf-8")
    assert "MAIL_LAB_SMTP_HOST=host.docker.internal\n" in content
    assert "MAIL_LAB_SMTP_PORT=2525\n" in content
    assert "MAIL_LAB_IMAP_HOST=host.docker.internal\n" in content
    assert "MAIL_LAB_IMAP_PORT=2143\n" in content
    assert "SMTP_BOT_ACCOUNT_USERNAME=bot@example.test\n" in content
    assert "SMTP_BOT_ACCOUNT_PASSWORD=secret\n" in content
    assert "IMAP_BOT_ACCOUNT_USERNAME=bot@example.test\n" in content
    assert "IMAP_BOT_ACCOUNT_PASSWORD=secret\n" in content


def test_apply_mail_lab_config_updates_staged_account_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "conf"
    imap_account = config_dir / "arbiter" / "account" / "imap" / "bot.yaml"
    smtp_account = config_dir / "arbiter" / "account" / "smtp" / "bot.yaml"
    imap_account.parent.mkdir(parents=True)
    smtp_account.parent.mkdir(parents=True)
    imap_account.write_text(
        "policy: bot_policy\nhost: imap.example.com\nport: 993\n",
        encoding="utf-8",
    )
    smtp_account.write_text(
        "policy: bot_policy\nhost: smtp.example.com\nport: 587\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MAIL_LAB_IMAP_HOST", "host.docker.internal")
    monkeypatch.setenv("MAIL_LAB_IMAP_PORT", "2143")
    monkeypatch.setenv("MAIL_LAB_SMTP_HOST", "host.docker.internal")
    monkeypatch.setenv("MAIL_LAB_SMTP_PORT", "2525")
    monkeypatch.setenv("BOT_EMAIL", "bot@example.test")
    monkeypatch.setenv("IMAP_BOT_ACCOUNT_USERNAME", "bot@example.test")
    monkeypatch.setenv("IMAP_BOT_ACCOUNT_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_BOT_ACCOUNT_USERNAME", "bot@example.test")
    monkeypatch.setenv("SMTP_BOT_ACCOUNT_PASSWORD", "secret")

    apply_mail_lab_config.apply_mail_lab_config(config_dir, update_env=True)

    imap_text = imap_account.read_text(encoding="utf-8")
    smtp_text = smtp_account.read_text(encoding="utf-8")
    env_text = (config_dir / ".env").read_text(encoding="utf-8")
    assert "host: host.docker.internal\n" in imap_text
    assert "port: 2143\n" in imap_text
    assert "tls: none\n" in imap_text
    assert "kind:" not in imap_text
    assert "host: host.docker.internal\n" in smtp_text
    assert "port: 2525\n" in smtp_text
    assert "from_email: bot@example.test\n" in smtp_text
    assert "IMAP_BOT_ACCOUNT_PASSWORD=secret\n" in env_text
    assert "SMTP_BOT_ACCOUNT_PASSWORD=secret\n" in env_text
