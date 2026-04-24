from __future__ import annotations

import asyncio
import json

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL

MAX_CAPABILITY_INTENT_RETRIES = 2


def should_check_capability_intent_with_llm(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    # Avoid extra hop for plain logs.
    pure_log_markers = (
        "i had ",
        "khaya",
        "ate ",
        "workout done",
        "run ",
        "walk ",
        "steps",
    )
    if any(m in text for m in pure_log_markers):
        return False
    return True


async def classify_capability_discovery_intent(user_message: str) -> tuple[bool, str]:
    if not GEMINI_API_KEY:
        return False, "no_api_key"

    system_prompt = (
        "You classify if user is asking app capabilities/features/help scope.\n"
        "Return ONLY JSON: {\"is_capability_query\":true|false,\"confidence\":\"high|medium|low\"}.\n"
        "Mark true for asks like: what can you do, can this app do X, feature list, help scope.\n"
        "Mark false for normal logging/coaching requests."
    )

    def _call_model(payload: dict[str, str]) -> tuple[bool, str]:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "application/json"},
        )
        raw = json.loads((response.text or "{}").strip())
        is_capability = bool(raw.get("is_capability_query"))
        confidence = str(raw.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid_confidence")
        return is_capability, confidence

    previous_error = ""
    for attempt in range(1, MAX_CAPABILITY_INTENT_RETRIES + 1):
        payload = {
            "user_message": user_message,
            "retry_context": (
                f"attempt={attempt}; previous_error={previous_error[:120]}" if attempt > 1 else ""
            ),
        }
        try:
            is_capability, confidence = await asyncio.to_thread(_call_model, payload)
            return is_capability and confidence != "low", confidence
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)
    return False, "failed"
