from __future__ import annotations

import time
from typing import Any

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.intent_contract import (
    ALLOWED_ROUTER_INTENTS,
    classify_heuristic_intent,
    normalize_router_result,
)
from app.services.agent_trace import log_agent_event
from app.services.llm_contract_runner import run_json_contract
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage

MAX_ROUTER_RETRIES = 3


def _fallback_intent(user_message: str) -> str:
    return classify_heuristic_intent(user_message)


async def classify_router_intent(
    user_message: str, *, last_turn: dict[str, Any] | None = None, trace_id: str | None = None
) -> dict[str, Any]:
    """
    Agent 2 (Router): classify top-level intent before heavy agent path.
    """
    log_agent_event(
        agent="router",
        stage="start",
        trace_id=trace_id,
        details={"message_chars": len(user_message or "")},
    )
    if not GEMINI_API_KEY:
        intent = _fallback_intent(user_message)
        result = {"primary_intent": intent, "confidence": "fallback"}
        log_agent_event(
            agent="router",
            stage="complete",
            status="fallback",
            trace_id=trace_id,
            details=result,
        )
        print(
            f"[TRACE][ROUTER][FALLBACK] trace_id={trace_id} "
            f"intent={result.get('primary_intent')} "
            f"confidence={result.get('confidence')} "
            f"reason=no_api_key"
        )
        return result

    # Check if last_turn context should be included (time-gated to 10 minutes)
    include_context = False
    elapsed_seconds = None
    if last_turn and isinstance(last_turn, dict):
        timestamp_utc_str = last_turn.get("timestamp_utc")
        if timestamp_utc_str:
            try:
                from datetime import datetime, timezone
                last_turn_time = datetime.fromisoformat(timestamp_utc_str)
                elapsed_seconds = (datetime.now(timezone.utc) - last_turn_time).total_seconds()
                if elapsed_seconds <= 600:  # 10 minute window
                    include_context = True
            except Exception:
                pass
    
    context_section = ""
    if include_context and last_turn:
        context_section = (
            f"\nContext from previous turn ({int(elapsed_seconds or 0)}s ago):\n"
            f"- User said: {last_turn.get('user_message', '')}\n"
            f"- System routed as: {last_turn.get('routed_intent', 'unknown')}\n"
            f"- Confidence: {last_turn.get('router_confidence', 'unknown')}\n\n"
            "Use this context to resolve ambiguous current messages:\n"
            "- If current message is acknowledgment ('theek hai', 'ok', 'haan', 'done', 'got it') after ANY intent, return SAME intent with confidence='continuation'\n"
            "- If current message is continuation ('aur', 'aur kuch', 'iske baad', 'and then') of last intent, return SAME intent with confidence='continuation'\n"
            "- If current message is completely unrelated to last_turn intent, ignore context and classify fresh\n"
            "- If time gap > 10 minutes, ignore context entirely\n"
        )
    
    system_prompt = (
        "You are Agent 2 Router for Apna Coach. Classify the message intent.\n"
        "Return ONLY JSON: {\"primary_intent\":\"...\",\"confidence\":\"high|medium|low|continuation\"}.\n"
        "Allowed primary_intent values exactly: "
        "burn_query, metric_explanation_query, food_recall_query, workout_request, plan_create_request, plan_status_query, plan_edit_request, plan_change_signal, nutrition_log, activity_log, historical_query, profile_update, general_chat.\n"
        "Allowed confidence values: high, medium, low, continuation.\n"
        + context_section +
        "Rules:\n"
        "- burn_query: asking burned calories/deficit/intake metrics.\n"
        "- metric_explanation_query: asking meaning/interpretation/safety of a metric (e.g. net deficit value).\n"
        "- food_recall_query: asks what user ate today or meal-wise today (breakfast/lunch/dinner).\n"
        "- workout_request: asks for workout plan/exercise prescription.\n"
        "- plan_create_request: asks for multi-day/week/month diet/workout plan.\n"
        "- plan_status_query: asks what current plan says for tomorrow/this week/next.\n"
        "- plan_edit_request: explicitly asks to edit or adjust existing plan.\n"
        "- plan_change_signal: life event impacting plan where user implies/asks plan adjustment (vacation, missed day, travel, schedule disruption).\n"
        "- nutrition_log: user logs food intake.\n"
        "- activity_log: user logs physical activity done.\n"
        "- historical_query: asks what was eaten/done on past day/date.\n"
        "- profile_update: user updates age/height/gender/weight/equipment/etc.\n"
        "- general_chat: everything else.\n"
        "Negative examples:\n"
        "- 'Aaj 2 roti khayi' is nutrition_log, not plan_create_request.\n"
        "- 'Next week plan dikhao' is plan_status_query, not workout_request.\n"
        "- 'Deficit 900 safe hai?' is metric_explanation_query, not burn_query.\n"
        "- 'I forgot to mention: I have knee pain' is profile_update unless user asks to change plan.\n"
        "Retry contract:\n"
        "- If retry_context is provided, you MUST fix prior failure reason and avoid repeating the same mistake.\n"
        "- Keep output concise and strictly schema-valid JSON."
    )

    def _observe(payload: dict[str, Any], response_text: str, elapsed_ms: int, response: object) -> None:
        usage = extract_gemini_usage(response)
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="router",
            stage="classify_intent",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response_text,
            usage=usage,
        )
    def _validate(raw: dict[str, Any]) -> dict[str, Any]:
        intent = str(raw.get("primary_intent") or "").strip()
        if intent not in ALLOWED_ROUTER_INTENTS:
            raise ValueError(f"invalid_intent:{intent or 'empty'}")
        confidence = str(raw.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"invalid_confidence:{confidence or 'empty'}")
        return normalize_router_result({"primary_intent": intent, "confidence": confidence})

    try:
        result = await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message},
            max_retries=MAX_ROUTER_RETRIES,
            validator=_validate,
            on_attempt_response=_observe,
        )
        log_agent_event(
            agent="router",
            stage="complete",
            trace_id=trace_id,
            details=result,
        )
        print(
            f"[TRACE][ROUTER] trace_id={trace_id} "
            f"intent={result.get('primary_intent')} "
            f"confidence={result.get('confidence')} "
            f"message_preview={user_message[:80]}"
        )
        return result
    except Exception as exc:  # noqa: BLE001
        previous_error = str(exc)
        # Check if this is a 429 rate limit error specifically
        error_lower = previous_error.lower()
        is_429 = (
            "429" in error_lower
            or "rate" in error_lower and "limit" in error_lower
            or "quota" in error_lower and "exceed" in error_lower
            or "resource" in error_lower and "exhausted" in error_lower
        )
        if is_429:
            log_agent_event(
                agent="router",
                stage="quota_exhausted",
                status="warn",
                trace_id=trace_id,
                details={"error": previous_error[:200]},
            )
            print(
                f"[TRACE][ROUTER][QUOTA_EXHAUSTED] trace_id={trace_id} "
                f"confidence=quota_exhausted error={previous_error[:100]}"
            )
            # Return special marker for webhooks.py to detect and send holding message
            return {"primary_intent": "general_chat", "confidence": "quota_exhausted"}
        
        log_agent_event(
            agent="router",
            stage="retry_failed",
            status="warn",
            trace_id=trace_id,
            details={"attempt": MAX_ROUTER_RETRIES, "reason": previous_error[:200]},
        )

    intent = _fallback_intent(user_message)
    result = {"primary_intent": intent, "confidence": "fallback"}
    log_agent_event(
        agent="router",
        stage="complete",
        status="llm_failed_fallback",
        trace_id=trace_id,
        details={"error": previous_error or "retries_exhausted", **result},
    )
    print(
        f"[TRACE][ROUTER][FALLBACK] trace_id={trace_id} "
        f"intent={result.get('primary_intent')} "
        f"confidence={result.get('confidence')} "
        f"reason=llm_failed"
    )
    return result
