# Internal Points From User Feedback

## 1) Meal recall consistency and day-part structure
- User reported a mismatch: meal was logged, but later recall query answered as if logging was unavailable.
- Expectation: food logs should be queryable naturally (for same day and historical days).
- Discussion point: keep raw `nutrition_log` but also maintain a structured "day parts" view (breakfast/lunch/dinner/snacks), including backfilled time-of-day tags when user mentions "breakfast" at a later clock time.
- Discussion point: summary/history fetch should prefer structured day-part output for better user trust.

## 2) Intent understanding quality vs deterministic gating
- Feedback indicates replies sometimes feel too deterministic or off-intent.
- Expectation: user question should be answered directly before motivational framing.
- Discussion point: maintain hybrid architecture, but increase LLM interpretation where language is ambiguous, and reduce brittle keyword behavior for conversational turns.
- Discussion point: deterministic logic remains for safety/math/enforcement only.

## 3) Latency and call-chain heaviness
- User perceived delay as a major product risk.
- Discussion point: identify heavy segments per intent (LLM hops, DB round-trips), and avoid running non-essential agents on simple turns.
- Discussion point: cache only where correctness is not compromised; define invalidation rules carefully before introducing broad caching.
- Discussion point: response-time target should improve without sacrificing safety and traceability.

## 4) Response variation and static feel
- User noted repeated/static wording in core responses.
- Discussion point: preserve central message ownership, but diversify phrasing style and response templates to reduce repetitiveness.
- Discussion point: do not allow style variation to override direct factual answering requirements.

## 5) Onboarding command collisions (copy-paste edge case)
- Copy-pasting onboarding template can accidentally trigger `edit/restart` because those words exist in the prompt itself.
- Discussion point: onboarding control detection should depend on clear command intent, not naive keyword presence inside long payloads.
- Discussion point: prefer intent-aware control handling and command-position checks to avoid false triggers.

## 6) Onboarding UX expectations
- Users expect:
  - chunk-friendly multi-turn onboarding,
  - visibility of what is pending,
  - ability to ask "what does this step mean",
  - ability to edit/restart intentionally.
- Discussion point: onboarding should be self-discoverable (no hidden commands) and explain critical fields (`core_why`, injuries, equipment) clearly, including voice-note support.

## 7) Parallel-user confidence
- Product should support multiple users concurrently without cross-user data collisions.
- Discussion point: current per-phone batching + per-phone queue/lock model is aligned for single-instance MVP usage.
- Discussion point: if multi-instance scale is introduced later, shared coordination/locking strategy must be revisited.

## 8) Coupling perception in multi-agent flow
- User perception: too many agents may be firing too often.
- Discussion point: move toward intent-scoped agent execution (minimal always-on core + conditional specialist bundles).
- Discussion point: keep full observability to verify actual fired-agent paths per `turn_id`.

## 9) MVP scope posture
- Feedback supports a practical MVP posture:
  - prioritize reliability, directness, and latency over complex autonomous loops.
  - keep strict bounds if any loop-style behavior is introduced later.
