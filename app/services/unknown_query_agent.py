from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
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

    def _call_model(payload: dict[str, Any]) -> dict[str, Any]:
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
            agent="unknown_query_agent",
            stage="clarify_intent",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        raw = json.loads((response.text or "{}").strip())
        if not isinstance(raw, dict):
            raise ValueError("invalid_json_object")
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

    previous_error = ""
    previous_output: dict[str, Any] = {}
    for attempt in range(1, MAX_UNKNOWN_RETRIES + 1):
        payload = {
            "user_message": user_message,
            "retry_context": (
                {
                    "attempt": attempt,
                    "previous_failure_reason": previous_error,
                    "previous_output": previous_output,
                    "instruction": "Fix prior output and return strict schema-valid JSON.",
                }
                if attempt > 1
                else {}
            ),
        }
        try:
            result = await asyncio.to_thread(_call_model, payload)
            if result == previous_output and attempt > 1:
                raise ValueError("repeated_same_output")
            log_agent_event(
                agent="unknown_query_agent",
                stage="complete",
                trace_id=trace_id,
                details={"mode": "clarifier", "safe_mode": result.get("safe_mode")},
            )
            return result
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)
            previous_output = previous_output or {}

    payload = fallback_payload
    log_agent_event(
        agent="unknown_query_agent",
        stage="complete",
        trace_id=trace_id,
        details={"mode": "clarifier_fallback", "safe_mode": payload.get("safe_mode")},
    )
    return payload
