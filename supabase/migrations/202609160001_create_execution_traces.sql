create table if not exists public.execution_traces (
    trace_id text primary key,
    created_at double precision,
    payload jsonb not null,
    mirrored_at timestamptz not null default now()
);

alter table public.execution_traces enable row level security;

revoke all on table public.execution_traces from anon, authenticated;

grant insert on table public.execution_traces to service_role;
grant select on table public.execution_traces to service_role;
