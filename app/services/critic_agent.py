from __future__ import annotations

import asyncio
import json
import time

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event
from app.services.observability_async import enqueue_llm_call_event, extract_gemini_usage
from app.services.persona import resolve_user_address


async def run_critic_agent(
    draft_text: str,
    *,
    source: str = "generic",
    living_profile: dict | None = None,
    trace_id: str | None = None,
) -> str:
    """
    Agent 9 (Critic):
    Final quality-control rewrite for WhatsApp vibe/length.
    Falls back to original draft on any failure.
    """
    original = (draft_text or "").strip()
    if not original:
        return original
    log_agent_event(
        agent="critic",
        stage="start",
        trace_id=trace_id,
        details={"source": source, "chars_before": len(original)},
    )
    if not GEMINI_API_KEY:
        log_agent_event(
            agent="critic",
            stage="bypass",
            status="no_api_key",
            trace_id=trace_id,
        )
        return original

    address = "Buddy"
    if isinstance(living_profile, dict):
        address = resolve_user_address(living_profile)

    system_prompt = (
        "You are Critic_Agent for Apna Coach. You are the final quality gate before "
        "a WhatsApp message is sent.\n"
        "Rules:\n"
        f"- Enforce user-preferred address token '{address}' and Hinglish persona.\n"
        "- Remove AI-speak like: 'As an AI', 'Here is a plan', 'In conclusion', "
        "'Important note'.\n"
        "- Keep it short and punchy for WhatsApp.\n"
        "- Max 3 short paragraphs OR short bullet points only.\n"
        "- Keep practical meaning intact; do not drop critical safety warnings.\n"
        "- Use natural WhatsApp style, short lines, and light emoji usage.\n"
        "- Keep tone respectful and warm. Prefer 'aap' phrasing over rude/slangy 'tu/tera' unless user explicitly asked for that style.\n"
        "- Avoid sounding aggressive, sarcastic, or dismissive.\n"
        "- PRESERVE conversational warmth markers like 'bhai', 'yaar', 'dekho', 'achha', 'tension mat le', 'solid hai', 'chal raha hai', 'bilkul', 'perfect', 'chinta mat karo'. Do NOT strip these in the name of brevity. A warm short response is better than a cold short response.\n"
        "- Output plain text only."
    )

    model_input = {"source": source, "draft_message": original}

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
            agent="critic",
            stage="rewrite_response",
            model=GEMINI_COACH_MODEL,
            latency_ms=elapsed_ms,
            request_payload=model_input,
            response_text=response.text or "",
            usage=extract_gemini_usage(response),
        )
        return (response.text or "").strip()

    try:
        polished = await asyncio.to_thread(_call_model)
        final_text = polished or original
        log_agent_event(
            agent="critic",
            stage="complete",
            trace_id=trace_id,
            details={"chars_before": len(original), "chars_after": len(final_text)},
        )
        return final_text
    except Exception as exc:  # noqa: BLE001
        print(f"[Critic Agent] Rewrite failed: {exc}")
        log_agent_event(
            agent="critic",
            stage="error",
            status="fallback_original",
            trace_id=trace_id,
            details={"error": str(exc)},
        )
        return original
