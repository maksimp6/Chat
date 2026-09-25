# Plugin System

Alice Pro plugins are local, versioned extensions discovered from `ALICE_PLUGIN_DIR`
(default: `plugins`).

## Manifest

Each plugin directory contains `plugin.json`:

```json
{
  "api_version": 1,
  "id": "example",
  "name": "Example Plugin",
  "version": "1.0.0",
  "description": "Example extension",
  "capabilities": ["example"],
  "permissions": ["read"],
  "config_schema": {},
  "entrypoint": "plugin.py"
}
```

Discovery validates the manifest but **does not execute plugin code**.

## Lifecycle

```
discover -> discovered
enable   -> enabled
disable  -> disabled
configure -> configured state retained by the manager
failure  -> failed
```

Lifecycle hook failures are contained and move the plugin to `failed` instead of
taking down the main request path.

## Security boundary

- Entry points must remain inside the plugin directory.
- Discovery never imports plugin code.
- Capabilities and permissions are declared in the manifest.
- Plugin exceptions are caught at the lifecycle boundary.
- Plugins do not receive application secrets implicitly.
- Future privileged capabilities must be mapped to the existing Tool Registry,
  Policy/Governance and approval pipeline rather than bypassing it.

## HTTP API

- `GET /api/plugins` discovers and lists installed plugins.
- `POST /api/plugins/discover` refreshes discovery.
- `POST /api/plugins/<id>/enable` enables a plugin.
- `POST /api/plugins/<id>/disable` disables a plugin.
- `PUT /api/plugins/<id>/config` updates plugin configuration.

This first slice establishes the stable manifest/lifecycle boundary. Plugin package
installation, persistent database configuration, and richer capability adapters
build on this contract.
