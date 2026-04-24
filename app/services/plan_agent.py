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
        "week_blocks": [{"week": 1, "focus": "Consistency", "actions": [text or "Follow current plan"]}],
        "day_actions": [text or "Follow current plan"],
        "response_text": "Starter plan ready. Aaj se consistency block start karte hain.",
    }


async def generate_structured_plan(
    *,
    user_message: str,
    living_profile: dict[str, Any],
    plan_context: dict[str, Any],
    trace_id: str | None = None,
) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        return _fallback(user_message)

    system_prompt = (
        "You are Plan Agent for Apna Coach.\n"
        "Return ONLY JSON with keys exactly:\n"
        "{"
        "\"type\":\"diet|training|hybrid\","
        "\"horizon\":\"daily|weekly|monthly\","
        "\"week_blocks\":[{\"week\":1,\"focus\":\"...\",\"actions\":[\"...\"]}],"
        "\"day_actions\":[\"...\"],"
        "\"response_text\":\"short WhatsApp-ready summary for user\""
        "}\n"
        "Rules:\n"
        "- Keep week_blocks compact (max 4 blocks).\n"
        "- Keep day_actions actionable (max 7).\n"
        "- Respect injuries, goals, constraints, and available equipment.\n"
        "- response_text must be concise and human."
    )
    payload = {
        "user_message": user_message,
        "living_profile": living_profile,
        "plan_context": plan_context,
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
    except Exception:
        return _fallback(user_message)

    plan_type = str(raw.get("type") or "hybrid").strip().lower()
    if plan_type not in ALLOWED_TYPES:
        plan_type = "hybrid"
    horizon = str(raw.get("horizon") or "weekly").strip().lower()
    if horizon not in ALLOWED_HORIZONS:
        horizon = "weekly"
    week_blocks = raw.get("week_blocks") or []
    if not isinstance(week_blocks, list):
        week_blocks = []
    week_blocks = week_blocks[:4]
    day_actions = raw.get("day_actions") or []
    if not isinstance(day_actions, list):
        day_actions = []
    day_actions = [str(x).strip() for x in day_actions if str(x).strip()][:7]
    response_text = str(raw.get("response_text") or "").strip()
    if not response_text:
        response_text = _fallback(user_message)["response_text"]
    if not week_blocks and day_actions:
        week_blocks = [{"week": 1, "focus": "Starter block", "actions": day_actions}]
    if not day_actions and week_blocks:
        first = week_blocks[0]
        actions = first.get("actions") if isinstance(first, dict) else []
        if isinstance(actions, list):
            day_actions = [str(x).strip() for x in actions if str(x).strip()][:7]
    return {
        "type": plan_type,
        "horizon": horizon,
        "week_blocks": week_blocks,
        "day_actions": day_actions,
        "response_text": response_text,
    }

