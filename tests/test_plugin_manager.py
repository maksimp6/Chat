import json

import pytest

from plugin_manager import PluginError, PluginManager


def write_plugin(root, plugin_id="demo", entrypoint=None):
    folder = root / plugin_id
    folder.mkdir()
    manifest = {
        "api_version": 1,
        "id": plugin_id,
        "name": "Demo Plugin",
        "version": "1.0.0",
        "capabilities": ["demo"],
        "permissions": ["read"],
    }
    if entrypoint:
        manifest["entrypoint"] = entrypoint
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_discovery_validates_manifest_without_executing_code(tmp_path):
    folder = write_plugin(tmp_path, entrypoint="plugin.py")
    (folder / "plugin.py").write_text("raise RuntimeError('must not run during discovery')", encoding="utf-8")

    manager = PluginManager(tmp_path)
    plugins = manager.discover()

    assert len(plugins) == 1
    assert plugins[0].manifest.id == "demo"
    assert plugins[0].state == "discovered"


def test_enable_and_disable_isolated_hook_failure(tmp_path):
    folder = write_plugin(tmp_path, entrypoint="plugin.py")
    (folder / "plugin.py").write_text(
        "def on_enable(config):\n"
        "    raise RuntimeError('boom')\n"
        "def on_disable(config):\n"
        "    pass\n",
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path)
    manager.discover()

    with pytest.raises(PluginError, match="plugin enable failed"):
        manager.enable("demo")
    assert manager.get("demo").state == "failed"
    assert manager.get("demo").error == "boom"


def test_discovery_rejects_entrypoint_escape(tmp_path):
    write_plugin(tmp_path, entrypoint="../outside.py")
    manager = PluginManager(tmp_path)

    assert manager.discover() == []
    with pytest.raises(PluginError, match="unknown plugin"):
        manager.enable("demo")


def test_config_is_persisted_in_record(tmp_path):
    write_plugin(tmp_path)
    manager = PluginManager(tmp_path)
    manager.discover()

    result = manager.configure("demo", {"enabled_for": ["local"]})

    assert result["config"] == {"enabled_for": ["local"]}
