from __future__ import annotations

from typing import Any

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.llm_contract_runner import run_json_contract
from app.services.messages import unknown_query_clarifier
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address

MAX_UNKNOWN_RETRIES = 3
ALLOWED_SAFE_MODE = {"strict", "guided"}


def should_trigger_unknown_query_clarifier(
    *,
    routed_intent: str,
    router_confidence: str,
) -> bool:
    return (
        str(routed_intent or "").strip().lower() == "general_chat"
        and str(router_confidence or "").strip().lower() == "fallback"
    )


async def build_unknown_intent_contract(
    living_profile: dict[str, Any],
    user_message: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    address = resolve_user_address(living_profile)
    fallback_payload = {
        "clarify_question": unknown_query_clarifier(address),
        "suggested_options": [
            "aaj ka burn/deficit numbers",
            "aaj kya khaya (food recall)",
            "workout plan",
            "existing plan edit/status",
            "profile update",
        ],
        "safe_mode": "strict",
    }
    if not GEMINI_API_KEY:
        return fallback_payload

    system_prompt = (
        "You are Unknown Intent Handler for Apna Coach. "
        "When router fails and intent is unclear, ask one concise clarification question.\n"
        "Return ONLY strict JSON with this schema:\n"
        "{"
        "\"clarify_question\":\"string\","
        "\"suggested_options\":[\"string\",\"string\",\"string\"],"
        "\"safe_mode\":\"strict|guided\""
        "}\n"
        "Rules:\n"
        "- Keep clarify_question under 220 chars.\n"
        "- suggested_options must have 3 to 5 short options.\n"
        "- No diagnosis, no unsafe guidance.\n"
        "Negative examples:\n"
        "- Do not return markdown/bullets outside JSON.\n"
        "- Do not return profile updates.\n"
        "- Do not ask multiple unrelated questions.\n"
        "Retry contract:\n"
        "- If retry_context exists, fix previous schema/output issue and do not repeat it."
    )

    def _observe(payload: dict[str, Any], response_text: str, elapsed_ms: int, response: object) -> None:
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="unknown_query_agent",
            stage="clarify_intent",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response_text,
            usage=extract_gemini_usage(response),
        )
    def _validate(raw: dict[str, Any]) -> dict[str, Any]:
        question = str(raw.get("clarify_question") or "").strip()
        if len(question) < 8:
            raise ValueError("invalid_clarify_question")
        options = raw.get("suggested_options")
        if not isinstance(options, list):
            raise ValueError("invalid_suggested_options")
        normalized_options = [str(item).strip() for item in options if str(item).strip()]
        if len(normalized_options) < 3:
            raise ValueError("insufficient_options")
        safe_mode = str(raw.get("safe_mode") or "").strip().lower()
        if safe_mode not in ALLOWED_SAFE_MODE:
            raise ValueError("invalid_safe_mode")
        return {
            "clarify_question": question[:220],
            "suggested_options": normalized_options[:5],
            "safe_mode": safe_mode,
        }

    try:
        result = await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message},
            max_retries=MAX_UNKNOWN_RETRIES,
            validator=_validate,
            on_attempt_response=_observe,
        )
        log_agent_event(
            agent="unknown_query_agent",
            stage="complete",
            trace_id=trace_id,
            details={"mode": "clarifier", "safe_mode": result.get("safe_mode")},
        )
        return result
    except Exception:  # noqa: BLE001
        pass

    payload = fallback_payload
    log_agent_event(
        agent="unknown_query_agent",
        stage="complete",
        trace_id=trace_id,
        details={"mode": "clarifier_fallback", "safe_mode": payload.get("safe_mode")},
    )
    return payload
