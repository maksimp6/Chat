# Departments

Departments are first-class domain containers that group an agent, capabilities, tools, policies, knowledge scope, session configuration and status.

The repository provides a persistent SQLite-backed registry and REST API:

- GET /api/departments
- GET /api/departments/:id
- POST /api/departments
- PUT /api/departments/:id
- DELETE /api/departments/:id
- POST /api/departments/:id/sessions
- GET /api/departments/:id/sessions/:session

Mutating endpoints require the backend-only ALICE_DEPARTMENTS_ADMIN_TOKEN. Discovery and session launch are read-only operations from the web UI.

Department sessions are represented by the existing Invocation/ExecutionTrace infrastructure. Agents and tools must still pass through the Agent Gateway and Universal Tool Executor. Departments do not get a bypass path.

The first implementation is system-level. User-specific department isolation will bind to the authenticated user identity once the full user authentication boundary replaces anonymous bootstrap.
