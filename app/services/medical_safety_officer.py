from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL


def _extract_injuries(living_profile: dict[str, Any]) -> list[dict[str, Any]]:
    physiology = living_profile.get("physiology") or {}
    raw_injuries = physiology.get("injuries") or []
    if not isinstance(raw_injuries, list):
        return []
    injuries: list[dict[str, Any]] = []
    for injury in raw_injuries:
        if isinstance(injury, dict):
            injuries.append(injury)
    return injuries


def _has_mobility_risk(injuries: list[dict[str, Any]]) -> bool:
    risk_terms = (
        "knee",
        "ankylosing",
        "spondyl",
        "back",
        "spine",
        "hip",
        "joint",
    )
    for injury in injuries:
        blob = " ".join(
            [
                str(injury.get("part") or ""),
                str(injury.get("history") or ""),
                str(injury.get("severity") or ""),
            ]
        ).lower()
        if any(term in blob for term in risk_terms):
            return True
    return False


def _rule_based_rewrite(workout_text: str, injuries: list[dict[str, Any]]) -> str:
    if not workout_text.strip():
        return workout_text
    if not _has_mobility_risk(injuries):
        return workout_text

    replacements = [
        (r"\bbox jumps?\b", "glute bridges"),
        (r"\bjump squats?\b", "tempo bodyweight squats"),
        (r"\bburpees?\b", "incline push-up + step-back combo"),
        (r"\bheavy deadlifts?\b", "Romanian deadlift with light load and slow tempo"),
        (r"\bdeadlifts?\b", "hip hinge drill with light dumbbells"),
        (r"\bsprints?\b", "brisk incline walk"),
    ]

    safe_text = workout_text
    swap_count = 0
    for pattern, substitute in replacements:
        updated = re.sub(pattern, substitute, safe_text, flags=re.IGNORECASE)
        if updated != safe_text:
            swap_count += 1
            safe_text = updated

    if swap_count == 0:
        return safe_text

    return (
        "Bhai, safety check done. Maine kuch high-impact moves swap kiye to protect "
        "your lower back/knees based on your injury profile.\n\n"
        f"{safe_text}"
    )


async def run_medical_safety_officer(
    workout_text: str,
    living_profile: dict[str, Any],
    *,
    source: str = "coach_workout",
) -> str:
    """
    Agent 5 (Medical Safety Officer):
    Intercepts workout output and rewrites unsafe movements before user delivery.
    """
    injuries = _extract_injuries(living_profile)
    if not injuries:
        return workout_text

    if not GEMINI_API_KEY:
        return _rule_based_rewrite(workout_text, injuries)

    system_prompt = (
        "You are Medical_Safety_Officer for Apna Coach. Your role is purely analytical "
        "and protective.\n"
        "You receive a draft workout and a user's injury profile.\n"
        "Rules:\n"
        "- If workout is safe for these injuries, return the original workout unchanged.\n"
        "- If any movement is risky, rewrite only the risky parts with low-impact alternatives.\n"
        "- Keep the same concise WhatsApp format and Bhai tone.\n"
        "- If you swap something, explicitly mention why (injury protection).\n"
        "- Do not add markdown.\n"
        "- Output plain text only."
    )

    model_input = {
        "source": source,
        "injuries": injuries,
        "draft_workout": workout_text,
    }

    def _call_model() -> str:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(model_input, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        return (response.text or "").strip()

    try:
        reviewed = await asyncio.to_thread(_call_model)
        if reviewed:
            return reviewed
    except Exception as exc:  # noqa: BLE001
        print(f"[Medical Safety Officer] LLM review failed: {exc}")

    return _rule_based_rewrite(workout_text, injuries)
