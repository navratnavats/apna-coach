# Post-MVP Hardening Backlog

This file tracks high-value hardening tasks that can be taken after MVP stabilization.

## Interpretation-to-Action Risk Areas (do later)

- `webhooks` nutrition path: add stronger contract gates before appending `logs.nutrition_log`.
- `webhooks` hydration path: gate `logs.current_day.water_liters_delta` with stricter evidence contract.
- `webhooks` workout completion: require stronger evidence contract for `workout_complete=true`.
- `intent_router`: reduce fallback dependence (`classify_heuristic_intent`) once reliability is proven.
- `coach_reply`: reduce fallback detector usage when router confidence is low/fallback.
- `onboarding_policy`: keep strict fallback only, monitor and reduce deterministic branch usage over time.
- `policy_agent`: harden fallback behavior and add explicit contract confidence thresholds.
- `psychology_agent`: migrate keyword fallback path to `run_json_contract` + safe fallback schema.
- `plan_agent`: migrate fallback plan text path to stricter contract outputs.
- `critic_agent`: move plain-text generation to strict text-contract helper.
- `capability_agent`: move plain-text generation to strict text-contract helper.

## Observability Improvements (do later)

- Add `activity_intent`, `safe_to_burn`, and guard reason dashboards in trace analytics.
- Add counters for fallback usage per agent (router, policy, onboarding, coach).
- Add contract violation metrics (invalid enum, missing required action fields).

## Recommendation

- **Do now (before broader user rollout):**
  - Keep the new activity burn guard live.
  - Add basic monitoring alerts for `activity_burn_skipped` spikes.
- **Do after MVP:**
  - Contract-hardening pass for text generators and remaining fallback-heavy agents.
  - Deep cleanup of residual heuristic branches once real traffic confirms stability.

