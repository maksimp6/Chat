"""First-class plugin lifecycle and manifest management for Alice Pro.

Plugins are discovered from a configured local directory. The manager validates
manifests before loading anything and never executes plugin code during
discovery. Optional Python lifecycle hooks are loaded only after an explicit
enable operation and are isolated behind exception handling.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("plugin_manager")

MANIFEST_NAME = "plugin.json"
SUPPORTED_MANIFEST_VERSION = 1
VALID_STATES = {"discovered", "enabled", "disabled", "failed"}
VALID_HOOKS = {"on_enable", "on_disable", "on_configure"}


class PluginError(ValueError):
    """Raised when a plugin manifest or lifecycle operation is invalid."""


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    api_version: int
    entrypoint: str | None = None
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    config_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PluginManifest":
        required = ("id", "name", "version")
        missing = [key for key in required if not str(data.get(key) or "").strip()]
        if missing:
            raise PluginError(f"manifest missing required fields: {', '.join(missing)}")

        plugin_id = str(data["id"]).strip()
        if not plugin_id.replace(".", "").replace("-", "").replace("_", "").isalnum():
            raise PluginError("plugin id must contain only letters, digits, '.', '_' or '-'")

        api_version = int(data.get("api_version", SUPPORTED_MANIFEST_VERSION))
        if api_version != SUPPORTED_MANIFEST_VERSION:
            raise PluginError(f"unsupported plugin api_version: {api_version}")

        entrypoint = data.get("entrypoint")
        if entrypoint is not None:
            entrypoint = str(entrypoint).strip() or None
            if entrypoint and (Path(entrypoint).is_absolute() or ".." in Path(entrypoint).parts):
                raise PluginError("entrypoint must remain inside the plugin directory")

        return cls(
            id=plugin_id,
            name=str(data["name"]).strip(),
            version=str(data["version"]).strip(),
            api_version=api_version,
            entrypoint=entrypoint,
            capabilities=tuple(str(x) for x in (data.get("capabilities") or [])),
            permissions=tuple(str(x) for x in (data.get("permissions") or [])),
            config_schema=dict(data.get("config_schema") or {}),
            description=str(data.get("description") or ""),
        )


@dataclass
class PluginRecord:
    manifest: PluginManifest
    path: Path
    state: str = "discovered"
    config: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class PluginManager:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        configured = root or os.environ.get("ALICE_PLUGIN_DIR", "plugins")
        self.root = Path(configured).expanduser().resolve()
        self.records: dict[str, PluginRecord] = {}

    def discover(self) -> list[PluginRecord]:
        self.root.mkdir(parents=True, exist_ok=True)
        discovered: dict[str, PluginRecord] = {}
        for manifest_path in sorted(self.root.glob(f"*/{MANIFEST_NAME}")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_dict(data)
                if manifest.id in discovered:
                    raise PluginError(f"duplicate plugin id: {manifest.id}")
                discovered[manifest.id] = PluginRecord(manifest=manifest, path=manifest_path.parent)
            except Exception as exc:
                logger.warning("Plugin discovery failed for %s: %s", manifest_path, exc)
        self.records = discovered
        return list(discovered.values())

    def list(self) -> list[dict[str, Any]]:
        return [self._public(record) for record in self.records.values()]

    def get(self, plugin_id: str) -> PluginRecord:
        try:
            return self.records[plugin_id]
        except KeyError as exc:
            raise PluginError(f"unknown plugin: {plugin_id}") from exc

    def enable(self, plugin_id: str) -> dict[str, Any]:
        record = self.get(plugin_id)
        if record.state == "enabled":
            return self._public(record)
        try:
            module = self._load_entrypoint(record)
            self._call_hook(module, "on_enable", record.config)
            record.state, record.error = "enabled", None
        except Exception as exc:
            record.state, record.error = "failed", str(exc)
            logger.exception("Plugin %s failed to enable", plugin_id)
            raise PluginError(f"plugin enable failed: {plugin_id}") from exc
        return self._public(record)

    def disable(self, plugin_id: str) -> dict[str, Any]:
        record = self.get(plugin_id)
        try:
            module = self._load_entrypoint(record, required=False)
            self._call_hook(module, "on_disable", record.config)
            record.state, record.error = "disabled", None
        except Exception as exc:
            record.state, record.error = "failed", str(exc)
            logger.exception("Plugin %s failed to disable", plugin_id)
            raise PluginError(f"plugin disable failed: {plugin_id}") from exc
        return self._public(record)

    def configure(self, plugin_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        record = self.get(plugin_id)
        if not isinstance(config, Mapping):
            raise PluginError("plugin config must be an object")
        record.config = dict(config)
        try:
            module = self._load_entrypoint(record, required=False)
            self._call_hook(module, "on_configure", record.config)
        except Exception as exc:
            record.error = str(exc)
            record.state = "failed"
            logger.exception("Plugin %s failed to configure", plugin_id)
            raise PluginError(f"plugin configure failed: {plugin_id}") from exc
        return self._public(record)

    def _load_entrypoint(self, record: PluginRecord, required: bool = True):
        if not record.manifest.entrypoint:
            if required:
                return None
            return None
        target = (record.path / record.manifest.entrypoint).resolve()
        if record.path.resolve() not in target.parents or target == record.path.resolve():
            raise PluginError("plugin entrypoint escapes plugin directory")
        if not target.is_file():
            raise PluginError(f"plugin entrypoint not found: {record.manifest.entrypoint}")
        module_name = "alice_plugin_" + record.manifest.id.replace("-", "_").replace(".", "_")
        spec = importlib.util.spec_from_file_location(module_name, target)
        if spec is None or spec.loader is None:
            raise PluginError("unable to load plugin entrypoint")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _call_hook(module: Any, hook: str, config: Mapping[str, Any]) -> None:
        if module is None:
            return
        if hook not in VALID_HOOKS:
            raise PluginError(f"unsupported plugin hook: {hook}")
        callback = getattr(module, hook, None)
        if callback is not None:
            callback(dict(config))

    @staticmethod
    def _public(record: PluginRecord) -> dict[str, Any]:
        return {
            "id": record.manifest.id,
            "name": record.manifest.name,
            "version": record.manifest.version,
            "api_version": record.manifest.api_version,
            "description": record.manifest.description,
            "capabilities": list(record.manifest.capabilities),
            "permissions": list(record.manifest.permissions),
            "state": record.state,
            "config": dict(record.config),
            "error": record.error,
        }


plugin_manager = PluginManager()
