from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.bio_math_agent import compute_current_day_metrics
from app.services.critic_agent import run_critic_agent
from app.services.messages import (
    coach_burn_recalc_hint,
    coach_historical_not_found,
    coach_missing_equipment,
)
from app.services.medical_safety_officer import run_medical_safety_officer
from app.services.persona import resolve_user_address


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


def _is_burn_or_deficit_query(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    calorie_terms = (
        "calorie",
        "calories",
        "kcal",
        "burn",
        "burnt",
        "deficit",
        "net",
    )
    activity_terms = ("run", "running", "walk", "steps", "swim", "cycling", "workout")
    question_terms = ("how much", "kitna", "kitni", "today", "aaj")
    has_calorie_context = any(term in text for term in calorie_terms)
    has_question = any(term in text for term in question_terms)
    has_activity_hint = any(term in text for term in activity_terms)
    return has_calorie_context and (has_question or has_activity_hint)


def _is_metric_explanation_query(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    has_metric_word = any(
        k in text for k in ("deficit", "tdee", "maintenance", "budget", "target")
    )
    has_meaning_word = any(
        k in text for k in ("matlab", "mean", "kya", "safe", "sahi", "zyada", "kam")
    )
    return has_metric_word and has_meaning_word


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
        psychology = living_profile.get("psychology") or {}
        style = str(psychology.get("motivation_style") or "gentle_nudge").strip().lower()
        if style == "strict_accountability":
            cadence = 2
        elif style == "challenge":
            cadence = 2
        elif style == "empathetic":
            cadence = 4
        else:
            cadence = 3
        return int(coach_message_count) % cadence == 0 and int(coach_message_count) > 0
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


async def _finalize_with_critic(
    draft_text: str,
    *,
    source: str,
    living_profile: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> str:
    polished = await run_critic_agent(
        draft_text,
        source=source,
        living_profile=living_profile,
        trace_id=trace_id,
    )
    return _sanitize_coach_reply(polished or draft_text)


async def generate_coach_reply(
    user_message: str,
    living_profile: dict[str, Any],
    session_context: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> str:
    """
    Brain B (Coach):
    Generate an empathetic, concise coaching reply using fresh profile context.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing for coach reply generation.")
    log_agent_event(
        agent="coach",
        stage="start",
        trace_id=trace_id,
        details={"message_chars": len(user_message or "")},
    )

    session = session_context or {}
    address = resolve_user_address(living_profile)
    historical_query_result = session.get("historical_query_result")
    if isinstance(historical_query_result, dict):
        archive_date = str(historical_query_result.get("archive_date") or "").strip()
        nutrition_entries = historical_query_result.get("nutrition_entries") or []
        if not isinstance(nutrition_entries, list):
            nutrition_entries = []
        if nutrition_entries:
            lines = []
            for entry in nutrition_entries[:8]:
                if not isinstance(entry, dict):
                    continue
                summary = str(entry.get("summary") or "meal").strip()
                calories = int(float(entry.get("estimated_calories") or 0))
                lines.append(f"- {summary} ({calories} kcal)")
            if lines:
                historical_reply = (
                    f"{address}, {archive_date} ka exact food log mil gaya:\n"
                    + "\n".join(lines)
                    + "\nAgar full breakdown (macros + sources) chahiye toh bol."
                )
                # Keep exact itemized historical facts untouched by critic rewrites.
                return _sanitize_coach_reply(historical_reply)
        # If query routed but no data found.
        return _sanitize_coach_reply(coach_historical_not_found(address))

    routed_intent = str(session.get("routed_intent") or "").strip().lower()
    burn_query_routed = routed_intent == "burn_query"
    metric_explain_routed = routed_intent == "metric_explanation_query"
    burn_query_fallback = _is_burn_or_deficit_query(user_message)
    metric_explain_fallback = _is_metric_explanation_query(user_message)
    is_burn_query = burn_query_routed or burn_query_fallback
    is_metric_explain_query = metric_explain_routed or metric_explain_fallback
    is_factual_query = is_burn_query or is_metric_explain_query

    logs = living_profile.get("logs") or {}
    current_day = logs.get("current_day") or {}
    metrics = current_day.get("metrics") if isinstance(current_day, dict) else {}
    if not isinstance(metrics, dict) or not metrics:
        metrics = compute_current_day_metrics(living_profile)
    burn_facts = session.get("burn_facts") or {}
    if isinstance(burn_facts, dict):
        metrics = {**metrics, **burn_facts}

    tdee = int(float(metrics.get("tdee_cals") or 0))
    active = int(float(metrics.get("active_cals_burnt") or 0))
    intake = int(float(metrics.get("intake_cals") or metrics.get("cals") or 0))
    net_deficit = int(float(metrics.get("net_deficit_cals") or metrics.get("net_deficit") or 0))
    budget = int(float(metrics.get("calorie_budget_cals") or metrics.get("calorie_budget") or 0))
    versus_budget = int(float(metrics.get("vs_budget_cals") or (budget - intake if budget > 0 else 0)))

    if is_metric_explain_query:
        if net_deficit >= 1000:
            meaning = "ye kaafi aggressive deficit hai (daily basis pe deep side)."
            action = (
                "Kal recovery better rakh: 250-400 kcal clean add kar, protein + hydration maintain kar."
            )
        elif net_deficit >= 400:
            meaning = "ye solid fat-loss zone me hai."
            action = "Isi range ko consistency se maintain kar."
        elif net_deficit >= 0:
            meaning = "deficit mild hai, progress slow but sustainable rahega."
            action = "Agar pace tez chahiye toh activity ya intake me small adjustment kar."
        else:
            meaning = "aaj aap surplus me gaye hain (deficit negative)."
            action = "Kal food quality tighten kar ya extra activity add kar."

        budget_status = "neeche" if versus_budget >= 0 else "upar"
        explain_reply = (
            f"Number: Budget gap {abs(versus_budget)} kcal {budget_status} (budget {budget}, intake {intake}).\n"
            f"Meaning: Plan ke hisaab se aap target calories se {abs(versus_budget)} kcal {budget_status} hain. "
            f"Safety context: net deficit {net_deficit} = ({tdee} + {active}) - {intake}.\n"
            f"Action: {action}"
        )
        return await _finalize_with_critic(
            explain_reply,
            source="coach_metric_explain",
            living_profile=living_profile,
            trace_id=trace_id,
        )

    if is_burn_query:
        budget_status = "neeche" if versus_budget >= 0 else "upar"
        burn_reply = (
            f"Number: Budget gap {abs(versus_budget)} kcal {budget_status} "
            f"(budget {budget}, intake {intake}).\n"
            f"Meaning: Aaj active burn {active} kcal hai. Safety context: net deficit {net_deficit} "
            f"= ({tdee} + {active}) - {intake}.\n"
            + coach_burn_recalc_hint()
        )
        return await _finalize_with_critic(
            burn_reply,
            source="coach_burn_query",
            living_profile=living_profile,
            trace_id=trace_id,
        )

    is_workout_request = False
    if not is_factual_query:
        is_workout_request = await _detect_workout_intent(user_message)
    log_agent_event(
        agent="coach",
        stage="intent_detected",
        trace_id=trace_id,
        details={
            "is_workout_request": is_workout_request,
            "is_burn_query": is_burn_query,
            "is_metric_explain_query": is_metric_explain_query,
            "routed_intent": routed_intent or "none",
        },
    )
    equipment_list = _get_available_equipment(living_profile)

    # Gatekeeper logic: collect equipment before workout programming.
    if is_workout_request and len(equipment_list) == 0:
        return await _finalize_with_critic(
            (
            coach_missing_equipment(address)
            ),
            source="coach",
            living_profile=living_profile,
            trace_id=trace_id,
        )

    # Specialist handoff: for workout requests with equipment available,
    # route to Workout Programmer agent prompt.
    if is_workout_request:
        workout_text = await _generate_workout_program(user_message, living_profile)
        if workout_text.strip():
            reviewed_workout = await run_medical_safety_officer(
                workout_text,
                living_profile,
                source="coach_workout",
                trace_id=trace_id,
            )
            return await _finalize_with_critic(
                reviewed_workout or workout_text,
                source="coach_workout",
                living_profile=living_profile,
                trace_id=trace_id,
            )

    add_reminder = (not is_factual_query) and _should_add_motivation_reminder(living_profile)
    reminder_anchor = _motivation_anchor(living_profile)
    response_mode = str((session_context or {}).get("response_mode") or "push").strip().lower()
    policy_decision = str((session_context or {}).get("policy_decision") or "allow").strip().lower()
    policy_reason = str((session_context or {}).get("policy_reason") or "normal").strip().lower()

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
    if response_mode == "support":
        additional_rules.append(
            "PSYCHOLOGY MODE: User may be low/stressed. Keep tone empathetic, reduce pressure, give one easy next step."
        )
    elif response_mode == "simplify":
        additional_rules.append(
            "PSYCHOLOGY MODE: Keep response very simple with one immediate action."
        )
    elif response_mode == "celebrate":
        additional_rules.append(
            "PSYCHOLOGY MODE: Start with a short celebration line, then next action."
        )
    if policy_decision == "allow_constrained":
        additional_rules.append(
            "POLICY MODE: This query is constrained. Stay within safe fitness-coaching boundaries, "
            "avoid medical diagnosis/unsafe extremes/out-of-scope actions, and provide conservative guidance."
        )
        additional_rules.append(f"POLICY REASON: {policy_reason}")

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
        "If session_context.workout_logged_this_turn is true, start with a short "
        "congratulatory line using the user's preferred address and acknowledge progression. If "
        "session_context.workout_highlight is non-empty, mention it naturally.\n"
        "If session_context.activity_burn_logged_this_turn is true, acknowledge "
        "estimated calories burnt and mention assumptions from "
        "session_context.activity_assumptions briefly. Tell user they can say "
        "'no rest/continuous' to recalculate.\n"
        "If user asks how much calories burned/deficit today, answer using hard "
        "numbers from logs.current_day.active_cals_burnt and logs.current_day.net_deficit "
        "(do not guess). Prefer session_context.burn_facts when present and keep "
        "those numeric values exact.\n"
        "If session_context.plan_context exists and routed intent is plan-related, "
        "use it as source of truth for continuity. For plan_create_request, provide a "
        "structured starter plan with Week 1 and tomorrow actions. For plan_status_query, "
        "answer from existing plan_context first. For plan_edit_request, return revised "
        "current block reflecting the requested change.\n"
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
        return reply_text

    draft_reply = await asyncio.to_thread(_call_model)
    final = await _finalize_with_critic(
        draft_reply,
        source="coach",
        living_profile=living_profile,
        trace_id=trace_id,
    )
    log_agent_event(
        agent="coach",
        stage="complete",
        trace_id=trace_id,
        details={"chars": len(final)},
    )
    return final

