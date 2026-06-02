from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "upgrade_release_line"


FIXTURE_FILES = {
    "pyproject.toml": """[project]
name = "arbiter-suite"
version = "0.8.0"
dependencies = [
  "arbiter-core==0.8.0",
  "arbiter-smtp==0.8.0",
  "arbiter-imap==0.8.0",
]
""",
    "core/pyproject.toml": """[project]
name = "arbiter-core"
version = "0.8.0"
""",
    "smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.8.0"
dependencies = [
  "arbiter-core>=0.8.0,<0.9.0",
]
""",
    "imap/pyproject.toml": """[project]
name = "arbiter-imap"
version = "0.8.0"
dependencies = [
  "arbiter-core>=0.8.0,<0.9.0",
]
""",
    "smtp/src/agent_arbiter_smtp/__init__.py": 'CORE_API_VERSION = "0.8"\n',
    "imap/src/agent_arbiter_imap/__init__.py": 'CORE_API_VERSION = "0.8"\n',
    "docs/overview.md": "- Version: `0.8.0`\n",
    "website/docs/operate/deployment/packages.md": (
        "arbiter-suite==0.8.0\n"
        "arbiter-core==0.8.0\n"
        "arbiter-smtp==0.8.0\n"
        "/wheels/arbiter_core-0.8.0-py3-none-any.whl\n"
        "/wheels/arbiter_smtp-0.8.0-py3-none-any.whl\n"
        "0.8.0.dev1\n"
    ),
    "website/docs/operate/server-reference.md": ("arbiter-core==0.8.0\n"),
    "website/docs/extend/plugins.md": (
        "`0.8.x` should use a plugin version on the `0.8` line, "
        "such as `0.8.0` or `0.8.1`.\n"
        '    version = "0.8.0"\n'
        '    core_api_version = "0.8"\n'
        '  "arbiter-core>=0.8.0,<0.9.0",\n'
    ),
}


def _write_fixture(root: Path) -> None:
    for relative_path, content in FIXTURE_FILES.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_dev_fixture(root: Path) -> None:
    files = {
        "pyproject.toml": """[project]
name = "arbiter-suite"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-core==0.9.0.dev1",
  "arbiter-smtp==0.9.0.dev1",
  "arbiter-imap==0.9.0.dev1",
]
""",
        "core/pyproject.toml": """[project]
name = "arbiter-core"
version = "0.9.0.dev1"
""",
        "smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-core>=0.9.0.dev1,<0.10.0",
]
""",
        "imap/pyproject.toml": """[project]
name = "arbiter-imap"
version = "0.9.0.dev1"
dependencies = [
  "arbiter-core>=0.9.0.dev1,<0.10.0",
]
""",
        "smtp/src/agent_arbiter_smtp/__init__.py": 'CORE_API_VERSION = "0.9"\n',
        "imap/src/agent_arbiter_imap/__init__.py": 'CORE_API_VERSION = "0.9"\n',
        "docs/overview.md": "- Version: `0.9.0.dev1`\n",
        "website/docs/operate/deployment/packages.md": (
            "arbiter-suite==0.9.0\n"
            "arbiter-core==0.9.0.dev1\n"
            "arbiter-smtp==0.9.0.dev1\n"
            "/wheels/arbiter_core-0.9.0.dev1-py3-none-any.whl\n"
            "/wheels/arbiter_smtp-0.9.0.dev1-py3-none-any.whl\n"
            "dev version such as `0.9.0.dev1`\n"
        ),
        "website/docs/operate/server-reference.md": ("arbiter-core==0.9.0.dev1\n"),
        "website/docs/extend/plugins.md": (
            "`0.9.x` should use a plugin version on the `0.9` line, "
            "such as `0.9.0.dev1`, `0.9.0`, or `0.9.1`.\n"
            '    version = "0.9.0.dev1"\n'
            '    core_api_version = "0.9"\n'
            '  "arbiter-core>=0.9.0.dev1,<0.10.0",\n'
        ),
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_independent_plugin_patch_fixture(root: Path) -> None:
    files = {
        **FIXTURE_FILES,
        "pyproject.toml": """[project]
name = "arbiter-suite"
version = "0.8.0"
dependencies = [
  "arbiter-core==0.8.0",
  "arbiter-smtp==0.8.1",
  "arbiter-imap==0.8.0",
]
""",
        "smtp/pyproject.toml": """[project]
name = "arbiter-smtp"
version = "0.8.1"
dependencies = [
  "arbiter-core>=0.8.0,<0.9.0",
]
""",
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _run_tool(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def test_upgrade_release_line_dry_run_prints_patch_without_writing(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    before = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    result = _run_tool(tmp_path, "--dry-run", "0.9")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == before
    assert '+  "arbiter-core==0.9.0"' in result.stdout
    assert '+  "arbiter-core>=0.9.0,<0.10.0"' in result.stdout
    assert "\x1b[32m✓\x1b[0m would update 10 file(s)" in result.stdout


def test_upgrade_release_line_updates_packages_runtime_and_docs(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert 'version = "0.9.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"arbiter-imap==0.9.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"arbiter-core>=0.9.0,<0.10.0"' in (
        tmp_path / "smtp/pyproject.toml"
    ).read_text(encoding="utf-8")
    assert 'CORE_API_VERSION = "0.9"' in (
        tmp_path / "imap/src/agent_arbiter_imap/__init__.py"
    ).read_text(encoding="utf-8")
    plugin_docs = (tmp_path / "website/docs/extend/plugins.md").read_text(
        encoding="utf-8"
    )
    assert "`0.9.x`" in plugin_docs
    assert "`0.9` line" in plugin_docs
    assert "`0.9.1`" in plugin_docs
    assert '"arbiter-core>=0.9.0,<0.10.0"' in plugin_docs


def test_upgrade_release_line_accepts_independently_patched_plugin(
    tmp_path: Path,
) -> None:
    _write_independent_plugin_patch_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert '"arbiter-smtp==0.9.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "0.9.0"' in (tmp_path / "smtp/pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_upgrade_release_line_promotes_dev_version_to_final_same_line(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.9")

    assert result.returncode == 0, result.stderr
    assert 'version = "0.9.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"arbiter-imap==0.9.0"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"arbiter-core>=0.9.0,<0.10.0"' in (
        tmp_path / "smtp/pyproject.toml"
    ).read_text(encoding="utf-8")
    deployment_docs = (
        tmp_path / "website/docs/operate/deployment/packages.md"
    ).read_text(encoding="utf-8")
    plugin_docs = (tmp_path / "website/docs/extend/plugins.md").read_text(
        encoding="utf-8"
    )
    assert "arbiter-suite==0.9.0\n" in deployment_docs
    assert "arbiter-suite==0.9.0.dev1" not in deployment_docs
    assert "dev version such as `0.9.0.dev1`" in deployment_docs
    assert "such as `0.9.0` or\n`0.9.1`" in plugin_docs
    assert "such as `0.9.0`,\n`0.9.0`" not in plugin_docs


def test_upgrade_release_line_updates_dev_docs_to_next_line_without_stale_suffix(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "--dry-run", "1.0")

    assert result.returncode == 0, result.stderr
    assert "+arbiter-suite==1.0.0" in result.stdout
    assert '+  "arbiter-core>=1.0.0,<1.1.0"' in result.stdout
    assert "+/wheels/arbiter_core-1.0.0-py3-none-any.whl" in result.stdout
    assert "1.0.0.dev1,<" not in result.stdout
    assert "such as `1.0.0` or\n+`1.0.1`" in result.stdout
    assert "such as `1.0.0`,\n+`1.0.0`" not in result.stdout


def test_upgrade_release_line_rejects_same_or_older_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "0.8")

    assert result.returncode == 1
    assert "\x1b[31m✗\x1b[0m upgrade_release_line:" in result.stderr
    assert "target release line 0.8 must be greater than current line 0.8" in (
        result.stderr
    )


def test_upgrade_release_line_check_accepts_matching_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.8")

    assert result.returncode == 0, result.stderr
    assert "\x1b[32m✓\x1b[0m release line check passed: 0.8 (0.8.0)" in (result.stdout)


def test_upgrade_release_line_check_accepts_matching_dev_release_line(
    tmp_path: Path,
) -> None:
    _write_dev_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.9")

    assert result.returncode == 0, result.stderr
    assert "\x1b[32m✓\x1b[0m release line check passed: 0.9 (0.9.0.dev1)" in (
        result.stdout
    )


def test_upgrade_release_line_check_rejects_mismatched_release_line(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    result = _run_tool(tmp_path, "--check", "0.9")

    assert result.returncode == 1
    assert "\x1b[31m✗\x1b[0m upgrade_release_line:" in result.stderr
    assert "target release line 0.9 does not match current line 0.8" in result.stderr
