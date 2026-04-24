from __future__ import annotations

from typing import Any

ALLOWED_ROUTER_INTENTS = {
    "burn_query",
    "metric_explanation_query",
    "food_recall_query",
    "workout_request",
    "plan_create_request",
    "plan_status_query",
    "plan_edit_request",
    "plan_change_signal",
    "nutrition_log",
    "activity_log",
    "historical_query",
    "profile_update",
    "general_chat",
}

ALLOWED_ROUTER_CONFIDENCE = {
    "high",
    "medium",
    "low",
    "fallback",
    "fast_lane",
}

LOW_CONFIDENCE_VALUES = {"low", "fallback"}
MEMORY_EXTRACT_SKIP_INTENTS = {
    "burn_query",
    "metric_explanation_query",
    "food_recall_query",
    "historical_query",
    "plan_status_query",
}


def classify_heuristic_intent(user_message: str) -> str:
    text = str(user_message or "").strip().lower()
    if not text:
        return "general_chat"
    if any(
        k in text
        for k in (
            "kya matlab",
            "what does",
            "mean",
            "matlab",
            "safe hai",
            "is this safe",
            "net deficit",
            "deficit 1135",
        )
    ):
        return "metric_explanation_query"
    if any(
        k in text
        for k in (
            "what did i eat",
            "what i ate",
            "kya khaya",
            "aaj kya khaya",
            "breakfast me kya",
            "lunch me kya",
            "dinner me kya",
            "meal log",
            "food log",
        )
    ):
        return "food_recall_query"
    if any(
        k in text
        for k in ("on tuesday", "history", "kal kya khaya", "pichle", "last week", "yesterday")
    ):
        return "historical_query"
    if any(k in text for k in ("12 week", "8 week", "diet plan", "meal plan", "tomorrow plan")):
        return "plan_create_request"
    if any(k in text for k in ("edit plan", "modify plan", "adjust plan", "change plan")):
        return "plan_edit_request"
    if any(k in text for k in ("vacation", "travel", "trip", "missed today", "skip today")):
        return "plan_change_signal"
    if any(k in text for k in ("next week plan", "this week plan", "what next")):
        return "plan_status_query"
    if any(k in text for k in ("burn", "burnt", "deficit", "kcal", "calorie")):
        return "burn_query"
    if any(k in text for k in ("workout", "exercise", "training", "plan")):
        return "workout_request"
    if any(k in text for k in ("ate", "khaya", "meal", "dinner", "lunch", "breakfast")):
        return "nutrition_log"
    if any(k in text for k in ("ran", "run", "steps", "walked", "swim", "cycling", "gym", "badminton")):
        return "activity_log"
    return "general_chat"


def normalize_router_result(raw: dict[str, Any] | None) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    intent = str(data.get("primary_intent") or "general_chat").strip().lower()
    if intent not in ALLOWED_ROUTER_INTENTS:
        intent = "general_chat"
    confidence = str(data.get("confidence") or "low").strip().lower()
    if confidence not in ALLOWED_ROUTER_CONFIDENCE:
        confidence = "low"
    return {"primary_intent": intent, "confidence": confidence}


def should_allow_plan_fallback(confidence: str) -> bool:
    return str(confidence or "").strip().lower() in LOW_CONFIDENCE_VALUES


def should_allow_detector_fallback(confidence: str) -> bool:
    return str(confidence or "").strip().lower() in LOW_CONFIDENCE_VALUES


def should_run_memory_extraction(
    *,
    routed_intent: str,
    routed_confidence: str,
    has_audio_media: bool,
    onboarding_fast_lane: bool,
) -> bool:
    if onboarding_fast_lane:
        return True
    if has_audio_media:
        return True
    intent = str(routed_intent or "").strip().lower()
    confidence = str(routed_confidence or "").strip().lower()
    if confidence in {"high", "medium"} and intent in MEMORY_EXTRACT_SKIP_INTENTS:
        return False
    return True
