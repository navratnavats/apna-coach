from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_MODEL_3_1_FLASH


def load_default_living_profile() -> dict[str, Any]:
    root_dir = Path(__file__).resolve().parents[2]
    rich_state_path = root_dir / "docs" / "RICH_USER_STATE.md"
    raw = rich_state_path.read_text(encoding="utf-8")

    start_marker = "```json"
    end_marker = "```"
    start_idx = raw.find(start_marker)
    if start_idx == -1:
        raise RuntimeError("docs/RICH_USER_STATE.md is missing a ```json block.")

    json_start = start_idx + len(start_marker)
    end_idx = raw.find(end_marker, json_start)
    if end_idx == -1:
        raise RuntimeError("docs/RICH_USER_STATE.md has an unclosed JSON code block.")

    json_blob = raw[json_start:end_idx].strip()
    return json.loads(json_blob)


def deep_merge_profile(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_profile(merged[key], value)
        else:
            merged[key] = value
    return merged


def extract_json_from_model_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Model output JSON must be an object.")
    return parsed


async def ai_memory_clerk(user_message: str, current_profile: dict[str, Any]) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        print("[AI] GEMINI_API_KEY missing; skipping extraction for this message.")
        return {}

    system_prompt = (
        "You are the Memory Clerk for Apna Coach. Analyze the user message and "
        "extract Name, Weight, Injuries, or Goals. Return ONLY a JSON object "
        "representing the updates needed to the Living User Profile. Do not "
        "hallucinate data.\n\n"
        "JSON rules:\n"
        "- Output must be valid JSON object only.\n"
        "- Use existing schema keys.\n"
        "- If injuries are present, return under physiology.injuries as an array "
        "of objects with keys: part, severity, history, pain_triggers.\n"
        "- If weight is present, set physiology.biometrics.weight as number in kg.\n"
        "- If name is present, set identity.name.\n"
        "- If goal is present, set psychology.core_why.\n"
        "- If information is missing, do not invent it and do not include that key."
    )

    model_input = {
        "current_profile": current_profile,
        "user_message": user_message,
    }

    def _call_model() -> dict[str, Any]:
        started_at = time.perf_counter()
        print(f"[AI] Calling Gemini model: {GEMINI_MODEL_3_1_FLASH}")
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL_3_1_FLASH, system_instruction=system_prompt
        )
        response = model.generate_content(
            json.dumps(model_input, ensure_ascii=False),
            generation_config={"response_mime_type": "application/json"},
        )
        response_text = response.text or "{}"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[AI] Response received in {elapsed_ms} ms (chars={len(response_text)})")
        parsed = extract_json_from_model_text(response_text)
        print(f"[AI] Parsed update keys: {sorted(parsed.keys())}")
        return parsed

    return await asyncio.to_thread(_call_model)


def next_onboarding_prompt(profile: dict[str, Any]) -> str:
    identity = profile.get("identity") or {}
    physiology = profile.get("physiology") or {}
    psychology = profile.get("psychology") or {}
    biometrics = physiology.get("biometrics") or {}
    injuries = physiology.get("injuries") or []

    raw_name = str(identity.get("name") or "").strip()
    name_missing = raw_name == "" or raw_name.lower() == "string"

    try:
        weight_missing = float(biometrics.get("weight", 0) or 0) <= 0
    except (TypeError, ValueError):
        weight_missing = True

    injuries_missing = not isinstance(injuries, list) or len(injuries) == 0
    core_why_missing = str(psychology.get("core_why") or "").strip() == ""

    if name_missing:
        return "Bhai, what is your name?"
    if weight_missing:
        return "To give you the best coaching, what is your current weight (in kg)?"
    if injuries_missing:
        return (
            'Do you have any current injuries I should know about? (Type "None" if you are 100% fit)'
        )
    if core_why_missing:
        return (
            "Finally, what is your main goal? (e.g., Fat loss for a wedding, "
            "building muscle, or just staying active?)"
        )
    return (
        "Bhai, your profile is now 100% complete! Give me 5 seconds to analyze "
        "your stats and create your personalized coaching plan... 🦾"
    )

