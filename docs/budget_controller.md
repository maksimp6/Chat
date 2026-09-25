# Budget Controller

This slice implements the deterministic REAL/DEMO budget domain required by Issue #8.

## Contract

- REAL and DEMO are independent ledgers.
- DEMO has no monetary value and cannot be converted to REAL.
- Agents cannot allocate or replenish funds without an explicit actor and policy approval.
- Spending and reservations enforce deterministic limits.
- REAL exhaustion can switch the active mode to DEMO when fallback policy is enabled.
- Every lifecycle mutation emits an audit event through an injected trace sink.
- Events contain budget/account/amount/balance data but no credentials or secrets.

The controller does not implement gambling strategy, payment-provider access, or unrestricted access to user funds.

## Next slice

A persistence/API adapter can map these domain events to the existing ExecutionTrace and database layer without putting financial state inside the LLM context.
