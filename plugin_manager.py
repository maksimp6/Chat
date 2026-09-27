"""First-class plugin lifecycle and manifest management for Alice Pro.

Plugins are discovered from a configured local directory. The manager validates
declarative manifests and never imports or executes plugin code. Privileged work
must be requested through the separate scoped execution gateway.
"""

from __future__ import annotations

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
ALLOWED_MANIFEST_FIELDS = {
    "api_version",
    "id",
    "name",
    "version",
    "description",
    "capabilities",
    "permissions",
    "config_schema",
}


class PluginError(ValueError):
    """Raised when a plugin manifest or lifecycle operation is invalid."""


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    api_version: int
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    config_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PluginManifest":
        if not isinstance(data, Mapping):
            raise PluginError("manifest must be a JSON object")
        unknown = sorted(set(data) - ALLOWED_MANIFEST_FIELDS)
        if unknown:
            raise PluginError(f"manifest contains unknown fields: {', '.join(unknown)}")
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

        capabilities = _string_list(data.get("capabilities"), "capabilities")
        permissions = _string_list(data.get("permissions"), "permissions")
        invalid_permissions = [item for item in permissions if not item.startswith("tool:")]
        if invalid_permissions:
            raise PluginError("permissions must use the 'tool:<name>' namespace")

        config_schema = data.get("config_schema", {})
        if not isinstance(config_schema, Mapping):
            raise PluginError("config_schema must be an object")

        return cls(
            id=plugin_id,
            name=str(data["name"]).strip(),
            version=str(data["version"]).strip(),
            api_version=api_version,
            capabilities=capabilities,
            permissions=permissions,
            config_schema=dict(config_schema),
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
        record.state, record.error = "enabled", None
        return self._public(record)

    def disable(self, plugin_id: str) -> dict[str, Any]:
        record = self.get(plugin_id)
        record.state, record.error = "disabled", None
        return self._public(record)

    def configure(self, plugin_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        record = self.get(plugin_id)
        if not isinstance(config, Mapping):
            raise PluginError("plugin config must be an object")
        record.config = dict(config)
        return self._public(record)

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


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise PluginError(f"{field_name} must be an array of non-empty strings")
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        raise PluginError(f"{field_name} must not contain duplicates")
    return normalized
