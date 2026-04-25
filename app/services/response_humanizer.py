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
        "5. No AI-speak: no 'Here is', 'As an AI', 'In conclusion', 'Important note'\n"
        "6. Plain text only — no markdown, no formatting, no emojis unless the original had them\n"
        "7. If user asked something unrelated during onboarding, acknowledge their question warmly first, "
        "then gently redirect to pending profile fields\n\n"
        "LENGTH & GROUPING (CRITICAL):\n"
        "- WhatsApp messages should feel bite-sized, not overwhelming.\n"
        "- If original message has MANY pending fields (5+), intelligently GROUP them:\n"
        "  * Group 1: Identity (name, age, gender) — ask these together\n"
        "  * Group 2: Biometrics (height, weight, target weight) — ask these together\n"
        "  * Group 3: Context (injuries, equipment, training location) — ask these together\n"
        "- Ask for ONE group at a time in your rewrite. Example: 'Pehle aapka naam, age aur gender bata do.'\n"
        "- If original is a simple message (1-3 items), keep it conversational and concise.\n"
        "- Target: Keep response under 500 characters when possible. If original has too much info, "
        "prioritize the FIRST group and warmly mention 'baaki details baad mein puchhunga'.\n"
        "- NEVER output bullet lists or numbered lists. Make it feel like natural speech.\n\n"
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
        
        # Only fallback if LLM returns empty (which should be rare with good prompts)
        if not result:
            log_agent_event(
                agent="humanizer",
                stage="empty_response",
                status="fallback",
                trace_id=trace_id,
            )
            print(f"[Humanizer][FALLBACK] Empty LLM response, returning original")
            return original
        
        # Trust the LLM completely for length and formatting decisions
        # No hard-coded guards - LLM handles grouping, length, and flow
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
