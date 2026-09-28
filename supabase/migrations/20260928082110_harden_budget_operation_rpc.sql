-- Keep budget mutations available to the trusted backend role only.
-- These statements are idempotent and also reconcile the live project,
-- whose recorded baseline already contains an invoker-rights version.
alter function public.apply_budget_operation(text, text, text, numeric, text, text, integer)
  security invoker;

alter function public.apply_budget_operation(text, text, text, numeric, text, text, integer)
  set search_path = public, pg_temp;

revoke all on function public.apply_budget_operation(text, text, text, numeric, text, text, integer)
  from public, anon, authenticated;

grant execute on function public.apply_budget_operation(text, text, text, numeric, text, text, integer)
  to service_role;

revoke all on table public.budget_accounts, public.budget_operations
  from anon, authenticated;
