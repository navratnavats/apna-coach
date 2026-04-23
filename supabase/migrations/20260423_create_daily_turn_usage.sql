create table if not exists public.daily_turn_usage (
  id uuid primary key default gen_random_uuid(),
  phone_number text not null,
  usage_date date not null,
  used_turns int not null default 0,
  updated_at timestamptz not null default timezone('utc', now()),
  created_at timestamptz not null default timezone('utc', now()),
  unique (phone_number, usage_date)
);

create index if not exists daily_turn_usage_phone_date_idx
  on public.daily_turn_usage (phone_number, usage_date desc);
