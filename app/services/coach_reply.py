from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL


def _get_available_equipment(living_profile: dict[str, Any]) -> list[str]:
    lifestyle = living_profile.get("lifestyle") or {}
    raw = lifestyle.get("available_equipment") or []
    if not isinstance(raw, list):
        return []
    equipment = []
    for item in raw:
        normalized = str(item).strip().lower()
        if normalized:
            equipment.append(normalized)
    return equipment


async def _detect_workout_intent(user_message: str) -> bool:
    """
    AI intent detector to avoid brittle keyword-only routing.
    """
    if not GEMINI_API_KEY:
        return False

    system_prompt = (
        "Classify if the user's message is asking for workout/training plan or "
        "exercise advice. Return ONLY JSON: {\"is_workout_request\": true/false}."
    )

    def _call_model() -> bool:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_message,
            generation_config={"response_mime_type": "application/json"},
        )
        parsed = json.loads((response.text or "{}").strip())
        return bool(parsed.get("is_workout_request", False))

    try:
        return await asyncio.to_thread(_call_model)
    except Exception:  # noqa: BLE001
        return False


async def _generate_workout_program(
    user_message: str, living_profile: dict[str, Any]
) -> str:
    """
    Specialist Workout Programmer agent (Hybrid Training).
    """
    system_prompt = (
        "You are Workout_Programmer for Apna Coach. You are an expert in Hybrid "
        "Training (visible muscle + long-distance running). Use the provided "
        "living_profile JSON as source of truth.\n\n"
        "Rules:\n"
        "- Generate a specific 'Quick Hit' workout for today with exactly 3 exercises.\n"
        "- Use only lifestyle.available_equipment and training environment.\n"
        "- Respect all injuries/medical flags from physiology.\n"
        "- If injuries increase risk, include a brief safety disclaimer and choose "
        "low-impact alternatives.\n"
        "- Keep output concise and WhatsApp-friendly in conversational Hinglish.\n"
        "- End with one guiding check-in question.\n"
        "- Output plain text only."
    )

    def _call_model() -> str:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(
                {"living_profile": living_profile, "user_message": user_message},
                ensure_ascii=False,
            ),
            generation_config={"response_mime_type": "text/plain"},
        )
        return (response.text or "").strip()

    return await asyncio.to_thread(_call_model)


def _should_add_motivation_reminder(living_profile: dict[str, Any]) -> bool:
    logs = living_profile.get("logs") or {}
    coach_message_count = logs.get("coach_message_count", 0)
    try:
        return int(coach_message_count) % 3 == 0 and int(coach_message_count) > 0
    except (TypeError, ValueError):
        return False


def _motivation_anchor(living_profile: dict[str, Any]) -> str:
    psychology = living_profile.get("psychology") or {}
    core_why = str(psychology.get("core_why") or "").strip()
    if core_why:
        return core_why

    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    target = biometrics.get("target")
    if target not in (None, "", 0, 0.0):
        return f"your target weight ({target} kg)"

    return "your fitness goal"


def _sanitize_coach_reply(reply_text: str) -> str:
    # Keep Twilio XML-safe output.
    return (
        reply_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .strip()
    )


async def generate_coach_reply(
    user_message: str,
    living_profile: dict[str, Any],
    session_context: dict[str, Any] | None = None,
) -> str:
    """
    Brain B (Coach):
    Generate an empathetic, concise coaching reply using fresh profile context.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing for coach reply generation.")

    is_workout_request = await _detect_workout_intent(user_message)
    equipment_list = _get_available_equipment(living_profile)

    # Gatekeeper logic: collect equipment before workout programming.
    if is_workout_request and len(equipment_list) == 0:
        return (
            "Bhai, main solid plan dene ke liye ready hoon, but mujhe pata hi nahi "
            "tu kis setup pe train karta hai. Gym access hai, ya ghar pe dumbbells, "
            "kettlebell, bands, pull-up bar, ya yoga mat hai? Dhyaan se bata."
        )

    # Specialist handoff: for workout requests with equipment available,
    # route to Workout Programmer agent prompt.
    if is_workout_request:
        workout_text = await _generate_workout_program(user_message, living_profile)
        if workout_text.strip():
            return _sanitize_coach_reply(workout_text)

    add_reminder = _should_add_motivation_reminder(living_profile)
    reminder_anchor = _motivation_anchor(living_profile)

    additional_rules = []
    additional_rules.append(
        "SAFETY: Always inspect physiology.injuries and medical flags from living_profile. "
        "If user asks for workout/training and there is any relevant injury risk, warn "
        "clearly, avoid harmful high-impact/loading suggestions, give safer alternatives, "
        "and ask one safety check question (e.g., pain level or trigger movement)."
    )
    if add_reminder:
        additional_rules.append(
            "MOTIVATION: This is every 3rd coach reply. Add one short motivational line "
            f"linked to {reminder_anchor} and consistency."
        )

    system_prompt = (
        "You are Apna Coach, an empathetic, firm, and knowledgeable fitness brother. "
        "You speak in conversational Hinglish (or the user's preferred language). "
        "Use natural phrases like 'tension mat le', 'focus kar', and 'dhyaan se' when appropriate. "
        "Keep messages concise for WhatsApp. Always read the provided living_profile "
        "JSON context before answering. Reference their goals, respect their injuries, "
        "and ask one guiding question at the end to keep them engaged. Do not output "
        "markdown, just clean text.\n\n"
        "If session_context.nutrition_logged_this_turn is true, acknowledge that food "
        "has been logged before giving coaching advice.\n"
        "If session_context.voice_note_logged_this_turn is true, briefly acknowledge "
        "that you processed their voice note before coaching response.\n"
        + "\n".join(additional_rules)
    )

    model_input = {
        "living_profile": living_profile,
        "user_message": user_message,
        "session_context": session_context or {},
    }

    def _call_model() -> str:
        started_at = time.perf_counter()
        print(f"[Coach] Calling Gemini model: {GEMINI_COACH_MODEL}")
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(model_input, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        reply_text = (response.text or "").strip()
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[Coach] Response received in {elapsed_ms} ms (chars={len(reply_text)})")
        return _sanitize_coach_reply(reply_text)

    return await asyncio.to_thread(_call_model)

