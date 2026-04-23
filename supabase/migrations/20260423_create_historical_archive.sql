-- ============================================================================
-- Historical archive table for cold storage (EOD compression output)
-- Target: Supabase (PostgreSQL)
-- ============================================================================

create extension if not exists "pgcrypto";

create table if not exists public.historical_archive (
  id uuid primary key default gen_random_uuid(),
  phone_number text not null,
  archive_date date not null,
  summary_line text not null default '',
  metrics jsonb not null default '{}'::jsonb,
  nutrition_entries jsonb not null default '[]'::jsonb,
  activity_entries jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_historical_archive_phone_date
  on public.historical_archive (phone_number, archive_date desc);

comment on table public.historical_archive is
  'Cold storage of raw daily logs (nutrition/activity) plus compressed day summary.';

comment on column public.historical_archive.metrics is
  'Daily aggregate metrics used for analytics/history fetch.';
