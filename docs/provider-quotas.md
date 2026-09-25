# Provider quotas

Alice Pro enforces optional per-user provider request quotas at the shared Yandex
Responses API request boundary.

## Policy

The default policy is deployment-configured:

- ALICE_QUOTA_PERIOD_SECONDS (default 86400);
- ALICE_QUOTA_MAX_REQUESTS (default 100; 0 disables the period limit);
- ALICE_QUOTA_MAX_REQUESTS_PER_MINUTE (default 20; 0 disables the rate limit);
- ALICE_QUOTA_REQUIRE_IDENTITY (true makes missing user identity fail closed).

Additional named policies are stored in provider_quota_policies and can be
assigned to a trusted user through the administrative quota API.

## Enforcement

A request is admitted before the outbound Yandex POST. The reservation is
atomic:

- SQLite uses BEGIN IMMEDIATE;
- PostgreSQL uses a transaction plus SELECT ... FOR UPDATE.

Continuation/polling is part of the already admitted provider operation and is
not counted as a separate user request. Actual token usage is recorded when a
successful final response contains usage metadata.

Quota exhaustion returns HTTP 429 from /api/chat with a structured error
containing the reason and reset times.

## APIs

GET /api/provider-quota/me returns the current trusted user's usage.

Administrative operations require ALICE_QUOTA_ADMIN_TOKEN and the
X-Provider-Quota-Admin-Token header:

- GET /api/provider-quota/users/<user_id>;
- PUT /api/provider-quota/policies/<policy_name>;
- PUT /api/provider-quota/users/<user_id>/policy.

No provider credential material is stored in quota tables or returned by these
endpoints. Execution Trace records reservation/denial/usage events with the
trusted user id and policy metadata only.

## Important boundary

The quota system limits provider request count and records observed token/cost
usage. It is deliberately separate from provider credential lifecycle and
from the canonical billing ledger.
