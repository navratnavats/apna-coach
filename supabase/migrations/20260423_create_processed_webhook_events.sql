-- ============================================================================
-- Twilio webhook idempotency ledger
-- ============================================================================

create extension if not exists "pgcrypto";

create table if not exists public.processed_webhook_events (
  id uuid primary key default gen_random_uuid(),
  message_sid text not null unique,
  phone_number text not null,
  payload_type text not null default 'twilio_inbound',
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_processed_webhook_events_phone_created
  on public.processed_webhook_events (phone_number, created_at desc);

comment on table public.processed_webhook_events is
  'Idempotency ledger for inbound webhook events (prevents duplicate processing).';
