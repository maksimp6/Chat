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


## Government Department

The Government Department provides a server-authoritative long-running case model.
The first workflow is IP_REGISTRATION.

### Case lifecycle

DRAFT -> PENDING_USER_DATA -> VALIDATING -> READY_FOR_APPROVAL -> SUBMITTED -> PROCESSING -> COMPLETED/REJECTED -> ARCHIVED

Case state is persisted in government_cases. The payload, prepared documents,
deadlines, government responses, approval state, and timestamps belong to the case.

### IP workflow

1. gov.case.create
2. gov.requirements
3. gov.collect_data
4. gov.validate_data
5. gov.tax_options
6. gov.prepare_application / gov.prepare_documents
7. gov.request_approval
8. explicit user approval
9. gov.submit
10. gov.status / gov.process_response
11. gov.archive_case

gov.submit is protected twice: the common Universal Tool Executor marks it as
approval-required, and the case itself must contain user_approved=true. The
second check is the server-side invariant and prevents a caller from bypassing
the workflow approval state.

No external government gateway is assumed by this first vertical slice. Submission
is represented as a state transition; a future GovGateway adapter can perform
the actual external transport without changing the case model.

### Data safety

Case payloads may contain personal data and therefore must not be copied into
ExecutionTrace or normal logs. Tool execution should use the existing
ExecutionTrace sanitizer and correlation chain:

conversation_id -> session_id -> invocation_id -> trace_id

The case is owner-scoped when an authenticated Alice Pro identity is available.
Anonymous test/bootstrap environments retain the existing system behavior.
