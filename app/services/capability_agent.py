from __future__ import annotations

import asyncio
import json
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.persona import resolve_user_address

MAX_CAPABILITY_RETRIES = 2

WHAT_WE_DO = [
    "Food logging from text/voice/image with estimated calories/macros",
    "Activity/workout logging with estimated calorie burn",
    "Daily deficit and metric explanations with factual numbers",
    "Workout guidance personalized by equipment, injuries, and environment",
    "Plan creation/edit/status support with continuity",
    "Historical recall for past logged days",
]

KNOWN_LIMITS = [
    "Not a medical diagnosis service",
    "Calorie/macros are estimates, not lab-grade exact values",
    "Needs consistent user logs for best recall quality",
]


async def generate_capability_pitch(
    *,
    user_message: str,
    living_profile: dict[str, Any],
    trace_id: str | None = None,
) -> str:
    address = resolve_user_address(living_profile)
    if not GEMINI_API_KEY:
        return (
            f"{address}, Apna Coach currently yeh reliably karta hai:\n"
            + "\n".join([f"- {x}" for x in WHAT_WE_DO])
            + "\n\nBrutal truth / limits:\n"
            + "\n".join([f"- {x}" for x in KNOWN_LIMITS])
        )

    system_prompt = (
        "You are Capability Agent for Apna Coach.\n"
        "Goal: explain product capabilities honestly, no hype.\n"
        "Return plain text only.\n"
        "Rules:\n"
        "- Mention only capabilities from allowed_capabilities.\n"
        "- Add explicit limits from known_limits.\n"
        "- Keep concise, WhatsApp-friendly, scannable bullets.\n"
        "- No fake promises, no roadmap speculation."
    )

    def _call_model(payload: dict[str, Any]) -> str:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        text = str(response.text or "").strip()
        if len(text) < 40:
            raise ValueError("capability_reply_too_short")
        return text

    previous_error = ""
    for attempt in range(1, MAX_CAPABILITY_RETRIES + 1):
        payload = {
            "user_message": user_message,
            "address": address,
            "allowed_capabilities": WHAT_WE_DO,
            "known_limits": KNOWN_LIMITS,
            "format_hint": {
                "sections": ["What I can do", "Limits", "How to ask me"],
            },
            "retry_context": (
                {"attempt": attempt, "previous_failure_reason": previous_error}
                if attempt > 1
                else {}
            ),
        }
        try:
            out = await asyncio.to_thread(_call_model, payload)
            log_agent_event(
                agent="capability_agent",
                stage="complete",
                trace_id=trace_id,
                details={"attempt": attempt, "chars": len(out)},
            )
            return out
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)

    fallback = (
        f"{address}, Apna Coach yeh reliably karta hai:\n"
        + "\n".join([f"- {x}" for x in WHAT_WE_DO])
        + "\n\nLimits (honest):\n"
        + "\n".join([f"- {x}" for x in KNOWN_LIMITS])
        + "\n\nHow to use fast:\n- 'Aaj kya khaya: ...'\n- 'Aaj workout: ...'\n- 'Aaj deficit kitna?'\n- 'Kal ke liye plan do'"
    )
    log_agent_event(
        agent="capability_agent",
        stage="fallback",
        trace_id=trace_id,
        details={"reason": previous_error[:160]},
    )
    return fallback
