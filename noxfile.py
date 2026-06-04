from __future__ import annotations

from pathlib import Path

import nox


nox.options.sessions = ["tests", "lint"]

CORE_PYPROJECT = nox.project.load_toml("core/pyproject.toml")

BLACK_TARGETS = [
    "core/src",
    "core/tests",
    "smtp/src",
    "smtp/tests",
    "imap/src",
    "imap/tests",
    "examples/plugins/echo/src",
    "examples/plugins/echo/tests",
    "noxfile.py",
    "tools/build_release_dists",
    "tools/extract_release_notes",
    "tools/plan_pypi_publish",
    "tools/upgrade_release_line",
]
TEST_TARGETS = [
    "core/tests",
    "smtp/tests",
    "imap/tests",
    "examples/plugins/echo/tests",
]
SUPPORTED_PYTHONS = nox.project.python_versions(CORE_PYPROJECT)
PYREFLY_TARGETS = [
    "core/src",
    "core/tests",
    "smtp/src",
    "smtp/tests",
    "imap/src",
    "imap/tests",
    "examples/plugins/echo/src",
    "examples/plugins/echo/tests",
    "noxfile.py",
]


def install_project(session: nox.Session) -> None:
    session.install(
        "aiosmtpd>=1.4.6,<2.0",
        "black>=25.0,<26.0",
        "nox>=2024.10,<2026.0",
        "pyrefly>=0.39,<0.40",
        "pytest>=7.4,<9.0",
        "tomli>=2.0,<3.0",
        "trustme>=1.2,<2.0",
    )
    session.install("-e", "core")
    session.install("-e", "smtp")
    session.install("-e", "imap")


def iter_black_targets() -> list[str]:
    paths: list[str] = []
    for target in BLACK_TARGETS:
        path = Path(target)
        if path.is_dir():
            paths.extend(str(file_path) for file_path in sorted(path.rglob("*.py")))
        else:
            paths.append(target)
    return paths


@nox.session
def tests(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or TEST_TARGETS))


@nox.session(
    python=SUPPORTED_PYTHONS,
    download_python="auto",
)
def compat(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or TEST_TARGETS))


@nox.session(name="deploy-test")
def deploy_test(session: nox.Session) -> None:
    install_project(session)
    session.run(
        "pytest",
        "imap/tests/integration/test_deploy_docker_integration.py",
        env={"ARBITER_RUN_DOCKER_DEPLOY_TESTS": "1"},
    )


@nox.session
def lint(session: nox.Session) -> None:
    install_project(session)
    for black_target in iter_black_targets():
        session.run(
            "black",
            "--check",
            "--target-version",
            "py310",
            "--workers",
            "1",
            black_target,
        )
    session.run("pyrefly", "check", "--config", "pyrefly.toml", *PYREFLY_TARGETS)
