# Agent Lifecycle And States

This file explains behavior by user stage: new user, existing user, first message, Nth message, plan edits, and normal Q&A.

## New User: First Message

1. User row absent -> default profile created from `RICH_USER_STATE.md`.
2. Intake missing fields checked.
3. If onboarding incomplete:
   - Intake prompt returned (asks missing basics).
4. If enough fields appear:
   - Bio-Math daily targets computed.
   - onboarding may graduate to complete.

## Existing User: First Message of Day

1. Profile loaded.
2. Fresh logs appended (nutrition/activity/workout/hydration).
3. Daily metrics recomputed deterministically.
4. Psychology state nudged by latest message.
5. Coach response generated from updated state.

## Nth Message (Normal Flow)

- Same pipeline, but with richer context from prior logs.
- Motivation cadence adapts using `motivation_style` and message count.
- Factual metric queries use deterministic metrics first.

## Plan Lifecycle

### Plan Create

- Intent: `plan_create_request`.
- Structured plan generated (`type`, `horizon`, `week_blocks`, `day_actions`).
- Plan version persisted to cold storage.
- Compact active slice synced to hot profile.

### Plan Status Query

- Intent: `plan_status_query`.
- Latest plan fetched from cold and injected into coach context.
- Coach answers from plan continuity, not isolated turn.

### Plan Edit

- Intent: `plan_edit_request`.
- Structured revised plan generated.
- Version increment persisted.
- Hot active slice updated.

### Plan Change Signal (Vacation/Missed Day)

- Intent: `plan_change_signal`.
- System asks confirmation (`Yes/No`) before mutation.
- `Yes` => edit path.
- `No` => no plan mutation.

## Outbound Quality Control

- Critic rewrites outbound text for WhatsApp readability.
- Addressing uses profile preference (`preferred_title`/name fallback).
- Safety and factual constraints preserved.

