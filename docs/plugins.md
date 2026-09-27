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
  "capabilities": ["read", "wikipedia"],
  "permissions": ["tool:wikipedia_search"],
  "config_schema": {}
}
```

Discovery validates the manifest but **does not execute plugin code**. Unknown
fields are rejected; in particular, executable entry points and lifecycle hooks
are not part of API version 1. Each permission is an exact `tool:<name>` grant.

## Lifecycle

```
discover -> discovered
enable   -> enabled
disable  -> disabled
configure -> configured state retained by the manager
failure  -> failed
```

These operations only update manager-owned state and configuration. They never
import or invoke files supplied by a plugin.

## Security boundary

- Plugins cannot declare executable manifest fields and are never imported into
  the host process.
- A tool call requires both an exact permission grant and every capability
  declared by that tool. A permission grant does not bypass tool approval.
- `PluginExecutionGateway` creates a short-lived invocation runtime, dispatches
  within that scope, and then calls `UniversalToolExecutor`; plugins never
  receive the registry, dispatcher, database, connector clients, or secrets.
- Invocation and trace IDs are preserved in the tool call, audit metadata, and
  execution trace. Tool-defined trace redaction continues to apply.
- Cross-runtime resources remain guarded by `RuntimeDispatcher`; no legacy
  per-preview localhost/container path is introduced.

Callers construct an `InvocationContext` from the request boundary and pass it
to `PluginExecutionGateway.execute`. The gateway must not be replaced with a
direct registry call. This contract is the prerequisite for plugin-consuming
work in #256 and follows the runtime isolation architecture in #350 (with tool
architecture context in #326 and #238).

## HTTP API

- `GET /api/plugins` discovers and lists installed plugins.
- `POST /api/plugins/discover` refreshes discovery.
- `POST /api/plugins/<id>/enable` enables a plugin.
- `POST /api/plugins/<id>/disable` disables a plugin.
- `PUT /api/plugins/<id>/config` updates plugin configuration.

This first slice establishes the stable manifest, lifecycle, and execution
boundary. Package installation and persistent configuration remain future work;
changing the previous executable `entrypoint` prototype requires removing that
field from installed manifests. No database migration is required.
