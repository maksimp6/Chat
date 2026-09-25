-- Alice Pro Issue #8 budget persistence
-- Apply through the repository's normal migration runner.
-- The database function uses row locks so concurrent workers cannot double-spend.

create table if not exists budget_accounts (
  budget_id text not null,
  account_type text not null check (account_type in ('REAL','DEMO')),
  currency text not null,
  allocated numeric(20,2) not null default 0 check (allocated >= 0),
  spent numeric(20,2) not null default 0 check (spent >= 0),
  reserved numeric(20,2) not null default 0 check (reserved >= 0),
  won numeric(20,2) not null default 0 check (won >= 0),
  loss_today numeric(20,2) not null default 0 check (loss_today >= 0),
  loss_period date not null default current_date,
  max_single_operation numeric(20,2) not null check (max_single_operation >= 0),
  max_daily_loss numeric(20,2) not null check (max_daily_loss >= 0),
  max_total_loss numeric(20,2) not null check (max_total_loss >= 0),
  locked_until timestamptz,
  version bigint not null default 0,
  updated_at timestamptz not null default now(),
  primary key (budget_id, account_type)
);

create table if not exists budget_operations (
  operation_id uuid primary key default gen_random_uuid(),
  budget_id text not null,
  account_type text not null check (account_type in ('REAL','DEMO')),
  operation_type text not null check (operation_type in ('RESERVE','SETTLE','RELEASE','SPEND','REFUND','WIN')),
  amount numeric(20,2) not null check (amount >= 0),
  status text not null check (status in ('APPLIED','REJECTED')),
  idempotency_key text,
  actor text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (budget_id, idempotency_key)
);

alter table budget_accounts enable row level security;
alter table budget_operations enable row level security;

create index if not exists budget_operations_budget_created_idx
  on budget_operations (budget_id, created_at desc);

create or replace function apply_budget_operation(
  p_budget_id text,
  p_account_type text,
  p_operation_type text,
  p_amount numeric,
  p_idempotency_key text default null,
  p_actor text default null
) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  a budget_accounts%rowtype;
  result jsonb;
begin
  if p_amount < 0 then raise exception 'amount must be non-negative'; end if;

  select * into a
    from budget_accounts
   where budget_id = p_budget_id and account_type = p_account_type
   for update;

  if not found then raise exception 'budget account not found'; end if;

  if p_idempotency_key is not null then
    select jsonb_build_object('operation_id', operation_id, 'status', status)
      into result
      from budget_operations
     where budget_id = p_budget_id and idempotency_key = p_idempotency_key;
    if result is not null then return result; end if;
  end if;

  if p_operation_type = 'RESERVE' then
    if p_amount > (a.allocated + a.won - a.spent - a.reserved) then
      raise exception 'insufficient available budget';
    end if;
    update budget_accounts set reserved = reserved + p_amount,
      version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  elsif p_operation_type = 'SETTLE' then
    if p_amount > a.reserved then raise exception 'cannot settle more than reserved'; end if;
    update budget_accounts set reserved = reserved - p_amount,
      spent = spent + p_amount,
      loss_today = case when loss_period = current_date then loss_today + p_amount else p_amount end,
      loss_period = current_date, version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  elsif p_operation_type = 'RELEASE' then
    if p_amount > a.reserved then raise exception 'cannot release more than reserved'; end if;
    update budget_accounts set reserved = reserved - p_amount,
      version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  elsif p_operation_type = 'SPEND' then
    if p_amount > (a.allocated + a.won - a.spent - a.reserved) then
      raise exception 'insufficient available budget';
    end if;
    update budget_accounts set spent = spent + p_amount,
      loss_today = case when loss_period = current_date then loss_today + p_amount else p_amount end,
      loss_period = current_date, version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  elsif p_operation_type = 'REFUND' then
    if p_amount > a.spent then raise exception 'cannot refund more than spent'; end if;
    update budget_accounts set spent = spent - p_amount,
      loss_today = greatest(0, case when loss_period = current_date then loss_today - p_amount else 0 end),
      loss_period = current_date, version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  elsif p_operation_type = 'WIN' then
    update budget_accounts set won = won + p_amount,
      version = version + 1, updated_at = now()
      where budget_id = p_budget_id and account_type = p_account_type;

  else
    raise exception 'unsupported operation type';
  end if;

  insert into budget_operations
    (budget_id, account_type, operation_type, amount, status, idempotency_key, actor)
  values
    (p_budget_id, p_account_type, p_operation_type, p_amount, 'APPLIED', p_idempotency_key, p_actor)
  returning jsonb_build_object('operation_id', operation_id, 'status', status) into result;

  return result;
end;
$$;

revoke all on function apply_budget_operation(text,text,text,numeric,text,text) from public;
