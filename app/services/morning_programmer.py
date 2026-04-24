from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.critic_agent import run_critic_agent
from app.services.messages import (
    morning_default_plan,
    morning_missing_equipment,
    morning_quick_hit_llm_error,
    morning_quick_hit_no_llm,
)
from app.services.medical_safety_officer import run_medical_safety_officer
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address


def _available_equipment(living_profile: dict[str, Any]) -> list[str]:
    lifestyle = living_profile.get("lifestyle") or {}
    raw = lifestyle.get("available_equipment") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip().lower() for item in raw if str(item).strip()]


async def generate_morning_workout_nudge(
    living_profile: dict[str, Any], trace_id: str | None = None
) -> str:
    """
    Morning proactive Workout Programmer specialist output.
    """
    equipment = _available_equipment(living_profile)
    address = resolve_user_address(living_profile)
    log_agent_event(
        agent="workout_programmer",
        stage="morning_start",
        trace_id=trace_id,
        details={"equipment_count": len(equipment)},
    )
    if not equipment:
        return await run_critic_agent(
            morning_missing_equipment(address),
            source="morning_nudge",
            living_profile=living_profile,
            trace_id=trace_id,
        )

    if not GEMINI_API_KEY:
        return await run_critic_agent(
            morning_quick_hit_no_llm(address),
            source="morning_nudge",
            living_profile=living_profile,
            trace_id=trace_id,
        )

    system_prompt = (
        "You are Workout_Programmer for Apna Coach. Build a proactive morning workout "
        "nudge in conversational Hinglish.\n"
        "Rules:\n"
        "- Use living_profile as source of truth.\n"
        "- Must account for lifestyle.available_equipment, physiology.injuries, and psychology.core_why.\n"
        "- Output exactly a 15-minute plan with 3 specific exercises + sets/reps.\n"
        "- Mention target body part for today.\n"
        "- Include one short injury-safe caution if needed.\n"
        f"- Keep message concise, WhatsApp-friendly, and use address token '{address}'.\n"
        "- Plain text only."
    )

    def _call_model() -> str:
        started_at = time.perf_counter()
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps({"living_profile": living_profile}, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="workout_programmer",
            stage="generate_morning_nudge",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload={"living_profile": living_profile},
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        return (response.text or "").strip()

    try:
        text = await asyncio.to_thread(_call_model)
    except Exception as exc:  # noqa: BLE001
        print(f"[Morning Programmer] LLM generation failed: {exc}")
        text = morning_quick_hit_llm_error(address)

    final_text = text or morning_default_plan(address)
    safety_reviewed = await run_medical_safety_officer(
        final_text,
        living_profile,
        source="morning_nudge",
        trace_id=trace_id,
    )
    final = await run_critic_agent(
        safety_reviewed,
        source="morning_nudge",
        living_profile=living_profile,
        trace_id=trace_id,
    )
    log_agent_event(
        agent="workout_programmer",
        stage="morning_complete",
        trace_id=trace_id,
        details={"chars": len(final)},
    )
    return final

