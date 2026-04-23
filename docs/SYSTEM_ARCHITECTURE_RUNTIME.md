# System Architecture Runtime

This document describes the current runtime behavior of Apna Coach from inbound webhook to final reply, including batching, queueing, multi-agent orchestration, and hot/cold memory.

## Runtime Entry

- Inbound: `POST /webhook` (Twilio WhatsApp).
- Idempotency: `processed_webhook_events` by `MessageSid`.
- Immediate ACK: contextual short response (text/audio/image aware).
- In-memory batching: `BATCH_WINDOW_SECONDS = 8.0`.

## Multi-Message Handling

- Multiple inbound events for same phone within buffer window are clubbed.
- Batch payload combines:
  - concatenated text
  - all media items (`MediaUrl0..N`)
- Per-phone FIFO queue ensures strict order.
- Per-phone async lock prevents parallel profile mutation.

## High-Level Pipeline

1. Load/create user profile (`living_profile`) from Supabase.
2. Parse media:
   - audio -> transcript merge
   - image -> vision nutrition extraction
3. Router intent classification (+ deterministic plan fallback intent).
4. Plan confirmation gate (`Yes/No`) for pending plan changes.
5. Memory Clerk extraction (skipped for burn and plan intents).
6. Schema guard sanitization (drops unknown keys).
7. Deep merge and append log arrays safely.
8. Deterministic engines:
   - Bio-Math daily targets
   - activity burn
   - current-day metrics
   - psychology bounded update
9. Persist updated profile.
10. Coach reply generation + Critic rewrite.
11. Optional plan structured generation and plan version persistence.
12. Final outbound message via Twilio REST.

## Human Delay UX

If long-running:

- Delay pings at 30s, 60s, 90s, then 150s holding note.
- No hard timeout kill of LLM chain.
- Final response sent once done.

## Hot vs Cold Memory

### Hot (`living_profile`)

- Current day dashboard + compact context.
- Psychology state.
- Active compact plan snapshot:
  - `type`, `horizon`, `current_block`, `week_blocks`, `day_actions`.

### Cold

- `historical_archive`: full day nutrition/activity history.
- `plan_versions`: versioned full plan payload history.

## Agent Roles in Runtime

- Front Desk: ACK, queue worker, delay ping management.
- Router: intent classification.
- Memory Clerk: extraction and additive updates.
- Bio-Math: deterministic calculations and targets.
- Medical Safety Officer: risky movement rewrite.
- Coach: final reasoning + response.
- Critic: final polish and compression.
- Psychology Agent: engagement/tone signals with bounded updates.
- Plan Agent: structured plan JSON for create/edit intents.

