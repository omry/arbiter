from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def private_windows_test_parent(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    from arbiter_server.file_protection import ensure_runtime_config_permissions
    from arbiter_server.file_protection.windows import _windows_file_security

    security = _windows_file_security(tmp_path)
    owner_sid = security.owner_sid
    assert owner_sid is not None
    allowed = {owner_sid, "S-1-5-18", "S-1-5-32-544"}
    # Use real, inheritable ACLs. Python/pytest's special 0o700 Owner Rights ACE
    # is not an operator-prepared private parent under Arbiter's allowlist.
    subprocess.run(
        [
            "icacls",
            str(tmp_path),
            "/inheritance:r",
            "/grant:r",
            f"*{owner_sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for sid in sorted({ace.sid for ace in security.access_aces} - allowed):
        subprocess.run(
            ["icacls", str(tmp_path), "/remove:g", f"*{sid}"],
            check=True,
            capture_output=True,
            text=True,
        )
    ensure_runtime_config_permissions(config_dir=tmp_path, env_file=None)
