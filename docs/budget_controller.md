# Budget Controller

This slice implements the deterministic REAL/DEMO budget domain required by Issue #8 and the lifecycle edge cases from the design review.

## Contract

- REAL and DEMO are independent ledgers.
- DEMO has no monetary value and cannot be converted to REAL.
- DEMO → REAL requires explicit external authorization. A newly replenished REAL account is not activated automatically.
- Agents cannot allocate or replenish funds without an explicit actor and policy approval.
- Spending and reservations are serialized by a controller lock so concurrent agent requests cannot double-spend the same balance.
- Reservations can be settled into spent or released after an external provider accepts/rejects an operation.
- Provider-confirmed winnings are recorded separately as `won`; the controller does not infer or mint winnings from agent output.
- Refunds reverse spent/loss accounting deterministically.
- Daily and total loss limits are enforced independently of the LLM.
- A configurable cooldown can lock an account after its daily loss limit is reached.
- The daily loss counter resets when the controller observes a new UTC day.
- REAL exhaustion can switch the active mode to DEMO when fallback policy is enabled.
- Every lifecycle mutation emits an audit event through an injected trace sink.
- Events contain budget/account/amount/balance data but no credentials or secrets.

## Lifecycle

```text
Agent intent
    ↓
Budget Controller
    ├── reserve
    ├── settle reservation → spent
    ├── release reservation
    ├── record provider-confirmed win
    └── refund
    ↓
ExecutionTrace
```

The LLM never becomes the source of truth for balances, winnings, approvals, cooldowns, or provider outcomes.

## Safety boundaries

The controller does not implement gambling strategy, payment-provider access, or unrestricted access to user funds.

External policy/governance remains authoritative for replenishment and explicit DEMO → REAL activation. The persistence/API adapter should preserve optimistic/concurrent update guarantees when this in-memory domain is connected to the database.

## Trace expectations

Lifecycle events should be mapped to the existing ExecutionTrace without storing secrets, credentials, payment tokens, or private keys. The event schema includes:

- budget identifier;
- account type;
- currency;
- amount;
- balance before/after;
- lifecycle event;
- cooldown/authorization metadata when relevant.

## Next integration slice

A persistence/API adapter should connect this deterministic domain to the database and ExecutionTrace using transactional updates/version checks. Payment-provider integration must remain outside the agent and outside the LLM context.
