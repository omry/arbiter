from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "upgrade_release_line"
SUITE_PYPROJECT = Path("meta/arbiter-suite/pyproject.toml")
SUITE_PYPROJECT_TEXT = str(SUITE_PYPROJECT)
SKILL_PYPROJECT = Path("skill/pyproject.toml")
SKILL_PYPROJECT_TEXT = str(SKILL_PYPROJECT)
PYTHON_CLIENT_PYPROJECT = Path("client/python-cli/pyproject.toml")


FIXTURE_FILES = {
    SUITE_PYPROJECT_TEXT: """[project]
name = "arbiter-suite"
version = "0.8.0"
dependencies = [
  "arbiter-server==0.8.0",
  "arbiter-client==0.8.0",
  "arbiter-smtp==0.8.0",
  "arbiter-imap==0.8.0",
]
""",
    "server/pyproject.toml": """[project]
name = "arbiter-server"
version = "0.8.0"
""",
    SKILL_PYPROJECT_TEXT: """[project]
name = "arbiter-skill"
version = "0.8.0"
""",
    str(
        PYTHON_CLIENT_PYPROJECT
    ): """[project]
name = "arbiter-python-client"
version = "0.8.0"
""",
    "plugins/smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.8.0"
dependencies = [
  "arbiter-server>=0.8.0,<0.9.0",
]
""",
    "plugins/imap/pyproject.toml": """[project]
name = "arbiter-imap"
version = "0.8.0"
dependencies = [
  "arbiter-server>=0.8.0,<0.9.0",
]
""",
    "plugins/smtp/src/arbiter_smtp/__init__.py": 'SERVER_API_VERSION = "0.8"\n',
    "plugins/imap/src/arbiter_imap/__init__.py": 'SERVER_API_VERSION = "0.8"\n',
    "website/docs/operate/deployment/3-bundle-deep-dive.md": (
        "arbiter-suite==0.8.0\n"
        "arbiter-server==0.8.0\n"
        "arbiter-smtp==0.8.0\n"
        "/wheels/arbiter_server-0.8.0-py3-none-any.whl\n"
        "/wheels/arbiter_smtp-0.8.0-py3-none-any.whl\n"
        "0.8.0.dev1\n"
    ),
    "website/docs/operate/server-reference.md": ("arbiter-server==0.8.0\n"),
    "website/docs/extend/plugins.md": (
        "`0.8.x` should use a plugin version on the `0.8` line, "
        "such as `0.8.0` or `0.8.1`.\n"
        '    version = "0.8.0"\n'
        '    server_api_version = "0.8"\n'
        '  "arbiter-server>=0.8.0,<0.9.0",\n'
    ),
}


def _write_fixture(root: Path) -> None:
    for relative_path, content in FIXTURE_FILES.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_dev_fixture(root: Path) -> None:
    files = {
        SUITE_PYPROJECT_TEXT: """[project]
name = "arbiter-suite"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-server==0.9.0.dev1",
  "arbiter-client==0.9.0.dev1",
  "arbiter-smtp==0.9.0.dev1",
  "arbiter-imap==0.9.0.dev1",
]
""",
        "server/pyproject.toml": """[project]
name = "arbiter-server"
version = "0.9.0.dev1"
""",
        SKILL_PYPROJECT_TEXT: """[project]
name = "arbiter-skill"
version = "0.9.0.dev1"
""",
        str(
            PYTHON_CLIENT_PYPROJECT
        ): """[project]
name = "arbiter-python-client"
version = "0.9.0.dev1"
""",
        "plugins/smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-server>=0.9.0.dev1,<0.10.0",
]
""",
        "plugins/imap/pyproject.toml": """[project]
name = "arbiter-imap"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-server>=0.9.0.dev1,<0.10.0",
]
""",
        "plugins/smtp/src/arbiter_smtp/__init__.py": 'SERVER_API_VERSION = "0.9"\n',
        "plugins/imap/src/arbiter_imap/__init__.py": 'SERVER_API_VERSION = "0.9"\n',
        "website/docs/operate/deployment/3-bundle-deep-dive.md": (
            "arbiter-suite==0.9.0\n"
            "arbiter-server==0.9.0.dev1\n"
            "arbiter-smtp==0.9.0.dev1\n"
            "/wheels/arbiter_server-0.9.0.dev1-py3-none-any.whl\n"
            "/wheels/arbiter_smtp-0.9.0.dev1-py3-none-any.whl\n"
            "dev version such as `0.9.0.dev1`\n"
        ),
        "website/docs/operate/server-reference.md": ("arbiter-server==0.9.0.dev1\n"),
        "website/docs/extend/plugins.md": (
            "`0.9.x` should use a plugin version on the `0.9` line, "
            "such as `0.9.0` or `0.9.1`.\n"
            "dev version such as `0.9.0.dev1`\n"
            '    version = "0.9.0"\n'
            '    server_api_version = "0.9"\n'
            '  "arbiter-server>=0.9.0,<0.10.0",\n'
        ),
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_independent_plugin_patch_fixture(root: Path) -> None:
    files = {
        **FIXTURE_FILES,
        SUITE_PYPROJECT_TEXT: """[project]
name = "arbiter-suite"
version = "0.8.0"
dependencies = [
  "arbiter-server==0.8.0",
  "arbiter-client==0.8.0",
  "arbiter-smtp==0.8.1",
  "arbiter-imap==0.8.0",
]
""",
        "plugins/smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.8.1"
dependencies = [
  "arbiter-server>=0.8.0,<0.9.0",
]
""",
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _run_tool(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        check=False,
        env=process_env,
        text=True,
        capture_output=True,
    )


def _assert_success_status(output: str, message: str) -> None:
    assert any(
        f"{marker} {message}" in output
        for marker in ("\x1b[32m✓\x1b[0m", "\x1b[32mOK\x1b[0m")
    )


def _assert_error_status(output: str, message: str) -> None:
    assert any(
        f"{marker} {message}" in output
        for marker in ("\x1b[31m✗\x1b[0m", "\x1b[31mERROR\x1b[0m")
    )


def _normalize_path_separators(output: str) -> str:
    return output.replace("\\", "/")


def test_upgrade_release_line_dry_run_prints_patch_without_writing(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    before = (tmp_path / SUITE_PYPROJECT).read_text(encoding="utf-8")

    result = _run_tool(tmp_path, "--dry-run", "0.9")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / SUITE_PYPROJECT).read_text(encoding="utf-8") == before
    assert '+  "arbiter-server==0.9.0"' in result.stdout
    assert '+  "arbiter-server>=0.9.0,<0.10.0"' in result.stdout
    assert str(PYTHON_CLIENT_PYPROJECT) in result.stdout
    _assert_success_status(result.stdout, "would update 11 file(s)")


def test_upgrade_release_line_updates_packages_runtime_and_docs(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert 'version = "0.9.0"' in (tmp_path / SUITE_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert 'version = "0.9.0"' in (tmp_path / SKILL_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert 'version = "0.9.0"' in (tmp_path / PYTHON_CLIENT_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert '"arbiter-imap==0.9.0"' in (tmp_path / SUITE_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert '"arbiter-server>=0.9.0,<0.10.0"' in (
        tmp_path / "plugins/smtp/pyproject.toml"
    ).read_text(encoding="utf-8")
    assert 'SERVER_API_VERSION = "0.9"' in (
        tmp_path / "plugins/imap/src/arbiter_imap/__init__.py"
    ).read_text(encoding="utf-8")
    plugin_docs = (tmp_path / "website/docs/extend/plugins.md").read_text(
        encoding="utf-8"
    )
    assert "`0.9.x`" in plugin_docs
    assert "`0.9` line" in plugin_docs
    assert "`0.9.1`" in plugin_docs
    assert '"arbiter-server>=0.9.0,<0.10.0"' in plugin_docs


def test_upgrade_release_line_accepts_independently_patched_plugin(
    tmp_path: Path,
) -> None:
    _write_independent_plugin_patch_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert '"arbiter-smtp==0.9.0"' in (tmp_path / SUITE_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert 'version = "0.9.0"' in (tmp_path / "plugins/smtp/pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_upgrade_release_line_discovers_new_plugin_package(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    suite_path = tmp_path / SUITE_PYPROJECT
    suite_path.write_text(
        suite_path.read_text(encoding="utf-8").replace(
            '  "arbiter-imap==0.8.0",\n',
            '  "arbiter-imap==0.8.0",\n  "arbiter-pop==0.8.0",\n',
        ),
        encoding="utf-8",
    )
    (tmp_path / "plugins/pop/src/arbiter_pop").mkdir(parents=True)
    (tmp_path / "plugins/pop/pyproject.toml").write_text(
        """[project]
name = "arbiter-pop"
version = "0.8.0"
dependencies = [
  "arbiter-server>=0.8.0,<0.9.0",
]
""",
        encoding="utf-8",
    )
    (tmp_path / "plugins/pop/src/arbiter_pop/__init__.py").write_text(
        'SERVER_API_VERSION = "0.8"\n',
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert '"arbiter-pop==0.9.0"' in suite_path.read_text(encoding="utf-8")
    assert '"arbiter-server>=0.9.0,<0.10.0"' in (
        tmp_path / "plugins/pop/pyproject.toml"
    ).read_text(encoding="utf-8")
    assert 'SERVER_API_VERSION = "0.9"' in (
        tmp_path / "plugins/pop/src/arbiter_pop/__init__.py"
    ).read_text(encoding="utf-8")


def test_upgrade_release_line_promotes_dev_version_to_final_same_line(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert 'version = "0.9.0"' in (tmp_path / SUITE_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert 'version = "0.9.0"' in (tmp_path / PYTHON_CLIENT_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert '"arbiter-imap==0.9.0"' in (tmp_path / SUITE_PYPROJECT).read_text(
        encoding="utf-8",
    )
    assert '"arbiter-server>=0.9.0,<0.10.0"' in (
        tmp_path / "plugins/smtp/pyproject.toml"
    ).read_text(encoding="utf-8")
    deployment_docs = (
        tmp_path / "website/docs/operate/deployment/3-bundle-deep-dive.md"
    ).read_text(encoding="utf-8")
    plugin_docs = (tmp_path / "website/docs/extend/plugins.md").read_text(
        encoding="utf-8"
    )
    assert "arbiter-suite==0.9.0\n" in deployment_docs
    assert "arbiter-suite==0.9.0.dev1" not in deployment_docs
    assert "dev version such as `0.9.0.dev1`" in deployment_docs
    assert "such as `0.9.0` or `0.9.1`" in plugin_docs
    assert "such as `0.9.0`,\n`0.9.0`" not in plugin_docs


def test_upgrade_release_line_updates_dev_docs_to_next_line_without_stale_suffix(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "--dry-run", "1.0")

    assert result.returncode == 0, result.stderr
    assert "+arbiter-suite==1.0.0" in result.stdout
    assert '+  "arbiter-server>=1.0.0,<1.1.0"' in result.stdout
    assert "+/wheels/arbiter_server-1.0.0-py3-none-any.whl" in result.stdout
    assert "1.0.0.dev1,<" not in result.stdout
    assert "such as `1.0.0` or `1.0.1`" in result.stdout
    assert "such as `1.0.0`,\n+`1.0.0`" not in result.stdout


def test_upgrade_release_line_rejects_same_or_older_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.8")

    assert result.returncode == 1
    _assert_error_status(result.stderr, "upgrade_release_line:")
    assert "target release line 0.8 must be greater than current line 0.8" in (
        result.stderr
    )


def test_upgrade_release_line_error_uses_ascii_status_for_limited_stream_encoding(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.8", env={"PYTHONIOENCODING": "cp1252"})

    assert result.returncode == 1
    assert "\x1b[31mERROR\x1b[0m upgrade_release_line:" in result.stderr
    assert "target release line 0.8 must be greater than current line 0.8" in (
        result.stderr
    )


def test_upgrade_release_line_check_accepts_matching_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.8")

    assert result.returncode == 0, result.stderr
    _assert_success_status(result.stdout, "release line check passed: 0.8 (0.8.0)")


def test_upgrade_release_line_check_requires_docs_for_final_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    docs = tmp_path / "website/docs/operate/deployment/3-bundle-deep-dive.md"
    docs.write_text("stale docs\n", encoding="utf-8")

    result = _run_tool(tmp_path, "--check", "0.8")

    assert result.returncode == 1
    assert (
        "website/docs/operate/deployment/3-bundle-deep-dive.md "
        "is missing release text: arbiter-suite==0.8.0"
    ) in _normalize_path_separators(result.stderr)


def test_upgrade_release_line_check_rejects_stale_python_client_version(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    python_client_path = tmp_path / PYTHON_CLIENT_PYPROJECT
    python_client_path.write_text(
        python_client_path.read_text(encoding="utf-8").replace(
            'version = "0.8.0"',
            'version = "0.9.0"',
        ),
        encoding="utf-8",
    )

    result = _run_tool(tmp_path, "--check", "0.8")

    assert result.returncode == 1
    assert (
        "client/python-cli/pyproject.toml version line 0.9 "
        "does not match root line 0.8"
    ) in _normalize_path_separators(result.stderr)


def test_upgrade_release_line_check_uses_ascii_status_for_limited_stream_encoding(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.8", env={"PYTHONIOENCODING": "cp1252"})

    assert result.returncode == 0, result.stderr
    assert "\x1b[32mOK\x1b[0m release line check passed: 0.8 (0.8.0)" in (result.stdout)


def test_upgrade_release_line_check_accepts_matching_dev_release_line(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.9")

    assert result.returncode == 0, result.stderr
    _assert_success_status(
        result.stdout,
        "release line check passed: 0.9 (0.9.0.dev1)",
    )


def test_upgrade_release_line_check_skips_docs_for_dev_release_line(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)
    for path in (
        "website/docs/operate/deployment/3-bundle-deep-dive.md",
        "website/docs/operate/server-reference.md",
        "website/docs/extend/plugins.md",
    ):
        (tmp_path / path).write_text("stale docs\n", encoding="utf-8")

    result = _run_tool(tmp_path, "--check", "0.9")

    assert result.returncode == 0, result.stderr
    _assert_success_status(
        result.stdout,
        "release line check passed: 0.9 (0.9.0.dev1)",
    )


def test_upgrade_release_line_check_rejects_mismatched_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.9")

    assert result.returncode == 1
    _assert_error_status(result.stderr, "upgrade_release_line:")
    assert "target release line 0.9 does not match current line 0.8" in result.stderr
