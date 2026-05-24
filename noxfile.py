from __future__ import annotations

from pathlib import Path

import nox


nox.options.sessions = ["tests", "lint"]

BLACK_TARGETS = [
    "src",
    "tests",
    "openclaw_skills/_shared/scripts",
    "openclaw_skills/send-email-interactive/scripts",
    "openclaw_skills/send-email-predefined/scripts",
    "noxfile.py",
]
STRICT_MYPY_TARGETS = ["src"]
SUPPLEMENTAL_MYPY_TARGETS = [
    "tests",
    "noxfile.py",
]
SHARED_SKILL_MYPY_TARGETS = ["openclaw_skills/_shared/scripts/mail_sentry_client.py"]
SKILL_SCRIPT_MYPY_TARGETS = [
    "openclaw_skills/send-email-interactive/scripts/send_email_interactive.py",
    "openclaw_skills/send-email-predefined/scripts/send_email_predefined.py",
]
SHARED_SKILL_SCRIPTS_DIR = str(Path("openclaw_skills/_shared/scripts").resolve())


def install_project(session: nox.Session) -> None:
    session.install("-e", ".[dev]")


@nox.session
def tests(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or ["tests"]))


@nox.session
def lint(session: nox.Session) -> None:
    install_project(session)
    session.run("black", "--check", *BLACK_TARGETS)
    session.run("mypy", *STRICT_MYPY_TARGETS)
    session.run(
        "mypy",
        "--allow-untyped-defs",
        "--allow-incomplete-defs",
        "--check-untyped-defs",
        *SUPPLEMENTAL_MYPY_TARGETS,
    )
    session.run(
        "mypy",
        "--allow-untyped-defs",
        "--allow-incomplete-defs",
        "--check-untyped-defs",
        *SHARED_SKILL_MYPY_TARGETS,
    )
    session.run(
        "mypy",
        "--allow-untyped-defs",
        "--allow-incomplete-defs",
        "--check-untyped-defs",
        *SKILL_SCRIPT_MYPY_TARGETS,
        env={"MYPYPATH": SHARED_SKILL_SCRIPTS_DIR},
    )
