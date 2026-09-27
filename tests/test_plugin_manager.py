import json

import pytest

from plugin_manager import PluginError, PluginManager


def write_plugin(root, plugin_id="demo", **overrides):
    folder = root / plugin_id
    folder.mkdir()
    manifest = {
        "api_version": 1,
        "id": plugin_id,
        "name": "Demo Plugin",
        "version": "1.0.0",
        "capabilities": ["demo"],
        "permissions": ["tool:echo"],
    }
    manifest.update(overrides)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_discovery_validates_manifest_without_executing_code(tmp_path):
    folder = write_plugin(tmp_path)
    (folder / "plugin.py").write_text(
        "raise RuntimeError('must not run during discovery')", encoding="utf-8"
    )

    manager = PluginManager(tmp_path)
    plugins = manager.discover()

    assert len(plugins) == 1
    assert plugins[0].manifest.id == "demo"
    assert plugins[0].state == "discovered"


def test_enable_and_disable_are_declarative_state_transitions(tmp_path):
    write_plugin(tmp_path)
    manager = PluginManager(tmp_path)
    manager.discover()
    assert manager.enable("demo")["state"] == "enabled"
    assert manager.disable("demo")["state"] == "disabled"


def test_discovery_rejects_executable_entrypoint(tmp_path):
    write_plugin(tmp_path, entrypoint="plugin.py")
    manager = PluginManager(tmp_path)

    assert manager.discover() == []
    with pytest.raises(PluginError, match="unknown plugin"):
        manager.enable("demo")


@pytest.mark.parametrize(
    "overrides",
    [
        {"permissions": ["read"]},
        {"capabilities": "demo"},
        {"permissions": ["tool:echo", "tool:echo"]},
        {"config_schema": []},
    ],
)
def test_discovery_rejects_invalid_manifest_contract(tmp_path, overrides):
    write_plugin(tmp_path, **overrides)
    assert PluginManager(tmp_path).discover() == []


def test_config_is_persisted_in_record(tmp_path):
    write_plugin(tmp_path)
    manager = PluginManager(tmp_path)
    manager.discover()

    result = manager.configure("demo", {"enabled_for": ["local"]})

    assert result["config"] == {"enabled_for": ["local"]}
