from __future__ import annotations

import asyncio
import json
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL

ALLOWED_MODES = {"push", "support", "simplify", "celebrate"}
ALLOWED_STYLES = {"gentle_nudge", "strict_accountability", "empathetic", "challenge"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _keyword_fallback(user_message: str) -> dict[str, Any]:
    text = str(user_message or "").strip().lower()
    quit_risk = any(
        k in text for k in ("quit", "chhod", "nahi hoga", "skip", "missed", "burnout", "tired")
    )
    celebrate = any(k in text for k in ("done", "completed", "hit", "great", "acha gaya", "pr"))
    if celebrate:
        return {
            "engagement_delta": 0.05,
            "motivation_style_suggested": "challenge",
            "quit_signal_detected": False,
            "quit_signal_text": "",
            "response_mode": "celebrate",
            "confidence": "fallback",
        }
    if quit_risk:
        return {
            "engagement_delta": -0.06,
            "motivation_style_suggested": "empathetic",
            "quit_signal_detected": True,
            "quit_signal_text": text[:120],
            "response_mode": "support",
            "confidence": "fallback",
        }
    return {
        "engagement_delta": 0.01,
        "motivation_style_suggested": "gentle_nudge",
        "quit_signal_detected": False,
        "quit_signal_text": "",
        "response_mode": "push",
        "confidence": "fallback",
    }


async def analyze_psychology_signals(
    *,
    user_message: str,
    living_profile: dict[str, Any],
) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        return _keyword_fallback(user_message)

    system_prompt = (
        "You are Psychology Agent for a fitness coach app. Analyze user message and context.\n"
        "Return ONLY JSON with keys exactly:\n"
        "{"
        "\"engagement_delta\": number between -0.10 and 0.10,"
        "\"motivation_style_suggested\":\"gentle_nudge|strict_accountability|empathetic|challenge\","
        "\"quit_signal_detected\": true/false,"
        "\"quit_signal_text\":\"short text or empty\","
        "\"response_mode\":\"push|support|simplify|celebrate\","
        "\"confidence\":\"high|medium|low\""
        "}\n"
        "Be conservative. Do not hallucinate."
    )

    payload = {"user_message": user_message, "psychology": living_profile.get("psychology") or {}}

    def _call() -> dict[str, Any]:
        model = genai.GenerativeModel(
            model_name=GEMINI_COACH_MODEL,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            json.dumps(payload, ensure_ascii=False),
            generation_config={"response_mime_type": "application/json"},
        )
        raw = json.loads((response.text or "{}").strip())
        if not isinstance(raw, dict):
            return _keyword_fallback(user_message)
        return raw

    try:
        raw = await asyncio.to_thread(_call)
    except Exception:
        return _keyword_fallback(user_message)

    engagement_delta = _clamp(float(raw.get("engagement_delta") or 0.0), -0.10, 0.10)
    style = str(raw.get("motivation_style_suggested") or "gentle_nudge").strip().lower()
    if style not in ALLOWED_STYLES:
        style = "gentle_nudge"
    mode = str(raw.get("response_mode") or "push").strip().lower()
    if mode not in ALLOWED_MODES:
        mode = "push"
    quit_signal_detected = bool(raw.get("quit_signal_detected"))
    quit_signal_text = str(raw.get("quit_signal_text") or "").strip()[:180]
    confidence = str(raw.get("confidence") or "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "engagement_delta": engagement_delta,
        "motivation_style_suggested": style,
        "quit_signal_detected": quit_signal_detected,
        "quit_signal_text": quit_signal_text,
        "response_mode": mode,
        "confidence": confidence,
    }


def apply_psychology_update(
    living_profile: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    profile = dict(living_profile or {})
    psychology = profile.get("psychology") or {}
    current_score = float(psychology.get("engagement_score") or 0.5)
    delta = float(analysis.get("engagement_delta") or 0.0)
    psychology["engagement_score"] = round(_clamp(current_score + delta, 0.0, 1.0), 3)

    style = str(analysis.get("motivation_style_suggested") or "").strip().lower()
    if style in ALLOWED_STYLES:
        psychology["motivation_style"] = style

    quit_signals = psychology.get("quit_signals") or []
    if not isinstance(quit_signals, list):
        quit_signals = []
    if bool(analysis.get("quit_signal_detected")):
        signal = str(analysis.get("quit_signal_text") or "").strip()
        if signal:
            quit_signals.append(signal)
    if len(quit_signals) > 8:
        quit_signals = quit_signals[-8:]
    psychology["quit_signals"] = quit_signals

    profile["psychology"] = psychology
    return profile

