create table if not exists public.plan_versions (
  id uuid primary key default gen_random_uuid(),
  phone_number text not null,
  plan_id text not null,
  version int not null,
  status text not null default 'active',
  change_reason text not null default '',
  plan_payload jsonb not null,
  created_at timestamptz not null default timezone('utc', now()),
  unique (phone_number, plan_id, version)
);

create index if not exists plan_versions_phone_created_idx
  on public.plan_versions (phone_number, created_at desc);
