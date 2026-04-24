create table if not exists public.llm_call_events (
  id uuid primary key default gen_random_uuid(),
  ts timestamptz not null default timezone('utc', now()),
  operation_id text,
  trace_id text,
  turn_id text,
  phone_number text,
  agent text not null,
  stage text not null,
  model text,
  status text not null default 'ok',
  latency_ms integer not null default 0,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens integer not null default 0,
  request_chars integer not null default 0,
  response_chars integer not null default 0,
  request_preview text not null default '',
  response_preview text not null default '',
  error_text text not null default ''
);

create index if not exists idx_llm_call_events_operation on public.llm_call_events (operation_id, ts desc);
create index if not exists idx_llm_call_events_trace on public.llm_call_events (trace_id, ts desc);

create table if not exists public.operation_metrics (
  id uuid primary key default gen_random_uuid(),
  ts timestamptz not null default timezone('utc', now()),
  operation_id text unique not null,
  trace_id text,
  turn_id text,
  phone_number text,
  total_latency_ms integer not null default 0,
  llm_calls_estimate integer not null default 0,
  final_status text not null default 'ok',
  routed_intent text not null default '',
  router_confidence text not null default '',
  intent_source text not null default '',
  total_prompt_tokens integer not null default 0,
  total_completion_tokens integer not null default 0,
  total_tokens integer not null default 0
);
