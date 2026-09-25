# Reusable AI sessions

Alice Pro exposes immutable built-in session profiles that can be cloned into
independent persisted sessions.

## Built-in profiles

The initial profiles are:

- developer: filesystem, terminal and git tools
- researcher: web and MCP tools
- assistant: general assistant profile
- terminal: terminal and filesystem tools
- agent: filesystem, terminal and MCP tools

Profiles are templates, not mutable sessions.

## API

List profiles:

GET /api/session-profiles

Inspect one:

GET /api/session-profiles/<profile_id>

Clone one into a persisted session:

POST /api/session-profiles/<profile_id>/clone

Optional request body:

{"name": "My Developer"}

The response contains both the created persisted session and its copied profile
configuration. The copy is independent from the built-in template.

A created session can then be used by the existing invocation lifecycle:

POST /api/sessions/<session_id>/invocations

with a conversation_id.

## Runtime boundary

The existing virtual low-consumption runtime remains deliberately lightweight.
It is a managed subprocess environment, not a security sandbox. Production
untrusted-code execution requires a container, VM, or another runtime backend
with enforceable isolation.
