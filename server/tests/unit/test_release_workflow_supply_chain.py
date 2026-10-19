from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_external_workflow_actions_have_immutable_pins_and_version_comments() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in path.read_text().splitlines():
            if "uses:" not in line:
                continue
            reference = line.split("uses:", 1)[1].strip()
            if reference.startswith("./"):
                continue
            if "/.github/workflows/" in reference:
                assert re.fullmatch(
                    r"[\w-]+/[\w.-]+/\.github/workflows/[\w.-]+\.ya?ml@[0-9a-f]{40}",
                    reference,
                ), (path, line)
            else:
                assert re.fullmatch(
                    r"[\w-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+", reference
                ), (path, line)


def test_release_jobs_install_hashed_binary_toolchain_and_disable_build_isolation() -> (
    None
):
    for name in ("prepare-release.yml", "publish.yml"):
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
        for job in workflow["jobs"].values():
            steps = job.get("steps", [])
            installed_lock = False
            for step in steps:
                command = step.get("run", "")
                if "python -m pip install" in command:
                    assert "--require-hashes" in command
                    assert "--only-binary=:all:" in command
                    assert "-r .github/requirements-release.txt" in command
                    installed_lock = True
                if "tools/build_release_dists" in command:
                    assert installed_lock
                    assert "--no-isolation" in command


def test_actions_dependency_updates_are_configured() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    assert any(
        update["package-ecosystem"] == "github-actions"
        and update["directory"] == "/"
        and update["schedule"]["interval"] == "monthly"
        for update in config["updates"]
    )
