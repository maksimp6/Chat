# `repository_index`

Shared contracts for immutable repository indexes and validation of a snapshot's identity.

## Public API

`from repository_index import …`: `SCHEMA_VERSION`, `SNAPSHOT_SCHEMA_VERSION`, `snapshot_revision`.

| Module | Purpose |
| --- | --- |
| [`repository_index.provenance`](provenance.py) | Validation of immutable repository-index snapshot identity. |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
