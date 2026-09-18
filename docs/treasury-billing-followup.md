# Treasury billing integration

Issue #12 connects the ExecutionTrace billing result to the Paper Balance
ledger without treating unknown or partially priced usage as confirmed debt.

## Runtime flow

1. /api/chat creates an InvocationContext for the request.
2. The context carries the trusted user_id when authentication provides one.
3. create_invocation_trace() copies that identity into trace context and the
   aggregated billing block as owner_id.
4. ExecutionTrace.finalize() produces the final billing aggregate.
5. settle_billing_to_treasury() posts only a fully calculated aggregate.
6. The ledger uses billing:<trace_id> as the idempotency reference.
7. Replaying the same finalized trace returns already_posted and does not
   create a second debit.
8. The complete settlement result is persisted inside the trace.

## Owner identity

Treasury never accepts owner_id from the JSON body or query string.

When authentication is present, the authentication layer should populate
Flask g.user_id or g.authenticated_user_id. For the current single-user
deployment, set the server-side ALICE_OWNER_ID value in .env. This is a
temporary deployment identity and is intentionally not client-controlled.

The missing-identity path is safe: chat can still return a model response, but
Treasury settlement is recorded as skipped with owner_identity_missing
instead of falling back to a shared default account.

## Ledger safety

Unknown pricing and partial pricing are never posted as confirmed debits.
Zero-cost results are skipped. Insufficient balance raises an error and the
Treasury transaction is rolled back, so no debit row is left behind.

## Verification

The test suite covers:

- successful billing settlement;
- duplicate settlement prevention;
- unknown and partial pricing;
- missing owner identity;
- insufficient balance;
- propagation of owner identity into trace billing context;
- rejection of client-supplied owner ids.

The Paper Balance remains demo-only. No real payment credentials or payment
processing are introduced by this integration.
