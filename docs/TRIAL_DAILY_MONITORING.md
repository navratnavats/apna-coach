# Trial Daily Monitoring (5 Users)

Use this checklist during the MVP rollout for:
- Day 1: You + sister
- Day 2/3: Gym friend + dad + mom

## Daily Goals

- Keep reply reliability high (no stuck turns).
- Keep user experience acceptable (clear ACK + eventual final reply).
- Catch safety/tone/data regressions early.
- Log issues and patch fast.

## Metrics To Track Daily

### 1) Reliability
- Total inbound turns
- Total final replies sent
- Success rate = replies sent / inbound turns
- Pipeline errors count
- Message send errors count
- Duplicate webhook skips count

Target:
- >95% turn completion
- 0 persistent queue stalls

### 2) Latency
- Median final response time
- P90 final response time
- Count of turns >60s
- Count of turns >90s
- Count of turns >150s

Target:
- Text-only mostly <45s
- Very few turns >150s

### 3) Policy Gate Quality
- allow / allow_constrained / deny counts
- False deny examples (normal fitness query denied)
- Unsafe misses (should have constrained but allowed)

Target:
- Deny only for valid out-of-scope/safety/security cases
- Near-zero false deny

### 4) Data Integrity
- Nutrition logs appended correctly (no duplicate placeholders)
- Activity burn and net metrics consistent
- Workout summary + volume trends append correctly
- Plan versions increment on create/edit
- Schema warnings count (unknown keys dropped)

Target:
- No profile overwrite/data loss incidents

### 5) Quota Gate
- Warning fired at 20 turns
- Hard wall fired after 25 turns
- Per-turn counting verified (burst text+image+voice in buffer = 1 turn)

Target:
- Exactly expected 20/25 behavior

### 6) Tone & UX
- Respectful language maintained
- No rude static fallback leaks
- Confusing responses reported by users

Target:
- Consistent respectful tone

## Day-Wise Rollout Plan

### Day 1 (You + Sister)
- Run stress tests intentionally:
  - burst input (text+image+voice under 10s)
  - plan create/edit/change signal + yes/no confirmation
  - historical query
  - metric explanation query
  - out-of-scope query
  - quota warning/wall path
- Collect top 5 breakpoints.

### Day 2/3 (Friend + Dad + Mom)
- Observe natural behavior:
  - Do they log meals/workouts without guidance?
  - Are delays acceptable to them?
  - Are replies understandable and useful?
  - Any repeated friction point?
- Patch only high-impact issues.

## Daily Incident Triage

Severity levels:
- `P0`: safety issue / data corruption / no reply
- `P1`: wrong routing / wrong metric explanation / severe latency
- `P2`: tone issue / formatting issue / minor extraction miss

Always record:
- user id/phone
- timestamp
- raw input
- expected vs actual behavior
- trace_id
- fix owner + ETA

## Daily Report Template

Copy/paste this at end of each day:

```
Date:
Users active:
Inbound turns:
Final replies sent:
Success rate:

Latency (median / p90):
>60s count:
>90s count:
>150s count:

Policy (allow / constrained / deny):
False denies:
Unsafe misses:

Data issues:
Quota issues:
Tone/UX issues:

Top 3 fixes for tomorrow:
1)
2)
3)
```

