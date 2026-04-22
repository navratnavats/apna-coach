-- ============================================================================
-- Foundational users table for Apna Coach
-- Target: Supabase (PostgreSQL)
-- Safe to run in Supabase SQL Editor
-- ============================================================================

-- Ensure UUID generation is available for primary keys.
create extension if not exists "pgcrypto";

-- ----------------------------------------------------------------------------
-- 1) Core users table
-- ----------------------------------------------------------------------------
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),

  -- Main external identifier from WhatsApp.
  phone_number text not null unique,

  -- Living User Profile (Rich JSON source of truth).
  -- This can contain onboarding, audio preferences, training environment,
  -- injuries, goals, logs, and other evolving profile attributes.
  living_profile jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

comment on table public.users is
  'Primary user identity and living Rich JSON profile for Apna Coach.';

comment on column public.users.phone_number is
  'Unique WhatsApp phone number identifier (E.164 recommended).';

comment on column public.users.living_profile is
  'Living user profile JSON (includes audio preferences and training environments).';

-- ----------------------------------------------------------------------------
-- 2) Trigger function to auto-refresh updated_at on row updates
-- ----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

-- ----------------------------------------------------------------------------
-- 3) Trigger binding
-- ----------------------------------------------------------------------------
drop trigger if exists trg_users_set_updated_at on public.users;

create trigger trg_users_set_updated_at
before update on public.users
for each row
execute function public.set_updated_at();
