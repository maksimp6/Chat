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
  "permissions": ["tool:alice_search_project"],
  "config_schema": {}
}
```

Discovery validates the manifest but **does not execute plugin code**. Version 1
is deliberately declarative: executable entry points and unknown fields are
rejected. This prevents a manifest from becoming an implicit Python import with
access to host globals.

## Lifecycle

```
discover -> discovered
enable   -> enabled
disable  -> disabled
configure -> configured state retained by the manager
failure  -> failed
```

Lifecycle operations are declarative state transitions. Plugins do not run
in-process lifecycle hooks.

## Security boundary

- Discovery never imports plugin code and rejects executable entry points.
- Capabilities describe what a plugin may request. Permissions use explicit
  `tool:<registered-tool-name>` grants; undeclared tools are denied.
- `PluginExecutionGateway` gives plugins only tool names and JSON-like arguments.
  It creates a per-invocation `plugin:<id>:<invocation-id>` runtime scope through
  `RuntimeDispatcher`, then uses
  `UniversalToolExecutor` for schema validation, authorization, policy, approval,
  and execution.
- The caller's invocation and trace IDs are preserved in every tool call and the
  result audit metadata includes the plugin and runtime IDs.
- Plugins never receive application globals, secrets, database handles, connector
  clients, the tool registry, or the dispatcher.

## HTTP API

- `GET /api/plugins` discovers and lists installed plugins.
- `POST /api/plugins/discover` refreshes discovery.
- `POST /api/plugins/<id>/enable` enables a plugin.
- `POST /api/plugins/<id>/disable` disables a plugin.
- `PUT /api/plugins/<id>/config` updates plugin configuration.

This foundation is a prerequisite for the autonomous-development work in #256.
Package installation, persistent configuration, and out-of-process plugin hosts
can build on this contract without weakening its execution boundary. Related
platform work is tracked in #350, #326, and #238.
