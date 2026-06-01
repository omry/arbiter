from __future__ import annotations

import nox  # type: ignore[import-not-found]


nox.options.sessions = ["tests", "lint"]

PYPROJECT = nox.project.load_toml("pyproject.toml")

BLACK_TARGETS = [
    "core/src",
    "core/tests",
    "smtp/src",
    "smtp/tests",
    "imap/src",
    "imap/tests",
    "noxfile.py",
    "tools/extract_release_notes",
    "tools/plan_pypi_publish",
    "tools/upgrade_release_line",
]
SUPPORTED_PYTHONS = nox.project.python_versions(PYPROJECT)
STRICT_MYPY_TARGETS = ["core/src", "smtp/src", "imap/src"]
SUPPLEMENTAL_MYPY_TARGETS = [
    "core/tests",
    "smtp/tests",
    "imap/tests",
    "noxfile.py",
]


def install_project(session: nox.Session) -> None:
    session.install(
        "aiosmtpd>=1.4.6,<2.0",
        "black>=25.0,<26.0",
        "mypy>=1.11,<2.0",
        "pytest>=7.4,<9.0",
        "trustme>=1.2,<2.0",
    )
    session.install("-e", "core")
    session.install("-e", "smtp")
    session.install("-e", "imap")


@nox.session  # type: ignore[untyped-decorator]
def tests(session: nox.Session) -> None:
    install_project(session)
    session.run(
        "pytest", *(session.posargs or ["core/tests", "smtp/tests", "imap/tests"])
    )


@nox.session(  # type: ignore[untyped-decorator]
    python=SUPPORTED_PYTHONS,
    download_python="auto",
)
def compat(session: nox.Session) -> None:
    install_project(session)
    session.run(
        "pytest", *(session.posargs or ["core/tests", "smtp/tests", "imap/tests"])
    )


@nox.session(name="deploy-test")  # type: ignore[untyped-decorator]
def deploy_test(session: nox.Session) -> None:
    install_project(session)
    session.run(
        "pytest",
        "imap/tests/integration/test_deploy_docker_integration.py",
        env={"AGENT_ARBITER_RUN_DOCKER_DEPLOY_TESTS": "1"},
    )


@nox.session  # type: ignore[untyped-decorator]
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
