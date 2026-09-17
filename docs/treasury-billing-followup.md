# Treasury billing integration follow-up

This document defines the next implementation phase for issue #12.

## Scope

- Convert calculated billing items into Treasury debit ledger entries.
- Use the authenticated user's stable identity instead of the hard-coded `default` owner.
- Make billing-to-Treasury posting idempotent using a stable operation/reference key.
- Attach `trace_id`, `conversation_id`, `session_id`, and `invocation_id` to the accounting record where available.
- Keep unknown pricing items out of confirmed debit entries.
- Add unit and integration tests for successful posting, duplicate prevention, insufficient balance, and missing user identity.

## Safety constraints

- Paper Balance only; no real payment processing.
- No secrets or payment credentials in ledger records.
- Treasury operations must remain behind authorized service/local-tool boundaries.
