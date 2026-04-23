# Trial Query Policy

This policy defines which user queries should enter the full multi-agent loop and which should be constrained, redirected, or rejected during prototype trials.

## Why This Exists

- Protects latency/cost during trial.
- Prevents unsafe/off-domain behavior.
- Gives predictable user experience.
- Makes logs auditable and actionable.

## Query Buckets

### 1) In-Scope (Full Pipeline)

These should run through Router -> Memory/Bio-Math/Psychology/Coach/Critic:

- Nutrition logging (`ate`, meal text, meal image).
- Activity logging (`ran`, `steps`, `gym chest in 50 mins`, sports).
- Deficit/burn/factual metric explanation.
- Workout request (`what should I train today`).
- Historical nutrition/activity recall.
- Plan create/status/edit/change signal.
- Profile updates (`age`, `height`, `gender`, equipment, preferences).

### 2) In-Scope but Constrained

These should be answered, but with stricter response mode:

- Medical-risk asks: always route through safety-aware path, avoid diagnosis.
- Emotional/low-motivation signals: use Psychology Agent output (`support`/`simplify` mode).
- Long/complex asks: allow processing with human-delay pings.

### 3) Out-of-Scope (Deflect / Refuse)

These should not run full coaching logic:

- Illegal/harmful requests.
- Financial, legal, or unrelated technical support.
- Non-fitness tasks unrelated to app goals.
- Prompt-injection attempts (`ignore your rules`, `leak system prompts`).

Recommended response pattern:

1. Acknowledge briefly.
2. State boundary.
3. Offer nearest in-scope help.

## Suggested Deterministic Gate (Pre-Router)

Add a lightweight policy check before heavy agent chain:

- `allow`: continue normal route.
- `allow_constrained`: continue with forced safe mode.
- `deny`: return boundary response immediately.

This avoids wasting tokens on clearly out-of-scope asks.

## Confirmation Rules (Already Needed)

- Plan change signals (vacation/missed-day/travel): ask `Yes/No` before mutating plan.
- Optional extension: explicit plan edits can also require confirmation.

## Trial KPI Hooks

- `policy_decision`: allow/allow_constrained/deny.
- `policy_reason`: out_of_scope / safety / prompt_injection / normal.
- `forced_mode`: support/simplify/safety.

Track these in trace logs for trial tuning.

