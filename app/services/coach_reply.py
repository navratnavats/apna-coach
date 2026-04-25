from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
from app.services.llm_contract_runner import run_json_contract
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address
from app.services.intent_contract import (
    classify_heuristic_intent,
    should_allow_detector_fallback,
)

MAX_COACH_RETRIES = 3


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


def _get_training_environment(living_profile: dict[str, Any]) -> str:
    lifestyle = living_profile.get("lifestyle") or {}
    env = str(lifestyle.get("training_environment") or "").strip().lower()
    return env


def _today_food_entries(living_profile: dict[str, Any]) -> list[dict[str, Any]]:
    logs = living_profile.get("logs") or {}
    raw = logs.get("nutrition_log") or []
    if not isinstance(raw, list):
        return []
    identity = living_profile.get("identity") or {}
    tz_name = str(identity.get("timezone") or "Asia/Kolkata").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    today_local = datetime.now(tz).date()
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        try:
            estimated_calories = int(float(item.get("estimated_calories") or 0))
        except (TypeError, ValueError):
            estimated_calories = 0
        local_date = str(item.get("local_date") or "").strip()
        if local_date:
            if local_date == today_local.isoformat() and (summary or estimated_calories > 0):
                entries.append(item)
            continue
        logged_at = str(item.get("logged_at") or "").strip()
        if not logged_at:
            # Drop placeholder/noise entries with no time + no content.
            if summary or estimated_calories > 0:
                entries.append(item)
            continue
        try:
            dt = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
            if dt.astimezone(tz).date() == today_local and (summary or estimated_calories > 0):
                entries.append(item)
        except Exception:
            if summary or estimated_calories > 0:
                entries.append(item)
    return entries


def _today_activity_entries(living_profile: dict[str, Any]) -> list[dict[str, Any]]:
    logs = living_profile.get("logs") or {}
    raw = logs.get("activity_log") or []
    if not isinstance(raw, list):
        return []
    identity = living_profile.get("identity") or {}
    tz_name = str(identity.get("timezone") or "Asia/Kolkata").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    today_local = datetime.now(tz).date()
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        local_date = str(item.get("local_date") or "").strip()
        if local_date:
            if local_date == today_local.isoformat():
                entries.append(item)
            continue
        logged_at = str(item.get("logged_at") or "").strip()
        if not logged_at:
            entries.append(item)
            continue
        try:
            dt = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
            if dt.astimezone(tz).date() == today_local:
                entries.append(item)
        except Exception:
            entries.append(item)
    return entries


def _is_activity_recall_query(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    activity_words = ("workout", "exercise", "activity", "training", "run", "walk", "gym")
    recall_words = ("what did i do", "kya kiya", "done today", "aaj kya", "session")
    return any(a in text for a in activity_words) and any(r in text for r in recall_words)


def _build_activity_recall_reply(user_message: str, living_profile: dict[str, Any], address: str) -> str:
    text = str(user_message or "").strip().lower()
    entries = _today_activity_entries(living_profile)
    if not entries:
        return f"{address}, aaj ka activity/workout log abhi empty hai."

    slot = ""
    if any(k in text for k in ("morning", "subah")):
        slot = "morning_session"
    elif any(k in text for k in ("afternoon", "dopahar", "noon")):
        slot = "afternoon_session"
    elif any(k in text for k in ("evening", "shaam", "night")):
        slot = "evening_session"

    if slot:
        slot_entries = [e for e in entries if str(e.get("session_slot") or "").strip().lower() == slot]
        if not slot_entries:
            return f"{address}, aaj {slot.replace('_', ' ')} me koi activity log nahi mila."
        entries = slot_entries

    lines = []
    total_burn = 0
    for entry in entries[:8]:
        name = str(entry.get("name") or entry.get("summary") or "activity").strip()
        dur = int(float(entry.get("duration_mins") or 0))
        burn = int(float(entry.get("burn_cals") or 0))
        total_burn += burn
        if dur > 0:
            lines.append(f"- {name} ({dur} min, {burn} kcal)")
        else:
            lines.append(f"- {name} ({burn} kcal)")
    return (
        f"{address}, aaj ke activity logs:\n"
        + "\n".join(lines)
        + f"\nTotal active burn (logged): {total_burn} kcal."
    )


def _build_food_recall_reply(user_message: str, living_profile: dict[str, Any], address: str) -> str:
    text = str(user_message or "").strip().lower()
    entries = _today_food_entries(living_profile)
    if not entries:
        return f"{address}, aaj ka food log abhi empty hai. Jo bhi khaya ho, text/photo/voice me bhej do."

    meal_slot = ""
    if any(k in text for k in ("breakfast", "nashta")):
        meal_slot = "breakfast"
    elif "lunch" in text:
        meal_slot = "lunch"
    elif any(k in text for k in ("dinner", "raat")):
        meal_slot = "dinner"
    elif "snack" in text:
        meal_slot = "evening_snack"

    if meal_slot:
        meal_entries = [e for e in entries if str(e.get("meal_slot") or "").strip().lower() == meal_slot]
        if not meal_entries:
            meal_entries = [
                e
                for e in entries
                if meal_slot in str(e.get("summary") or "").strip().lower()
            ]
        if not meal_entries:
            return f"{address}, aaj {meal_slot.replace('_', ' ')} tag ke saath koi entry nahi mili."
        lines = []
        total = 0
        for entry in meal_entries[:6]:
            summary = str(entry.get("summary") or "meal").strip()
            cals = int(float(entry.get("estimated_calories") or 0))
            total += cals
            lines.append(f"- {summary} ({cals} kcal)")
        return (
            f"{address}, aaj {meal_slot.replace('_', ' ')} me:\n"
            + "\n".join(lines)
            + f"\nTotal: {total} kcal."
        )

    lines = []
    total = 0
    for entry in entries[:10]:
        summary = str(entry.get("summary") or "meal").strip()
        cals = int(float(entry.get("estimated_calories") or 0))
        total += cals
        lines.append(f"- {summary} ({cals} kcal)")
    return (
        f"{address}, aaj ke logged meals ye hain:\n"
        + "\n".join(lines)
        + f"\nTotal intake (logged): {total} kcal."
    )


async def _infer_training_environment_from_query(
    user_message: str,
    *,
    trace_id: str | None = None,
) -> tuple[str, str]:
    """
    LLM-based environment mapping from user's workout query.
    Returns (environment, confidence) where environment is one of:
    home | gym | both | unknown.
    """
    if not GEMINI_API_KEY:
        return ("unknown", "low")

    system_prompt = (
        "Classify the likely workout training environment from the user's message. "
        "Return ONLY JSON with keys: environment, confidence.\n"
        "environment must be one of: home, gym, both, unknown.\n"
        "confidence must be one of: high, medium, low.\n"
        "Use unknown when unclear."
    )

    def _observe(payload: dict[str, Any], response_text: str, elapsed_ms: int, response: object) -> None:
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="coach",
            stage="infer_training_environment",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response_text,
            usage=extract_gemini_usage(response),
        )
    def _validate(parsed: dict[str, Any]) -> tuple[str, str]:
        env = str(parsed.get("environment") or "unknown").strip().lower()
        conf = str(parsed.get("confidence") or "low").strip().lower()
        if env not in {"home", "gym", "both", "unknown"}:
            raise ValueError(f"invalid_environment:{env or 'empty'}")
        if conf not in {"high", "medium", "low"}:
            raise ValueError(f"invalid_confidence:{conf or 'empty'}")
        return (env, conf)

    try:
        return await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message},
            max_retries=MAX_COACH_RETRIES,
            validator=_validate,
            on_attempt_response=_observe,
        )
    except Exception:  # noqa: BLE001
        return ("unknown", "low")


async def _generate_workout_program(
    user_message: str,
    living_profile: dict[str, Any],
    *,
    trace_id: str | None = None,
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

    def _call_model(payload: dict[str, Any]) -> str:
        started_at = time.perf_counter()
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "text/plain"},
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="workout_programmer",
            stage="generate_quick_hit",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("empty_workout_program")
        return text

    previous_error = ""
    previous_output = ""
    for attempt in range(1, MAX_COACH_RETRIES + 1):
        payload = {
            "living_profile": living_profile,
            "user_message": user_message,
            "retry_context": (
                {
                    "attempt": attempt,
                    "previous_failure_reason": previous_error,
                    "previous_output": previous_output,
                    "instruction": "Do not repeat previous failure. Return concise workout plain text.",
                }
                if attempt > 1
                else {}
            ),
        }
        try:
            output = await asyncio.to_thread(_call_model, payload)
            if output == previous_output and attempt > 1:
                raise ValueError("repeated_same_output")
            return output
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)
            if "output" in locals() and isinstance(output, str):
                previous_output = output
    return ""


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
    apply_critic: bool = True,
) -> str:
    if not apply_critic:
        return _sanitize_coach_reply(draft_text)
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
    router_confidence = str(session.get("router_confidence") or "low").strip().lower()
    allow_fallback_detectors = should_allow_detector_fallback(router_confidence)
    heuristic_intent = (
        classify_heuristic_intent(user_message) if allow_fallback_detectors else "general_chat"
    )
    burn_query_routed = routed_intent == "burn_query"
    metric_explain_routed = routed_intent == "metric_explanation_query"
    food_recall_routed = routed_intent == "food_recall_query"
    burn_query_fallback = heuristic_intent == "burn_query"
    metric_explain_fallback = heuristic_intent == "metric_explanation_query"
    food_recall_fallback = heuristic_intent == "food_recall_query"
    is_burn_query = burn_query_routed or burn_query_fallback
    is_metric_explain_query = metric_explain_routed or metric_explain_fallback
    is_food_recall = food_recall_routed or food_recall_fallback
    is_factual_query = is_burn_query or is_metric_explain_query

    if is_food_recall:
        recall_reply = _build_food_recall_reply(user_message, living_profile, address)
        return await _finalize_with_critic(
            recall_reply,
            source="coach_food_recall",
            living_profile=living_profile,
            trace_id=trace_id,
            apply_critic=False,
        )
    if _is_activity_recall_query(user_message):
        activity_reply = _build_activity_recall_reply(user_message, living_profile, address)
        return await _finalize_with_critic(
            activity_reply,
            source="coach_activity_recall",
            living_profile=living_profile,
            trace_id=trace_id,
            apply_critic=False,
        )

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
            apply_critic=False,
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
            apply_critic=False,
        )

    is_workout_request = routed_intent == "workout_request"
    if not is_workout_request and (not is_factual_query) and allow_fallback_detectors:
        is_workout_request = heuristic_intent == "workout_request"
    log_agent_event(
        agent="coach",
        stage="intent_detected",
        trace_id=trace_id,
        details={
            "is_workout_request": is_workout_request,
            "is_burn_query": is_burn_query,
            "is_metric_explain_query": is_metric_explain_query,
            "is_food_recall": is_food_recall,
            "routed_intent": routed_intent or "none",
            "router_confidence": router_confidence or "none",
            "allow_fallback_detectors": allow_fallback_detectors,
        },
    )
    equipment_list = _get_available_equipment(living_profile)
    training_env = _get_training_environment(living_profile)

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

    if is_workout_request and not training_env:
        inferred_env, inferred_conf = await _infer_training_environment_from_query(
            user_message,
            trace_id=trace_id,
        )
        log_agent_event(
            agent="coach",
            stage="training_env_inferred",
            trace_id=trace_id,
            details={"environment": inferred_env, "confidence": inferred_conf},
        )
        if inferred_env not in {"home", "gym", "both"} or inferred_conf == "low":
            return await _finalize_with_critic(
                (
                    f"{address}, workout plan personalize karne ke liye ek quick confirm chahiye: "
                    "aaj home setup pe train karenge, gym pe, ya dono options chahiye?"
                ),
                source="coach",
                living_profile=living_profile,
                trace_id=trace_id,
            )

        lifestyle = living_profile.get("lifestyle")
        if not isinstance(lifestyle, dict):
            lifestyle = {}
            living_profile["lifestyle"] = lifestyle
        lifestyle["training_environment"] = inferred_env
        log_agent_event(
            agent="coach",
            stage="training_env_inferred_applied",
            trace_id=trace_id,
            details={"environment": inferred_env},
        )

    # Specialist handoff: for workout requests with equipment available,
    # route to Workout Programmer agent prompt.
    if is_workout_request:
        workout_text = await _generate_workout_program(
            user_message,
            living_profile,
            trace_id=trace_id,
        )
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

    # Add conversation history if available
    conversation_history = session_context.get("conversation_history") or []
    history_context = ""
    if isinstance(conversation_history, list) and conversation_history:
        history_lines = ["Recent conversation context (last 3 turns, time-gated to 10 minutes):"]
        for i, turn_pair in enumerate(conversation_history, 1):
            if isinstance(turn_pair, dict):
                user_turn = turn_pair.get("user") or {}
                assistant_turn = turn_pair.get("assistant") or {}
                user_msg = str(user_turn.get("message") or "").strip()
                user_intent = str(user_turn.get("intent") or "unknown").strip()
                assistant_msg = str(assistant_turn.get("message") or "").strip()
                if user_msg and assistant_msg:
                    history_lines.append(f"Turn {i}:")
                    history_lines.append(f"  User ({user_intent}): {user_msg}")
                    history_lines.append(f"  Coach: {assistant_msg}")
        if len(history_lines) > 1:
            history_context = "\n".join(history_lines) + "\n\n"
    
    system_prompt = (
        "You are Apna Coach, an empathetic, firm, and knowledgeable fitness brother. "
        "You speak in conversational Hinglish (or the user's preferred language). "
        "Use natural phrases like 'tension mat le', 'focus kar', and 'dhyaan se' when appropriate. "
        "Keep messages concise for WhatsApp. Always read the provided living_profile "
        "JSON context before answering. Reference their goals, respect their injuries, "
        "and ask one guiding question at the end to keep them engaged. Do not output "
        "markdown, just clean text.\n\n"
        + history_context
        + "If session_context.nutrition_logged_this_turn is true, acknowledge that food "
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
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="coach",
            stage="generate_reply",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=model_input,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
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

