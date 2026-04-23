create table if not exists public.agent_trace_events (
  id uuid primary key default gen_random_uuid(),
  ts timestamptz not null,
  trace_id text,
  phone_number text,
  agent text not null,
  stage text not null,
  status text not null default 'ok',
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists agent_trace_events_trace_idx
  on public.agent_trace_events (trace_id, created_at desc);

create index if not exists agent_trace_events_phone_idx
  on public.agent_trace_events (phone_number, created_at desc);

create index if not exists agent_trace_events_created_idx
  on public.agent_trace_events (created_at desc);
