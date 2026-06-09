from __future__ import annotations

import pytest

from arbiter_server.storage import PluginStorage, plugin_data_dir


def test_plugin_storage_allocates_paths_inside_plugin_directory(tmp_path) -> None:
    storage = PluginStorage(plugin_name="imap", root=tmp_path)

    assert storage.path("artifacts", "file") == tmp_path / "imap" / "artifacts" / "file"


def test_plugin_storage_rejects_paths_outside_scope(tmp_path) -> None:
    storage = PluginStorage(plugin_name="imap", root=tmp_path)

    with pytest.raises(ValueError, match="relative"):
        storage.path(str(tmp_path / "other"))
    with pytest.raises(ValueError, match="traverse"):
        storage.path("..", "smtp")


def test_plugin_data_dir_rejects_invalid_plugin_names(tmp_path) -> None:
    with pytest.raises(ValueError, match="invalid plugin name"):
        plugin_data_dir(tmp_path, "../smtp")
