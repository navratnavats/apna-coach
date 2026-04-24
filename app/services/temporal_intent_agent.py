from __future__ import annotations

import asyncio
import json
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL

MAX_TEMPORAL_RETRIES = 3


def should_invoke_temporal_agent(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "morning",
        "evening",
        "night",
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "subah",
        "shaam",
        "raat",
        "kal",
        "yesterday",
        "today",
        "am",
        "pm",
        ":",
    )
    return any(m in lowered for m in markers)


async def parse_temporal_intent(
    *,
    user_text: str,
    timezone_name: str,
) -> dict[str, Any]:
    if not GEMINI_API_KEY or not should_invoke_temporal_agent(user_text):
        return {}

    system_prompt = (
        "You are Temporal Intent Parser for fitness logs.\n"
        "Return ONLY JSON with keys:\n"
        "{"
        "\"time_ref_type\":\"explicit_clock|relative_phrase|none\","
        "\"clock_time_local\":\"HH:MM or empty\","
        "\"relative_ref\":\"today|yesterday|today_morning|today_evening|today_night|yesterday_morning|yesterday_evening|yesterday_night|none\","
        "\"slot_hint\":\"breakfast|morning_snack|lunch|evening_snack|dinner|other|morning_session|afternoon_session|evening_session|other_session|none\","
        "\"confidence\":\"high|medium|low\""
        "}\n"
        "Rules:\n"
        "- Infer only from user_text and timezone.\n"
        "- If uncertain, use none + low.\n"
        "- No markdown, no extra text."
    )

    allowed_time_ref = {"explicit_clock", "relative_phrase", "none"}
    allowed_slot = {
        "breakfast",
        "morning_snack",
        "lunch",
        "evening_snack",
        "dinner",
        "other",
        "morning_session",
        "afternoon_session",
        "evening_session",
        "other_session",
        "none",
    }
    allowed_rel = {
        "today",
        "yesterday",
        "today_morning",
        "today_evening",
        "today_night",
        "yesterday_morning",
        "yesterday_evening",
        "yesterday_night",
        "none",
    }
    allowed_conf = {"high", "medium", "low"}

    def _call_model(payload: dict[str, Any]) -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "application/json"},
        )
        raw = json.loads((response.text or "{}").strip())
        if not isinstance(raw, dict):
            raise ValueError("invalid_json_object")
        time_ref = str(raw.get("time_ref_type") or "").strip().lower()
        if time_ref not in allowed_time_ref:
            raise ValueError("invalid_time_ref")
        slot = str(raw.get("slot_hint") or "none").strip().lower()
        if slot not in allowed_slot:
            raise ValueError("invalid_slot_hint")
        relative_ref = str(raw.get("relative_ref") or "none").strip().lower()
        if relative_ref not in allowed_rel:
            raise ValueError("invalid_relative_ref")
        conf = str(raw.get("confidence") or "low").strip().lower()
        if conf not in allowed_conf:
            raise ValueError("invalid_confidence")
        clock_time = str(raw.get("clock_time_local") or "").strip()
        return {
            "time_ref_type": time_ref,
            "clock_time_local": clock_time,
            "relative_ref": relative_ref,
            "slot_hint": slot,
            "confidence": conf,
        }

    previous_error = ""
    previous_output: dict[str, Any] = {}
    for attempt in range(1, MAX_TEMPORAL_RETRIES + 1):
        payload = {
            "user_text": user_text,
            "timezone": timezone_name,
            "retry_context": (
                {
                    "attempt": attempt,
                    "previous_failure_reason": previous_error,
                    "previous_output": previous_output,
                }
                if attempt > 1
                else {}
            ),
        }
        try:
            result = await asyncio.to_thread(_call_model, payload)
            if result == previous_output and attempt > 1:
                raise ValueError("repeated_same_output")
            return result
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)
            previous_output = previous_output or {}
    return {}
