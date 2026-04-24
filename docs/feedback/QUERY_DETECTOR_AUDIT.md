# Query Detector Audit (Services)

## Purpose
- Capture all query detection methods (especially `_is_*query`) in `app/services`.
- Map each to real-life user language.
- Document current behavior, risks, and recommended improvements.

---

## A) Direct `_is_*query` methods

### 1) `app/services/coach_reply.py` -> `_is_burn_or_deficit_query(user_message)`
- **What it detects now**
  - Burn/deficit metric ask via keywords: `calorie`, `burn`, `deficit`, `kcal`, etc.
  - Requires question context/hint (`how much`, `kitna`, `today`, `aaj`) or activity hint.
- **Real-life examples it should catch**
  - "Aaj kitna burn hua?"
  - "How much deficit today?"
  - "Mera calorie burn bata"
- **Risk**
  - Misses paraphrases/slang/typos.
  - Can conflict with router if used as a primary override.
- **Current role**
  - In coach path, currently used as fallback-style local detector.
- **Recommendation**
  - Keep as fallback only when router confidence is low/fallback.
  - Primary source should be router intent.

### 2) `app/services/coach_reply.py` -> `_is_metric_explanation_query(user_message)`
- **What it detects now**
  - Meaning/safety interpretation asks about `deficit`, `tdee`, `budget`, `target`.
- **Real-life examples**
  - "Deficit 1100 ka matlab kya?"
  - "Is this safe?"
  - "Net deficit ka meaning samjha"
- **Risk**
  - Keyword-only detection may miss natural phrasing.
  - Might trigger incorrectly on generic questions containing metric words.
- **Recommendation**
  - Use routed intent as primary.
  - Keep this function as safety fallback with strict low-confidence guard.

### 3) `app/services/coach_reply.py` -> `_is_food_recall_query(user_message)`
- **What it detects now**
  - Food recall asks: `what did i eat`, `kya khaya`, `breakfast me kya`, etc.
- **Real-life examples**
  - "Mujhe dikha maine kya khaya"
  - "Aaj breakfast me kya khaya tha?"
  - "Food log bata"
- **Risk**
  - Phrase coverage limited.
  - Currently behaves close to primary in coach path if triggered.
- **Recommendation**
  - Demote to fallback only.
  - Add router-level intent `food_recall_query` as primary route.

---

## B) Query-like classifiers and fallback detectors (not `_is_*` but same responsibility)

### 4) `app/services/intent_router.py` -> `_fallback_intent(user_message)`
- **What it does**
  - Deterministic keyword fallback intent when router LLM is unavailable/fails.
- **Real-life mapping**
  - Handles broad labels: plan create/edit/status, burn, nutrition/activity log, historical query.
- **Risk**
  - Over-broad keywords can misroute.
  - Good for resilience, bad as primary.
- **Recommendation**
  - Keep as last-resort fallback.
  - Never override high-confidence LLM route with this fallback.

### 5) `app/services/plan_orchestrator.py` -> `classify_plan_intent_fallback(user_message)`
- **What it does**
  - Keyword mapping specifically for plan intents.
- **Real-life mapping**
  - "12 week plan", "edit plan", "vacation", "tomorrow plan".
- **Risk**
  - Can force plan routing even when user intent is mixed/other.
- **Recommendation**
  - Only apply when router confidence is low/fallback.
  - Do not hard override strong router output.

### 6) `app/services/onboarding_policy.py` -> `evaluate_onboarding_message(...)`
- **What it does**
  - During onboarding, checks whether message likely matches pending fields.
- **Real-life mapping**
  - Allows details chunks for age/height/weight/target/core_why/injuries/equipment.
- **Risk**
  - Marker-based checks like `kg` can allow unrelated text.
  - Can reject meaningful answers if user language differs from marker list.
- **Recommendation**
  - Move toward parse-confidence + extraction success check.
  - Keep marker logic as backup only.

---

## C) Query-specific LLM detectors in coach flow

### 7) `app/services/coach_reply.py` -> `_detect_workout_intent(user_message)`
- **What it does**
  - LLM classifier returns `is_workout_request: true/false`.
- **Real-life mapping**
  - "Aaj workout de"
  - "Ran 5km, ab kya exercise karun?"
- **Risk**
  - Duplicate intent work if router already decided.
- **Recommendation**
  - Use only when router is low-confidence/ambiguous.
  - Otherwise trust routed intent.

### 8) `app/services/coach_reply.py` -> `_infer_training_environment_from_query(user_message)`
- **What it does**
  - LLM classifier maps environment: `home|gym|both|unknown`.
- **Real-life mapping**
  - "Ghar pe plan do", "gym workout de", "dono options de".
- **Risk**
  - Extra LLM call on workout path increases latency/cost.
- **Recommendation**
  - Trigger only when training environment missing and intent already workout.

---

## D) Real-world phrase coverage gaps to improve

- **Food recall**
  - "Mene kya kya khaya tha"
  - "Aaj ka khana dikha"
  - "Mera meal history bata"
- **Metric explain**
  - "Ye number scary lag raha"
  - "Deficit zyada to nahi?"
- **Burn query**
  - "Outflow kitna hua"
  - "Aaj calories kitni jali"
- **Plan change**
  - "Aaj break lena hai, kal continue"
  - "Travel pe hun to adjust kar"

---

## E) Current architecture interpretation

- System currently has a hybrid of:
  1. Router LLM intent,
  2. fallback keyword classifiers,
  3. coach-local `_is_*query` detectors.
- This gives resilience, but also creates coupling/conflict risk if local checks act as primary route overrides.

---

## F) Improvement policy (recommended for all detectors)

1. **Primary authority**
   - Router intent + confidence.
2. **Secondary authority**
   - Local detector only when confidence is low/fallback.
3. **Deterministic guarantees**
   - Once routed, data fetch/math/safety must be deterministic.
4. **Fallback rules**
   - If LLM invalid/unknown after retries, use safe deterministic fallback.
5. **Trace requirement**
   - Log whether route came from router-primary or detector-fallback.

---

## G) Actionable refactor checklist

- [ ] Add explicit `food_recall_query` as first-class router intent contract.
- [ ] Ensure `classify_plan_intent_fallback` does not override high-confidence router result.
- [ ] Add a confidence gate in coach before applying `_is_*query` fallback logic.
- [ ] Record `intent_source` in trace (`router_primary` vs `local_fallback`).
- [ ] Expand prompt examples for Hinglish paraphrases and typo-heavy inputs.
- [ ] Keep deterministic output formatting for factual responses (burn/recall/metrics).

