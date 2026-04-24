# Pre 3-User Launch Brief

## Why this document exists
- Keep a stable internal record of decisions so context is not lost across chats/summaries.
- Align founder + CTO thinking before onboarding 3 more trial users.
- Track what we are doing, why we are doing it, and expected impact.

## Current stage
- Product is in pre-beta trial mode (initial users active, expansion pending).
- Objective is not feature explosion; objective is reliability, trust, and response quality at acceptable cost/latency.

## What we are doing now (priority order)
1. Logging trust:
   - Meal/activity reported by user must be stored and retrievable reliably.
2. Onboarding smoothness:
   - Chunk-friendly onboarding, no hidden commands, no accidental reset/edit loops.
3. Latency reduction:
   - Reduce slow turns by trimming unnecessary LLM/agent hops.
4. Response quality:
   - Direct answer first; motivation secondary; better query understanding.
5. Capability clarity:
   - User should know what app can do (guided discovery), not guess blindly.

## Why we are doing this first
- Trust break (log not recalled) is a retention killer.
- Onboarding friction burns quota and user patience.
- High latency kills WhatsApp engagement.
- Better quality + clarity improves early user confidence and daily usage.

## What this impacts
- Better D1/D7 retention potential.
- Lower support/confusion load from users.
- Lower cost/turn when unnecessary calls/messages are reduced.
- Cleaner architecture signal before moving from 5 users to 10-20 users.

## How it impacts users
- Users feel understood quickly.
- Users can complete onboarding in chunks (text/voice), not one rigid message.
- Users can ask onboarding control queries naturally (status/left/explain/edit/restart).
- Users get more useful direct answers for factual queries.

## How we are doing it (implementation style)
- Hybrid model:
  - LLM for understanding ambiguous user intent/language.
  - Deterministic logic for safety, schema integrity, math, quota, and guardrails.
- Intent-scoped execution:
  - Run only required agents for a given query path.
- Trace-driven validation:
  - Confirm behavior with `turn_id` logs, not assumptions.

## Efficiency and economics considerations
- Budget-aware operation (MVP constraint):
  - Minimize calls/turn.
  - Keep prompt context lean per intent.
  - Avoid unnecessary Twilio outbound messages.
- Onboarding turns are quota-free while incomplete (already implemented).
- Avoid broad architecture rewrite before reliability baseline is stable.

## Deterministic vs LLM direction
- We are not moving to deterministic-only.
- We are not moving to LLM-only.
- We are moving to:
  - LLM-first interpretation where language ambiguity exists.
  - Deterministic enforcement where correctness/safety must be guaranteed.

## Query handling principles (agreed)
- Answer the asked question directly first.
- Use user context/history where relevant.
- Motivation should be contextual, not forced every turn.
- During onboarding, support natural Hinglish/Hindi command variants.

## Why not full agentic loop right now
- Increases latency, token usage, and debugging complexity during pre-beta.
- Too many moving parts before architecture stress test phase.
- Better approach now: bounded, scoped improvements with clear stop conditions.

## Go/No-Go checks before adding 3 more users
- Meal recall reliability verified.
- Onboarding loop/collision issues resolved in real chat tests.
- Latency improved for common turns.
- No critical trace/path inconsistencies.
- Capability explanation message ready for new users.

## Immediate next actions
- Run smoke tests on logging + onboarding + factual recall.
- Record pass/fail in this folder.
- If pass, onboard 3 more users and monitor for 48 hours before new feature pushes.
