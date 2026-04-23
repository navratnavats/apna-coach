from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.critic_agent import run_critic_agent


def _today_nutrition_entries(
    living_profile: dict[str, Any], timezone_name: str
) -> list[dict[str, Any]]:
    logs = living_profile.get("logs") or {}
    nutrition_log = logs.get("nutrition_log") or []
    if not isinstance(nutrition_log, list):
        return []

    tz = ZoneInfo(timezone_name)
    today_local = datetime.now(tz).date()
    today_entries: list[dict[str, Any]] = []

    for entry in nutrition_log:
        if not isinstance(entry, dict):
            continue
        raw_ts = str(entry.get("logged_at") or "").strip()
        if not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if dt.astimezone(tz).date() == today_local:
                today_entries.append(entry)
        except ValueError:
            continue

    return today_entries


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def generate_dietitian_review(
    living_profile: dict[str, Any], timezone_name: str, trace_id: str | None = None
) -> str:
    """
    Generate end-of-day concise accountability message from today's nutrition log.
    """
    today_entries = _today_nutrition_entries(living_profile, timezone_name)
    log_agent_event(
        agent="dietitian",
        stage="start",
        trace_id=trace_id,
        details={"today_entries": len(today_entries)},
    )
    if not today_entries:
        return await run_critic_agent(
            "Bhai, aaj kuch khaya nahi ya log karna bhool gaye? Aise progress nahi hogi.",
            source="dietitian_review",
            trace_id=trace_id,
        )

    total_calories = sum(_safe_float(e.get("estimated_calories")) for e in today_entries)
    total_protein = sum(
        _safe_float((e.get("estimated_macros") or {}).get("protein_g")) for e in today_entries
    )

    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    body_weight = _safe_float(biometrics.get("weight"))
    # Use 1.6g/kg as baseline if explicit target missing.
    estimated_protein_goal = round(body_weight * 1.6, 1) if body_weight > 0 else 120.0

    logs = living_profile.get("logs") or {}
    current_day = logs.get("current_day") or {}
    calorie_budget = _safe_float(current_day.get("calorie_budget"))
    has_calorie_budget = calorie_budget > 0

    if not GEMINI_API_KEY:
        lines = []
        if total_protein >= estimated_protein_goal:
            lines.append("Protein intake on point today. Muscle recovery sorted.")
        if has_calorie_budget and total_calories > calorie_budget:
            lines.append("Calorie budget exceed ho gaya hai. Kal subah extra 2km run pakka.")
        if not lines:
            lines.append(
                "Bhai, aaj ka nutrition theek gaya. Kal logging aur protein consistency pe focus kar."
            )
        return await run_critic_agent(
            " ".join(lines),
            source="dietitian_review",
            trace_id=trace_id,
        )

    system_prompt = (
        "You are the Dietitian Agent for Apna Coach. Create a concise WhatsApp end-of-day "
        "review in conversational Hinglish.\n"
        "Rules:\n"
        "- Use the user's goals from living_profile.\n"
        "- Evaluate today's nutrition entries only.\n"
        "- Mention one win and one correction.\n"
        "- Keep tone supportive but accountable.\n"
        "- End with one short action line for tomorrow.\n"
        "- If protein_goal_hit is true, include this exact sentence: "
        "'Protein intake on point today. Muscle recovery sorted.'\n"
        "- If over_calorie_budget is true, include this exact sentence: "
        "'Calorie budget exceed ho gaya hai. Kal subah extra 2km run pakka.'\n"
        "- Plain text only, no markdown."
    )

    model_input = {
        "living_profile": living_profile,
        "today_nutrition_log": today_entries,
        "metrics": {
            "total_calories": round(total_calories, 1),
            "total_protein_g": round(total_protein, 1),
            "estimated_protein_goal_g": estimated_protein_goal,
            "calorie_budget": calorie_budget if has_calorie_budget else None,
            "protein_goal_hit": total_protein >= estimated_protein_goal,
            "over_calorie_budget": has_calorie_budget and total_calories > calorie_budget,
        },
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
        text = await asyncio.to_thread(_call_model)
    except Exception as exc:  # noqa: BLE001
        print(f"[Dietitian] LLM review failed: {exc}")
        return await run_critic_agent(
            (
            "Bhai, quick review: aaj ka logging complete rakha, great. Kal protein thoda "
            "aur consistent rakhte hain aur water target hit karte hain."
            ),
            source="dietitian_review",
            trace_id=trace_id,
        )

    final = await run_critic_agent(
        text
        or (
        "Bhai, aaj ka nutrition review ready hai. Kal se thoda aur disciplined logging "
        "aur protein focus rakhenge."
        ),
        source="dietitian_review",
        trace_id=trace_id,
    )
    log_agent_event(
        agent="dietitian",
        stage="complete",
        trace_id=trace_id,
        details={"chars": len(final)},
    )
    return final

