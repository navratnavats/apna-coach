from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage

ALLOWED_TYPES = {"diet", "training", "hybrid"}
ALLOWED_HORIZONS = {"daily", "weekly", "monthly"}


def _fallback(user_message: str) -> dict[str, Any]:
    text = str(user_message or "").strip()
    return {
        "type": "hybrid",
        "horizon": "weekly",
        "weeks_total": 12,
        "week_blocks": [{"week": 1, "focus": "Consistency", "actions": [text or "Follow current plan"]}],
        "day_actions": [text or "Follow current plan"],
        "response_text_preview": "Week 1 starter plan ready. Aaj se consistency block start karte hain.",
        "response_text_full": "Full progressive plan ready hai. Bolo 'show full plan' to see all weeks in detail.",
        "response_text": "Week 1 starter plan ready. Aaj se consistency block start karte hain.",
        "needs_clarification": False,
        "clarifying_questions": [],
        "rolling": {
            "roadmap_weeks": [{"week": 1, "focus": "Starter consistency block", "actions": [text or "Start safely"]}],
            "unlocked_days": [
                {
                    "day": "Mon",
                    "workout": {"title": "Starter walk", "items": [{"name": "Brisk walk", "sets": "1", "reps": "20 mins", "rest_sec": 0, "tempo": "easy"}]},
                    "meals": {"target_kcal": 0, "protein_g": 0, "carbs_g": 0, "fats_g": 0, "blocks": [{"meal": "breakfast", "items": ["Protein-rich meal"], "kcal": 0}]},
                },
                {
                    "day": "Tue",
                    "workout": {"title": "Mobility + core", "items": [{"name": "Mobility circuit", "sets": "1", "reps": "15 mins", "rest_sec": 30, "tempo": "controlled"}]},
                    "meals": {"target_kcal": 0, "protein_g": 0, "carbs_g": 0, "fats_g": 0, "blocks": [{"meal": "lunch", "items": ["Balanced meal"], "kcal": 0}]},
                },
            ],
            "next_unlock_hint": "Reply with Day 1 completion and meals to unlock next day.",
            "missing_inputs": [],
        },
        "generation_status": "fallback_error",
        "error_type": "plan_generation_failed",
        "error_message": "",
    }


async def generate_structured_plan(
    *,
    user_message: str,
    living_profile: dict[str, Any],
    plan_context: dict[str, Any],
    request_mode: str = "create",
    strict_daywise: bool = False,
    required_training_days_per_week: int = 5,
    detailed_day_limit: int = 2,
    goal_anchor: str = "",
    trace_id: str | None = None,
) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        return _fallback(user_message)

    system_prompt = (
        "You are an elite certified fitness and nutrition specialist coach.\n"
        "You design safe, personalized, evidence-aligned plans for Indian users.\n"
        "You adapt for any goal domain (fat loss, muscle gain, running, swimming, endurance, mobility, rehab-safe training, lifestyle fitness, diet-only, training-only, or hybrid).\n"
        "You are not limited to running.\n"
        "Return ONLY JSON with keys exactly:\n"
        "{"
        "\"type\":\"diet|training|hybrid\","
        "\"horizon\":\"daily|weekly|monthly\","
        "\"weeks_total\":0,"
        "\"week_blocks\":[{\"week\":1,\"focus\":\"...\",\"actions\":[\"...\"],\"days\":[{\"day\":\"Mon\",\"workout\":{\"title\":\"...\",\"items\":[{\"name\":\"...\",\"sets\":\"...\",\"reps\":\"...\",\"rest_sec\":60,\"tempo\":\"...\"}]},\"meals\":{\"target_kcal\":0,\"protein_g\":0,\"carbs_g\":0,\"fats_g\":0,\"blocks\":[{\"meal\":\"breakfast\",\"items\":[\"...\"],\"kcal\":0}]} }]}],"
        "\"day_actions\":[\"...\"],"
        "\"response_text_preview\":\"short WhatsApp-ready week-1 summary\","
        "\"response_text_full\":\"short WhatsApp-ready full-plan summary\","
        "\"needs_clarification\":false,"
        "\"clarifying_questions\":[\"...\"],"
        "\"overtraining_risk\":\"low|moderate|high\","
        "\"edit_applied\":false,"
        "\"rolling\":{\"roadmap_weeks\":[{\"week\":1,\"focus\":\"...\",\"actions\":[\"...\"]}],\"unlocked_days\":[{\"day\":\"Mon\",\"workout\":{\"title\":\"...\",\"items\":[{\"name\":\"...\",\"sets\":\"...\",\"reps\":\"...\",\"rest_sec\":60,\"tempo\":\"...\"}]},\"meals\":{\"target_kcal\":0,\"protein_g\":0,\"carbs_g\":0,\"fats_g\":0,\"blocks\":[{\"meal\":\"breakfast\",\"items\":[\"...\"],\"kcal\":0}]}}],\"next_unlock_hint\":\"...\",\"missing_inputs\":[\"...\"]}"
        "}\n"
        "Rules:\n"
        "- If key planning inputs are missing, set needs_clarification=true and ask concise high-value questions.\n"
        "- If user gives only timeframe (e.g., '16 weeks plan') but goal is unclear, MUST ask whether to optimize for the stated goal or a different goal.\n"
        "- Clarification should target: goal, timeframe, schedule availability, work/lifestyle pattern, experience level, aggressiveness level, and diet preferences/restrictions.\n"
        "- Do not assume plan duration. Derive weeks_total from user-provided timeframe; if missing use 0 and ask.\n"
        "- week_blocks can be max 12 when timeframe supports it.\n"
        "- Keep day_actions actionable (max 7).\n"
        "- Include both training and diet details when user intent is mixed or hybrid.\n"
        "- Aggressiveness must influence volume/intensity/progression for both training and diet strictness.\n"
        "- For any sport/activity goal (e.g., swim, run, cycling, strength), create progressive and sport-specific structure.\n"
        "- If user request is too aggressive/unsafe, set overtraining_risk=high and ask to start with a safer 1-week ramp.\n"
        "- Respect job/work-life constraints (shift work, desk job, commute, family constraints) for timing and adherence.\n"
        "- Always account for injuries and medical constraints from profile.\n"
        "- For request_mode=edit, preserve valid existing plan structure and apply targeted modifications; do not discard whole plan unless user explicitly asks full rebuild.\n"
        "- If strict_daywise=true, include at least one day entry under days[] for EVERY generated week block.\n"
        "- required_training_days_per_week is an integer 1..7.\n"
        "- For training/hybrid plans, include at least required_training_days_per_week unique day entries per week.\n"
        "- detailed_day_limit is 1..3. Provide that many detailed days first, then keep week roadmap concise.\n"
        "- Keep all recommendations aligned to goal_anchor if provided.\n"
        "- Keep workout and diet sections clearly differentiated for each detailed day.\n"
        "- For weeks beyond detailed_day_limit, provide concise week-level progression roadmap.\n"
        "- rolling.roadmap_weeks and rolling.unlocked_days are REQUIRED. unlocked_days must contain exactly detailed_day_limit days when enough info is available.\n"
        "- If constraints are missing, MUST set needs_clarification=true and include missing_inputs; do not return shallow partial plan.\n"
        "- Avoid repeating the same confirmation phrasing; ask only concrete missing fields.\n"
        "- Respect injuries, goals, constraints, and available equipment.\n"
        "- response text must be concise, clear, and coach-like."
    )
    payload = {
        "user_message": user_message,
        "living_profile": living_profile,
        "plan_context": plan_context,
        "request_mode": str(request_mode or "create"),
        "strict_daywise": bool(strict_daywise),
        "required_training_days_per_week": max(1, min(7, int(required_training_days_per_week or 5))),
        "detailed_day_limit": max(1, min(3, int(detailed_day_limit or 2))),
        "goal_anchor": str(goal_anchor or "").strip()[:240],
    }

    def _call() -> dict[str, Any]:
        started_at = time.perf_counter()
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "application/json"},
        )
        print(
            "[PlanAgent][LLM_REQUEST] "
            f"prompt={system_prompt[:1200]} payload={json.dumps(payload, ensure_ascii=False)[:2000]}"
        )
        print(
            "[PlanAgent][LLM_RESPONSE] "
            f"{(response.text or '')[:3000]}"
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="plan_agent",
            stage="generate_structured_plan",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        raw = json.loads((response.text or "{}").strip())
        return raw if isinstance(raw, dict) else {}

    try:
        raw = await asyncio.to_thread(_call)
    except Exception as exc:
        fallback = _fallback(user_message)
        fallback["error_type"] = str(exc.__class__.__name__ or "plan_generation_failed")
        fallback["error_message"] = str(exc)[:500]
        return fallback

    plan_type = str(raw.get("type") or "hybrid").strip().lower()
    if plan_type not in ALLOWED_TYPES:
        plan_type = "hybrid"
    horizon = str(raw.get("horizon") or "weekly").strip().lower()
    if horizon not in ALLOWED_HORIZONS:
        horizon = "weekly"
    weeks_total = raw.get("weeks_total")
    try:
        weeks_total = int(float(weeks_total))
    except (TypeError, ValueError):
        weeks_total = 0
    weeks_total = max(0, min(12, weeks_total))

    week_blocks = raw.get("week_blocks") or []
    if not isinstance(week_blocks, list):
        week_blocks = []
    week_blocks = week_blocks[:12]
    day_actions = raw.get("day_actions") or []
    if not isinstance(day_actions, list):
        day_actions = []
    day_actions = [str(x).strip() for x in day_actions if str(x).strip()][:7]
    response_text_preview = str(raw.get("response_text_preview") or "").strip()
    response_text_full = str(raw.get("response_text_full") or "").strip()
    fallback = _fallback(user_message)
    if not response_text_preview:
        response_text_preview = fallback["response_text_preview"]
    if not response_text_full:
        response_text_full = fallback["response_text_full"]

    needs_clarification = bool(raw.get("needs_clarification"))
    clarifying_questions = raw.get("clarifying_questions") or []
    if not isinstance(clarifying_questions, list):
        clarifying_questions = []
    clarifying_questions = [str(x).strip() for x in clarifying_questions if str(x).strip()][:3]
    overtraining_risk = str(raw.get("overtraining_risk") or "low").strip().lower()
    if overtraining_risk not in {"low", "moderate", "high"}:
        overtraining_risk = "low"
    edit_applied = bool(raw.get("edit_applied"))
    rolling = raw.get("rolling") or {}
    if not isinstance(rolling, dict):
        rolling = {}
    roadmap_weeks = rolling.get("roadmap_weeks") or []
    if not isinstance(roadmap_weeks, list):
        roadmap_weeks = []
    unlocked_days = rolling.get("unlocked_days") or []
    if not isinstance(unlocked_days, list):
        unlocked_days = []
    next_unlock_hint = str(rolling.get("next_unlock_hint") or "").strip()
    missing_inputs = rolling.get("missing_inputs") or []
    if not isinstance(missing_inputs, list):
        missing_inputs = []
    missing_inputs = [str(x).strip() for x in missing_inputs if str(x).strip()][:4]
    if not week_blocks and day_actions:
        week_blocks = [{"week": 1, "focus": "Starter block", "actions": day_actions}]
    if not day_actions and week_blocks:
        first = week_blocks[0]
        actions = first.get("actions") if isinstance(first, dict) else []
        if isinstance(actions, list):
            day_actions = [str(x).strip() for x in actions if str(x).strip()][:7]
    if not roadmap_weeks and week_blocks:
        for b in week_blocks[:8]:
            if not isinstance(b, dict):
                continue
            roadmap_weeks.append(
                {
                    "week": int(float(b.get("week") or 0) or 1),
                    "focus": str(b.get("focus") or "Progress block").strip(),
                    "actions": [str(a).strip() for a in (b.get("actions") or []) if str(a).strip()][:3],
                }
            )
    if not unlocked_days and week_blocks and isinstance(week_blocks[0], dict):
        for d in (week_blocks[0].get("days") or [])[: max(1, min(3, int(detailed_day_limit or 2)) )]:
            if isinstance(d, dict):
                unlocked_days.append(d)
    if not next_unlock_hint:
        next_unlock_hint = "Reply with completion + meal adherence to unlock next day."
    return {
        "type": plan_type,
        "horizon": horizon,
        "weeks_total": weeks_total,
        "week_blocks": week_blocks,
        "day_actions": day_actions,
        "response_text_preview": response_text_preview,
        "response_text_full": response_text_full,
        "response_text": response_text_preview,
        "needs_clarification": needs_clarification,
        "clarifying_questions": clarifying_questions,
        "overtraining_risk": overtraining_risk,
        "edit_applied": edit_applied,
        "rolling": {
            "roadmap_weeks": roadmap_weeks,
            "unlocked_days": unlocked_days,
            "next_unlock_hint": next_unlock_hint,
            "missing_inputs": missing_inputs,
        },
        "generation_status": "ok",
        "error_type": "",
        "error_message": "",
    }

