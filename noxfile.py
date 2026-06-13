from __future__ import annotations

from pathlib import Path

import nox


nox.options.sessions = ["unit", "lint"]

SERVER_PYPROJECT = nox.project.load_toml("server/pyproject.toml")

BLACK_TARGETS = [
    "client/hatch_build.py",
    "client/python-cli/src",
    "client/python-cli/tests",
    "server/src",
    "server/tests",
    "plugins/smtp/src",
    "plugins/smtp/tests",
    "plugins/imap/src",
    "plugins/imap/tests",
    "examples/plugins/echo/src",
    "examples/plugins/echo/tests",
    "noxfile.py",
    "skill/arbiter_skill_build.py",
    "tools/bump_release_version",
    "tools/build_release_dists",
    "tools/extract_release_notes",
    "tools/plan_pypi_publish",
    "tools/release_packages.py",
    "tools/sl_precommit_lint",
    "tools/smoke_release_install",
    "tools/upgrade_release_line",
]
TEST_TARGETS = [
    "client/python-cli/tests",
    "server/tests",
    "plugins/smtp/tests",
    "plugins/imap/tests",
    "examples/plugins/echo/tests",
]
UNIT_TEST_TARGETS = [
    "client/python-cli/tests/unit",
    "server/tests/unit",
    "plugins/smtp/tests/unit",
    "plugins/imap/tests/unit",
    "examples/plugins/echo/tests",
]
SERVER_INTEGRATION_TEST_TARGETS = ["server/tests/integration"]
PLUGIN_INTEGRATION_TEST_TARGETS = [
    str(path)
    for path in sorted(Path("plugins").glob("*/tests/integration"))
    if path.is_dir()
]
INTEGRATION_TEST_TARGETS = [
    *SERVER_INTEGRATION_TEST_TARGETS,
    *PLUGIN_INTEGRATION_TEST_TARGETS,
]
SUPPORTED_PYTHONS = nox.project.python_versions(SERVER_PYPROJECT)
PYREFLY_TARGETS = [
    "client/hatch_build.py",
    "client/python-cli/src",
    "client/python-cli/tests",
    "server/src",
    "server/tests",
    "plugins/smtp/src",
    "plugins/smtp/tests",
    "plugins/imap/src",
    "plugins/imap/tests",
    "examples/plugins/echo/src",
    "examples/plugins/echo/tests",
    "noxfile.py",
    "skill/arbiter_skill_build.py",
    "tools/bump_release_version",
    "tools/build_release_dists",
    "tools/release_packages.py",
    "tools/smoke_release_install",
]


def install_project(session: nox.Session) -> None:
    session.install(
        "aiosmtpd>=1.4.6,<2.0",
        "black>=25.0,<26.0",
        "build>=1.2,<2.0",
        "editables>=0.5,<1.0",
        "hatchling>=1.24,<2.0",
        "nox>=2024.10,<2026.0",
        "pyrefly>=0.39,<0.40",
        "pytest>=7.4,<9.0",
        "tomli>=2.0,<3.0",
    )
    session.install("-e", "client/python-cli")
    session.install("-e", "server")
    session.install("-e", "plugins/smtp")
    session.install("-e", "plugins/imap")


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
def unit(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or UNIT_TEST_TARGETS))


@nox.session
def integration(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or INTEGRATION_TEST_TARGETS))


@nox.session(name="server-integration")
def server_integration(session: nox.Session) -> None:
    install_project(session)
    session.run("pytest", *(session.posargs or SERVER_INTEGRATION_TEST_TARGETS))


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
    session.run("pytest", *(session.posargs or UNIT_TEST_TARGETS))


@nox.session(name="deploy-test")
def deploy_test(session: nox.Session) -> None:
    install_project(session)
    session.install("-e", "client")
    session.run(
        "pytest",
        "plugins/imap/tests/integration/test_deploy_docker_integration.py",
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
