from __future__ import annotations

import asyncio
import json
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event

ALLOWED_INTENTS = {
    "burn_query",
    "workout_request",
    "nutrition_log",
    "activity_log",
    "profile_update",
    "general_chat",
}


def _fallback_intent(user_message: str) -> str:
    text = str(user_message or "").lower()
    if any(k in text for k in ("burn", "burnt", "deficit", "kcal", "calorie")):
        return "burn_query"
    if any(k in text for k in ("workout", "exercise", "training", "plan")):
        return "workout_request"
    if any(k in text for k in ("ate", "khaya", "meal", "dinner", "lunch", "breakfast")):
        return "nutrition_log"
    if any(
        k in text
        for k in ("ran", "run", "steps", "walked", "swim", "cycling", "gym", "badminton")
    ):
        return "activity_log"
    return "general_chat"


async def classify_router_intent(
    user_message: str, *, trace_id: str | None = None
) -> dict[str, Any]:
    """
    Agent 2 (Router): classify top-level intent before heavy agent path.
    """
    log_agent_event(
        agent="router",
        stage="start",
        trace_id=trace_id,
        details={"message_chars": len(user_message or "")},
    )
    if not GEMINI_API_KEY:
        intent = _fallback_intent(user_message)
        result = {"primary_intent": intent, "confidence": "fallback"}
        log_agent_event(
            agent="router",
            stage="complete",
            status="fallback",
            trace_id=trace_id,
            details=result,
        )
        return result

    system_prompt = (
        "You are Agent 2 Router for Apna Coach. Classify the message intent.\n"
        "Return ONLY JSON: {\"primary_intent\":\"...\",\"confidence\":\"high|medium|low\"}.\n"
        "Allowed primary_intent values exactly: "
        "burn_query, workout_request, nutrition_log, activity_log, profile_update, general_chat.\n"
        "Rules:\n"
        "- burn_query: asking burned calories/deficit/intake metrics.\n"
        "- workout_request: asks for workout plan/exercise prescription.\n"
        "- nutrition_log: user logs food intake.\n"
        "- activity_log: user logs physical activity done.\n"
        "- profile_update: user updates age/height/gender/weight/equipment/etc.\n"
        "- general_chat: everything else."
    )

    def _call_model() -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_message,
            generation_config={"response_mime_type": "application/json"},
        )
        raw = json.loads((response.text or "{}").strip())
        intent = str(raw.get("primary_intent") or "").strip()
        if intent not in ALLOWED_INTENTS:
            intent = _fallback_intent(user_message)
        confidence = str(raw.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        return {"primary_intent": intent, "confidence": confidence}

    try:
        result = await asyncio.to_thread(_call_model)
        log_agent_event(
            agent="router",
            stage="complete",
            trace_id=trace_id,
            details=result,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        intent = _fallback_intent(user_message)
        result = {"primary_intent": intent, "confidence": "fallback"}
        log_agent_event(
            agent="router",
            stage="complete",
            status="llm_failed_fallback",
            trace_id=trace_id,
            details={"error": str(exc), **result},
        )
        return result
