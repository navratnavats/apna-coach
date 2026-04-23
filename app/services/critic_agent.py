from __future__ import annotations

import asyncio
import json

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.agent_trace import log_agent_event


async def run_critic_agent(
    draft_text: str,
    *,
    source: str = "generic",
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

    system_prompt = (
        "You are Critic_Agent for Apna Coach. You are the final quality gate before "
        "a WhatsApp message is sent.\n"
        "Rules:\n"
        "- Enforce Bhai/Hinglish persona strictly.\n"
        "- Remove AI-speak like: 'As an AI', 'Here is a plan', 'In conclusion', "
        "'Important note'.\n"
        "- Keep it short and punchy for WhatsApp.\n"
        "- Max 3 short paragraphs OR short bullet points only.\n"
        "- Keep practical meaning intact; do not drop critical safety warnings.\n"
        "- Use natural WhatsApp style, short lines, and light emoji usage.\n"
        "- Output plain text only."
    )

    model_input = {"source": source, "draft_message": original}

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
