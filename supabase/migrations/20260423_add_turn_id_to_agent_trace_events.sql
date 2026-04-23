alter table if exists public.agent_trace_events
  add column if not exists turn_id text;

create index if not exists agent_trace_events_turn_idx
  on public.agent_trace_events (turn_id, created_at desc);
