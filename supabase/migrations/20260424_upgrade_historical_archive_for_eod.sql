-- Support idempotent archive upsert + late-entry merges.
alter table if exists public.historical_archive
add column if not exists archive_version integer not null default 1;

alter table if exists public.historical_archive
add column if not exists updated_at timestamptz not null default timezone('utc', now());

create unique index if not exists uq_historical_archive_phone_date
  on public.historical_archive (phone_number, archive_date);
