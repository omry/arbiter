from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "plan_pypi_publish"


def _load_tool() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("plan_pypi_publish", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load plan_pypi_publish module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _write_project(root: Path, relative_path: str, name: str, version: str) -> None:
    pyproject = root / relative_path / "pyproject.toml"
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    pyproject.write_text(
        f"""[project]
name = "{name}"
version = "{version}"
""",
        encoding="utf-8",
    )


def _write_fixture(root: Path, *, imap_version: str = "0.9.0.dev1") -> None:
    _write_project(root, "meta/arbiter-suite", "arbiter-suite", "0.9.0.dev1")
    _write_project(root, "server", "arbiter-server", "0.9.0.dev1")
    _write_project(root, "plugins/imap", "arbiter-imap", imap_version)
    _write_project(root, "plugins/smtp", "arbiter-smtp", "0.9.0.dev1")


def _parse_package_keys(tool: ModuleType) -> Callable[[str], frozenset[str] | None]:
    return cast(
        Callable[[str], frozenset[str] | None],
        getattr(tool, "_parse_package_keys"),
    )


def _build_plan(tool: ModuleType) -> Callable[..., list[Any]]:
    return cast(Callable[..., list[Any]], getattr(tool, "build_plan"))


def _write_github_output(tool: ModuleType) -> Callable[..., None]:
    return cast(Callable[..., None], getattr(tool, "_write_github_output"))


def _copy_distributions(tool: ModuleType) -> Callable[..., list[Path]]:
    return cast(Callable[..., list[Path]], getattr(tool, "_copy_distributions"))


def _distribution_patterns(tool: ModuleType) -> Callable[..., tuple[str, ...]]:
    return cast(Callable[..., tuple[str, ...]], getattr(tool, "_distribution_patterns"))


def test_parse_package_keys_accepts_all_and_comma_separated_keys() -> None:
    parse_package_keys = _parse_package_keys(_load_tool())

    assert parse_package_keys("all") is None
    assert parse_package_keys(" server,imap,meta:all ") == frozenset(
        {"server", "imap", "meta:all"}
    )


def test_version_accepts_final_and_dev_versions() -> None:
    tool = _load_tool()
    version_type = getattr(tool, "Version")

    assert version_type.parse("0.9.0").text == "0.9.0"
    assert version_type.parse("0.9.0-dev1").text == "0.9.0.dev1"
    assert version_type.parse("0.9.0.dev1") < version_type.parse("0.9.0")


def test_parse_package_keys_rejects_unknown_and_mixed_all_keys() -> None:
    parse_package_keys = _parse_package_keys(_load_tool())

    with pytest.raises(ValueError, match="unknown package key"):
        parse_package_keys("server,mail")

    with pytest.raises(ValueError, match="cannot combine 'all'"):
        parse_package_keys("all,server")


def test_build_plan_only_queries_selected_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path)
    queried_packages: list[str] = []

    def fake_pypi_version(package_name: str) -> None:
        queried_packages.append(package_name)
        return None

    monkeypatch.setattr(tool, "_pypi_version", fake_pypi_version)

    plan = _build_plan(tool)(tmp_path, package_keys=frozenset({"server", "meta:all"}))

    assert queried_packages == ["arbiter-server", "arbiter-suite"]
    assert [item.package.name for item in plan if item.publish] == [
        "arbiter-server",
        "arbiter-suite",
    ]
    assert [
        item.package.name
        for item in plan
        if item.reason == "not selected by --packages"
        and item.package.kind not in {"client", "skill"}
    ] == ["arbiter-imap", "arbiter-smtp"]


def test_build_plan_discovers_new_plugin_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path)
    _write_project(tmp_path, "plugins/pop", "arbiter-pop", "0.9.0.dev1")
    queried_packages: list[str] = []

    def fake_pypi_version(package_name: str) -> None:
        queried_packages.append(package_name)
        return None

    monkeypatch.setattr(tool, "_pypi_version", fake_pypi_version)

    package_keys = getattr(tool, "_parse_package_keys")("pop", root=tmp_path)
    plan = _build_plan(tool)(tmp_path, package_keys=package_keys)

    assert queried_packages == ["arbiter-pop"]
    assert [item.package.name for item in plan if item.publish] == ["arbiter-pop"]


def test_build_plan_validates_unselected_plugin_version_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path, imap_version="0.10.0")

    monkeypatch.setattr(tool, "_pypi_version", lambda package_name: None)

    with pytest.raises(
        ValueError,
        match="arbiter-imap version line 0.10 does not match server 0.9",
    ):
        _build_plan(tool)(tmp_path, package_keys=frozenset({"server"}))


def test_build_plan_allows_selected_packages_with_different_patch_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path, imap_version="0.9.1")
    monkeypatch.setattr(tool, "_pypi_version", lambda package_name: None)

    plan = _build_plan(tool)(
        tmp_path,
        package_keys=frozenset({"imap"}),
    )

    assert [item.package.name for item in plan if item.publish] == ["arbiter-imap"]


def test_skill_publish_key_uses_server_version_and_wheel_only_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path)
    monkeypatch.setattr(tool, "_pypi_version", lambda package_name: None)

    plan = _build_plan(tool)(
        tmp_path,
        package_keys=_parse_package_keys(tool)("skill"),
    )
    publish_items = [item for item in plan if item.publish]

    assert [
        getattr(tool, "package_key_by_name")(tmp_path)[item.package.name]
        for item in publish_items
    ] == ["skill"]
    item = publish_items[0]
    assert item.package.name == "arbiter-skill"
    assert item.local_version.text == "0.9.0.dev1"
    assert _distribution_patterns(tool)(item.package, item.local_version) == (
        "arbiter_skill-0.9.0.dev1-py3-none-any.whl",
    )


def test_client_publish_key_uses_server_version_and_platform_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    _write_fixture(tmp_path)
    monkeypatch.setattr(tool, "_pypi_version", lambda package_name: None)

    plan = _build_plan(tool)(
        tmp_path,
        package_keys=_parse_package_keys(tool)("client"),
    )
    publish_items = [item for item in plan if item.publish]

    assert [
        getattr(tool, "package_key_by_name")(tmp_path)[item.package.name]
        for item in publish_items
    ] == ["client"]
    item = publish_items[0]
    assert item.package.name == "arbiter-client"
    assert item.local_version.text == "0.9.0.dev1"
    assert _distribution_patterns(tool)(item.package, item.local_version) == (
        "arbiter_client-0.9.0.dev1-py3-none-manylinux_2_17_x86_64.whl",
        "arbiter_client-0.9.0.dev1-py3-none-manylinux_2_17_aarch64.whl",
        "arbiter_client-0.9.0.dev1-py3-none-macosx_11_0_x86_64.whl",
        "arbiter_client-0.9.0.dev1-py3-none-macosx_11_0_arm64.whl",
        "arbiter_client-0.9.0.dev1-py3-none-win_amd64.whl",
        "arbiter_client-0.9.0.dev1-py3-none-win_arm64.whl",
    )


def test_write_github_output_includes_publish_keys(tmp_path: Path) -> None:
    tool = _load_tool()
    output_path = tmp_path / "github-output"

    _write_github_output(tool)(
        str(output_path),
        publish_count=2,
        publish_keys=["server", "smtp"],
        publish_specs=["arbiter-server==0.9.0", "arbiter-smtp==0.9.1"],
        publish_title="arbiter-server 0.9.0, arbiter-smtp 0.9.1",
    )

    assert output_path.read_text(encoding="utf-8") == (
        "publish_count=2\n"
        "has_publish=true\n"
        "publish_keys=server,smtp\n"
        "publish_specs=arbiter-server==0.9.0,arbiter-smtp==0.9.1\n"
        "publish_title=arbiter-server 0.9.0, arbiter-smtp 0.9.1\n"
    )


def test_copy_distributions_missing_artifact_explains_build_order(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    package_type = getattr(tool, "Package")
    version_type = getattr(tool, "Version")
    item_type = getattr(tool, "PlanItem")
    package = package_type(
        key="server",
        kind="server",
        name="arbiter-server",
        path=Path("server"),
    )
    item = item_type(
        package=package,
        local_version=version_type.parse("0.9.0.dev1"),
        pypi_version=None,
        publish=True,
        reason="project is not on PyPI",
    )

    with pytest.raises(
        FileNotFoundError,
        match="build distributions first, or omit --prepare-output-dir",
    ):
        _copy_distributions(tool)(
            dist_dir=tmp_path / "dist",
            output_dir=tmp_path / "dist-publish",
            item=item,
        )
