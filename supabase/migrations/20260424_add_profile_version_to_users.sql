-- Add optimistic concurrency control to users profile updates.
alter table if exists public.users
add column if not exists profile_version integer not null default 0;

comment on column public.users.profile_version is
  'Monotonic version for optimistic concurrency on living_profile updates.';
