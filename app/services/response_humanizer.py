from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address


async def humanize_response(
    *,
    intent_text: str,
    living_profile: dict,
    last_user_message: str = "",
    trace_id: str | None = None,
    conversation_history: str = "",
) -> str:
    """
    Agent: Response Humanizer
    
    Rewrites static system messages into warm, conversational, language-matched WhatsApp messages.
    
    The "father test" — if a 60-year-old Indian man who isn't tech-savvy can understand and 
    respond without confusion, we've succeeded.
    
    Falls back to original intent_text on ANY exception. Never crashes.
    """
    original = (intent_text or "").strip()
    if not original:
        return original
    
    log_agent_event(
        agent="humanizer",
        stage="start",
        trace_id=trace_id,
        details={
            "chars_original": len(original),
            "has_history": bool(conversation_history),
        },
    )
    print(f"[Humanizer][REQUEST] trace_id={trace_id} original_len={len(original)} text={original[:200]}")
    
    if not GEMINI_API_KEY:
        log_agent_event(
            agent="humanizer",
            stage="bypass",
            status="no_api_key",
            trace_id=trace_id,
        )
        return original
    
    address = resolve_user_address(living_profile)
    identity = living_profile.get("identity") or {}
    language_mix = str(identity.get("language_mix") or "hinglish").strip().lower()
    
    history_context = ""
    if conversation_history:
        history_context = f"\n{conversation_history}\n\n"
    
    system_prompt = (
        "You are Apna Coach — a warm, patient Indian fitness coach on WhatsApp.\n\n"
        + history_context
        + "The system needs to communicate this to the user:\n"
        f"{original}\n\n"
        f"The user's last message was: {last_user_message}\n"
        f"Address them as: {address}\n"
        f"Their language preference: {language_mix}\n\n"
        "Rules:\n"
        "1. Rewrite as a natural, warm, conversational WhatsApp message\n"
        "2. Match their language exactly — Hinglish stays Hinglish, English stays English, Hindi stays Hindi\n"
        "3. Sound like a helpful human coach, never a form or system notification\n"
        "4. If this is onboarding context, explain WHY you need the info — not just WHAT\n"
        "5. Max 4 short lines (not sentences — lines). No bullet lists unless absolutely necessary\n"
        "6. No AI-speak: no 'Here is', 'As an AI', 'In conclusion', 'Important note'\n"
        "7. Plain text only — no markdown, no formatting\n"
        "8. If user asked something unrelated during onboarding, acknowledge their question warmly first, "
        "then gently redirect to pending profile fields\n"
        "9. Group related pending fields naturally — ask for identity fields together (name, age, gender), "
        "biometric fields together (height, weight, target), and context fields together (injuries, equipment). "
        "Never ask for more than one group at a time. Make it feel like one natural question, not a list.\n\n"
        "Output the humanized message only."
    )
    
    model_input = {
        "original_text": original,
        "user_message": last_user_message,
        "address": address,
        "language": language_mix,
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
            agent="humanizer",
            stage="rewrite_static_message",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=model_input,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        return (response.text or "").strip()
    
    try:
        result = await asyncio.to_thread(_call_model)
        if not result:
            log_agent_event(
                agent="humanizer",
                stage="empty_response",
                status="fallback",
                trace_id=trace_id,
            )
            return original
        
        # Relative length guard: if humanized result is >30% longer than original 
        # and original was already short, reject it — original was fine
        if len(result) > len(original) * 1.3 and len(original) < 400:
            log_agent_event(
                agent="humanizer",
                stage="length_guard_relative",
                status="fallback",
                trace_id=trace_id,
                details={"original_len": len(original), "result_len": len(result)},
            )
            return original
        
        # Absolute ceiling: WhatsApp messages over 500 chars feel like essays
        if len(result) > 500:
            log_agent_event(
                agent="humanizer",
                stage="length_guard_absolute",
                status="fallback",
                trace_id=trace_id,
                details={"result_len": len(result)},
            )
            return original
        
        log_agent_event(
            agent="humanizer",
            stage="complete",
            trace_id=trace_id,
            details={"chars_before": len(original), "chars_after": len(result)},
        )
        print(f"[Humanizer][RESPONSE] trace_id={trace_id} result_len={len(result)} text={result[:200]}")
        return result
    
    except Exception as exc:  # noqa: BLE001
        print(f"[Humanizer] Rewrite failed: {exc}")
        log_agent_event(
            agent="humanizer",
            stage="error",
            status="fallback_original",
            trace_id=trace_id,
            details={"error": str(exc)},
        )
        return original
