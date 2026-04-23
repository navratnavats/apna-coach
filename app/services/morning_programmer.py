from __future__ import annotations

import asyncio
import json
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL


def _available_equipment(living_profile: dict[str, Any]) -> list[str]:
    lifestyle = living_profile.get("lifestyle") or {}
    raw = lifestyle.get("available_equipment") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip().lower() for item in raw if str(item).strip()]


async def generate_morning_workout_nudge(living_profile: dict[str, Any]) -> str:
    """
    Morning proactive Workout Programmer specialist output.
    """
    equipment = _available_equipment(living_profile)
    if not equipment:
        return (
            "Subah ho gayi Bhai! Plan banane ko ready hoon, but pehle bata tu kis "
            "setup pe hai - gym access hai ya ghar pe dumbbells/bands/pull-up bar?"
        )

    if not GEMINI_API_KEY:
        return (
            "Subah ho gayi Bhai! Aaj 15-min quick hit: 1) Goblet Squat 3x12, "
            "2) Dumbbell Row 3x12/side, 3) Band Face Pull 3x15. Dhyaan se form pe focus kar."
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
        "- Keep message concise, WhatsApp-friendly, Bhai tone.\n"
        "- Plain text only."
    )

    def _call_model() -> str:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps({"living_profile": living_profile}, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        return (response.text or "").strip()

    try:
        text = await asyncio.to_thread(_call_model)
    except Exception as exc:  # noqa: BLE001
        print(f"[Morning Programmer] LLM generation failed: {exc}")
        return (
            "Subah ho gayi Bhai! Aaj 15-min quick hit: 1) DB Romanian Deadlift 3x10, "
            "2) Resistance Band Row 3x12, 3) Glute Bridge 3x15. Injury-safe pace me kar."
        )

    return text or (
        "Subah ho gayi Bhai! Aaj 15-min plan ready: 3 exercises, controlled reps, "
        "aur form pe full focus."
    )

