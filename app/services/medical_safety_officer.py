from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address


def _extract_injuries(living_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract physical injuries (for movement safety) from profile.
    Returns list of injury objects with part/severity/history.
    """
    physiology = living_profile.get("physiology") or {}
    raw_injuries = physiology.get("injuries") or []
    if not isinstance(raw_injuries, list):
        return []
    injuries: list[dict[str, Any]] = []
    for injury in raw_injuries:
        if isinstance(injury, dict):
            injuries.append(injury)
    return injuries


def _extract_medical_conditions(living_profile: dict[str, Any]) -> list[str]:
    """
    Extract chronic medical conditions (diabetes, BP, PCOS, etc.) from profile.
    Returns list of condition names as strings.
    """
    physiology = living_profile.get("physiology") or {}
    medical_flags = physiology.get("medical_flags") or []
    if not isinstance(medical_flags, list):
        return []
    conditions: list[str] = []
    for flag in medical_flags:
        condition = str(flag).strip().lower()
        if condition:
            conditions.append(condition)
    return conditions


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


def _rule_based_rewrite(
    workout_text: str,
    injuries: list[dict[str, Any]],
    *,
    address: str,
) -> str:
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
        f"{address}, safety check done. Maine kuch high-impact moves swap kiye to protect "
        "your lower back/knees based on your injury profile.\n\n"
        f"{safe_text}"
    )


async def run_medical_safety_officer(
    workout_text: str,
    living_profile: dict[str, Any],
    *,
    source: str = "coach_workout",
    trace_id: str | None = None,
) -> str:
    """
    Agent 5 (Medical Safety Officer):
    Intercepts workout output and rewrites unsafe movements before user delivery.
    Reviews both physical injuries AND chronic medical conditions.
    """
    injuries = _extract_injuries(living_profile)
    medical_conditions = _extract_medical_conditions(living_profile)
    address = resolve_user_address(living_profile)
    
    has_safety_concerns = bool(injuries or medical_conditions)
    
    log_agent_event(
        agent="medical_safety_officer",
        stage="start",
        trace_id=trace_id,
        details={
            "source": source,
            "injury_count": len(injuries),
            "medical_condition_count": len(medical_conditions),
        },
    )
    if not has_safety_concerns:
        log_agent_event(
            agent="medical_safety_officer",
            stage="bypass",
            status="no_safety_concerns",
            trace_id=trace_id,
        )
        return workout_text

    if not GEMINI_API_KEY:
        rewritten = _rule_based_rewrite(workout_text, injuries, address=address)
        log_agent_event(
            agent="medical_safety_officer",
            stage="complete",
            status="rule_based",
            trace_id=trace_id,
            details={"rewritten": rewritten != workout_text},
        )
        return rewritten

    system_prompt = (
        "You are Medical_Safety_Officer for Apna Coach. Your role is purely analytical "
        "and protective.\n"
        "You receive a draft workout, user's injury profile, and chronic medical conditions.\n"
        "Rules:\n"
        "- Review for TWO types of safety concerns:\n"
        "  * Physical injuries (knee, shoulder, back, etc.) — check movement safety\n"
        "  * Chronic medical conditions (diabetes, hypertension, asthma, etc.) — check intensity/recovery safety\n"
        "- If workout is safe for these conditions, return the original workout unchanged.\n"
        "- If any movement is risky, rewrite only the risky parts with low-impact alternatives.\n"
        "- For medical conditions like diabetes: warn about blood sugar monitoring, hydration, and gradual intensity.\n"
        "- For conditions like hypertension: avoid extremely high-intensity bursts, include recovery periods.\n"
        f"- Keep the same concise WhatsApp format and use address token '{address}'.\n"
        "- If you swap something, explicitly mention why (injury/medical protection).\n"
        "- Do not add markdown.\n"
        "- Output plain text only."
    )

    model_input = {
        "source": source,
        "physical_injuries": injuries,
        "chronic_medical_conditions": medical_conditions,
        "draft_workout": workout_text,
    }

    def _call_model() -> str:
        started_at = time.perf_counter()
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(model_input, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="medical_safety_officer",
            stage="review_workout_safety",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=model_input,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        return (response.text or "").strip()

    try:
        reviewed = await asyncio.to_thread(_call_model)
        if reviewed:
            log_agent_event(
                agent="medical_safety_officer",
                stage="complete",
                status="llm_reviewed",
                trace_id=trace_id,
                details={"rewritten": reviewed != workout_text},
            )
            return reviewed
    except Exception as exc:  # noqa: BLE001
        print(f"[Medical Safety Officer] LLM review failed: {exc}")
        log_agent_event(
            agent="medical_safety_officer",
            stage="error",
            status="llm_failed",
            trace_id=trace_id,
            details={"error": str(exc)},
        )

    rewritten = _rule_based_rewrite(workout_text, injuries, address=address)
    log_agent_event(
        agent="medical_safety_officer",
        stage="complete",
        status="fallback_rule_based",
        trace_id=trace_id,
        details={"rewritten": rewritten != workout_text},
    )
    return rewritten
