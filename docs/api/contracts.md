# API Contracts

Alice Pro treats every API boundary as an explicit contract.

## Rule

Communication over HTTP API must use a named request/response contract. A route,
client, tool, or agent must not depend on undocumented fields or silently accept
payload drift.

For every endpoint, the contract defines:

- allowed request fields;
- required request fields;
- exact JSON types;
- nullable fields;
- enum values where applicable;
- allowed response fields;
- required response fields;
- HTTP status codes;
- error response shapes.

Unknown fields are rejected unless a contract explicitly allows additional
properties.

## Strictness

Contracts are intentionally strict:

- a missing required field is an error;
- an unexpected field is an error;
- a wrong JSON type is an error;
- blank text does not satisfy a non-empty string contract;
- Python `bool` is not accepted as an integer;
- a response that adds a field without first changing its contract is an error;
- error responses are contracts too.

API evolution therefore follows this order:

```text
contract change
    ↓
producer implementation
    ↓
consumer implementation
    ↓
contract tests
    ↓
integration/runtime tests
```

A producer and consumer must never be changed independently by relying on an
implicit payload shape.

## Runtime API reference implementation

`api_contracts.py` is the current contract registry and strict validator for
the runtime/session/invocation API.

`runtime_api.py` enforces two boundaries:

- `_request_payload(contract_name)` validates incoming JSON before business logic;
- `_contract_response(contract_name, payload, status)` validates outgoing JSON
  before serialization.

Runtime routes are forbidden from calling `request.get_json()` or `jsonify()`
directly. Architecture tests enforce this.

## Test levels

API tests must cover all of these levels:

1. **Contract validator tests**: required fields, extra fields, types, nullable
   values, enums, nested structures, and invalid payloads.
2. **Architecture tests**: routes cannot bypass contract helpers and every
   referenced contract name must exist.
3. **Route tests**: real Flask requests and responses are validated against the
   named contract.
4. **Negative route tests**: unexpected fields, wrong types, missing required
   fields, and invalid values must produce the documented error contract.
5. **End-to-end tests**: consumers exercise the real endpoint and validate the
   resulting observable behavior.

A green test that checks only for a field name or source-code string is not
sufficient evidence that an API works.

## Extending the contract registry

When adding a new endpoint:

1. add named request, success-response, and error-response contracts;
2. validate the request at the route boundary;
3. validate every response at the route boundary;
4. add negative tests for every rejected class of payload;
5. add an architecture test if the endpoint family can bypass the shared
   boundary;
6. update consumers only after the contract is explicit.

The long-term target is that every internal HTTP API family follows the same
contract-first rule.
