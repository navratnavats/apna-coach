from __future__ import annotations

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.llm_contract_runner import run_json_contract
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage

MAX_CAPABILITY_INTENT_RETRIES = 2


def should_check_capability_intent_with_llm(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    # Avoid extra hop for plain logs.
    pure_log_markers = (
        "i had ",
        "khaya",
        "ate ",
        "workout done",
        "run ",
        "walk ",
        "steps",
    )
    if any(m in text for m in pure_log_markers):
        return False
    return True


async def classify_capability_discovery_intent(
    user_message: str,
    *,
    trace_id: str | None = None,
) -> tuple[bool, str]:
    if not GEMINI_API_KEY:
        return False, "no_api_key"

    system_prompt = (
        "You classify if user is asking app capabilities/features/help scope.\n"
        "Return ONLY JSON: {\"is_capability_query\":true|false,\"confidence\":\"high|medium|low\"}.\n"
        "Mark true for asks like: what can you do, can this app do X, feature list, help scope.\n"
        "Mark false for normal logging/coaching requests."
    )

    def _observe(payload: dict[str, str], response_text: str, elapsed_ms: int, response: object) -> None:
        enqueue_llm_call_event(
            operation_id=trace_id,
            trace_id=trace_id,
            turn_id=None,
            phone_number=None,
            agent="capability_intent_agent",
            stage="classify_capability_intent",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=payload,
            response_text=response_text,
            usage=extract_gemini_usage(response),
        )
    def _validate(raw: dict[str, object]) -> tuple[bool, str]:
        is_capability = bool(raw.get("is_capability_query"))
        confidence = str(raw.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid_confidence")
        return is_capability, confidence

    try:
        is_capability, confidence = await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message},
            max_retries=MAX_CAPABILITY_INTENT_RETRIES,
            validator=_validate,
            on_attempt_response=_observe,
        )
        return is_capability and confidence != "low", confidence
    except Exception:  # noqa: BLE001
        return False, "failed"
