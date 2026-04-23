from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import google.generativeai as genai

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL_3_1_FLASH,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)
from app.services.agent_trace import log_agent_event


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


async def ai_memory_clerk(
    user_message: str,
    current_profile: dict[str, Any],
    source_hint: str = "text",
    trace_id: str | None = None,
) -> dict[str, Any]:
    log_agent_event(
        agent="memory_clerk",
        stage="start",
        trace_id=trace_id,
        details={"source_hint": source_hint, "message_chars": len(user_message or "")},
    )
    if not GEMINI_API_KEY:
        print("[AI] GEMINI_API_KEY missing; skipping extraction for this message.")
        log_agent_event(
            agent="memory_clerk",
            stage="skipped",
            status="no_api_key",
            trace_id=trace_id,
        )
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
        "- If height is present, set physiology.biometrics.height as number in centimeters.\n"
        "- If age is present, set physiology.biometrics.age as integer in years.\n"
        "- If name is present, set identity.name.\n"
        "- If gender is present, set identity.gender (e.g., male/female).\n"
        "- If goal is present, set psychology.core_why.\n"
        "- If user mentions training setup (home/gym/park/travel), set "
        "lifestyle.training_environment.\n"
        "- If user mentions equipment (dumbbells, kettlebell, resistance band, "
        "pull-up bar, yoga mat, barbell, treadmill, full gym, etc), save as "
        "lifestyle.available_equipment string array.\n"
        "- Normalize equipment names into concise lowercase tokens.\n"
        "- Detect Food Logging Intent from text/transcript (e.g., 'khaya', 'ate', "
        "'breakfast/lunch/dinner', meal names like Butter Chicken, Dal Makhani, Poha).\n"
        "- If food logging intent is present, estimate calories/macros and return under "
        "logs.nutrition_log as an array with one or more entries.\n"
        "- Each nutrition entry must include: source, summary, estimated_calories, "
        "estimated_macros(protein_g, carbs_g, fat_g), confidence.\n"
        "- For source, use source_hint exactly if it is 'text' or 'voice'.\n"
        "- Detect Workout Completion intent from text/transcript (e.g., 'workout done', "
        "'training complete', 'aaj workout khatam', 'hit 15kg dumbbells').\n"
        "- If workout completion intent is present, set logs.current_day.workout_complete = true.\n"
        "- If exercises are mentioned, return logs.last_3_workout_summaries as array items "
        "with concise keys like date, summary, exercises, top_weight_kg (only when present).\n"
        "- If sets/reps/weights are mentioned, return logs.volume_trends as array items "
        "with keys like date, exercise, weight_kg, reps, sets (only include known values).\n"
        "- For workout log entries, use source as source_hint ('text' or 'voice').\n"
        "- Detect hydration intent (e.g., 'drank 2 liters water', '3 glass pani').\n"
        "- Convert hydration to liters. Assume 1 glass = 0.25 liters.\n"
        "- For hydration updates, return logs.current_day.water_liters_delta as a number "
        "to ADD to existing water value (do not return total replacement).\n"
        "- Detect activity logging intent (running, walking, gym, swimming, cycling, "
        "sports like badminton/pickleball/football, etc).\n"
        "- If activity is logged, return logs.activity_log as array of objects with keys: "
        "name, category(run|walk|cycling|swimming|strength_training|racquet_sport|team_sport|general_cardio), "
        "duration_mins, intensity(light|moderate|vigorous), met_score, rest_style(normal|no_rest|long_rest), "
        "confidence, assumption_note.\n"
        "- For strength training, if user does not specify rest style, default to rest_style='normal'.\n"
        "- If user says no rest/continuous workout, set rest_style='no_rest'.\n"
        "- If user is correcting a just-logged activity intensity/rest (e.g., 'no rest tha'), "
        "return logs.activity_adjustment object with keys like mode='recalculate_last' and "
        "rest_style='no_rest' so backend can recompute instead of double-counting.\n"
        "- Never remove existing logs. Return only additive updates.\n"
        "- If information is missing, do not invent it and do not include that key."
    )

    model_input = {
        "current_profile": current_profile,
        "user_message": user_message,
        "source_hint": source_hint,
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
        log_agent_event(
            agent="memory_clerk",
            stage="complete",
            trace_id=trace_id,
            details={"update_keys": sorted(parsed.keys())},
        )
        return parsed

    return await asyncio.to_thread(_call_model)


def _download_media_bytes(media_url: str) -> bytes:
    request = urllib.request.Request(media_url, method="GET")
    # Twilio media URLs may require account auth.
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        credentials = f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")
        auth_header = base64.b64encode(credentials).decode("ascii")
        request.add_header("Authorization", f"Basic {auth_header}")

    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


async def ai_nutrition_from_image(
    image_url: str, current_profile: dict[str, Any], trace_id: str | None = None
) -> dict[str, Any]:
    """
    Brain A vision extension:
    Estimate meal nutrition from image and return a normalized food log object.
    """
    if not GEMINI_API_KEY:
        print("[AI Vision] GEMINI_API_KEY missing; skipping image nutrition extraction.")
        log_agent_event(
            agent="nutritionist",
            stage="vision_skipped",
            status="no_api_key",
            trace_id=trace_id,
        )
        return {}
    log_agent_event(
        agent="nutritionist",
        stage="vision_start",
        trace_id=trace_id,
    )

    system_prompt = (
        "You are the Memory Clerk for Apna Coach. Analyze the food image and return "
        "ONLY a JSON object with estimated nutrition details.\n\n"
        "Return format:\n"
        "{\n"
        '  "food_log_entry": {\n'
        '    "source": "image",\n'
        '    "summary": "short description of meal",\n'
        '    "estimated_calories": number,\n'
        '    "estimated_macros": {"protein_g": number, "carbs_g": number, "fat_g": number},\n'
        '    "confidence": "low|medium|high"\n'
        "  }\n"
        "}\n"
        "Do not output markdown. Do not include any extra text."
    )

    def _call_model() -> dict[str, Any]:
        started_at = time.perf_counter()
        print(f"[AI Vision] Calling Gemini model: {GEMINI_MODEL_3_1_FLASH}")
        image_bytes = _download_media_bytes(image_url)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL_3_1_FLASH,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            [
                {
                    "mime_type": "image/jpeg",
                    "data": image_bytes,
                },
                json.dumps({"current_profile": current_profile}, ensure_ascii=False),
            ],
            generation_config={"response_mime_type": "application/json"},
        )
        response_text = response.text or "{}"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        print(
            f"[AI Vision] Response received in {elapsed_ms} ms "
            f"(chars={len(response_text)})"
        )
        parsed = extract_json_from_model_text(response_text)
        print(f"[AI Vision] Parsed keys: {sorted(parsed.keys())}")
        log_agent_event(
            agent="nutritionist",
            stage="vision_complete",
            trace_id=trace_id,
            details={"parsed_keys": sorted(parsed.keys())},
        )
        return parsed

    return await asyncio.to_thread(_call_model)


async def ai_transcribe_voice_note(
    media_url: str,
    media_content_type: str = "audio/ogg",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """
    Brain A audio extension:
    Transcribe WhatsApp voice note media into text for downstream brains.
    """
    if not GEMINI_API_KEY:
        print("[AI Audio] GEMINI_API_KEY missing; skipping voice transcription.")
        log_agent_event(
            agent="memory_clerk",
            stage="voice_transcribe_skipped",
            status="no_api_key",
            trace_id=trace_id,
        )
        return {}
    log_agent_event(
        agent="memory_clerk",
        stage="voice_transcribe_start",
        trace_id=trace_id,
    )

    system_prompt = (
        "You are the Memory Clerk for Apna Coach. Transcribe the user's voice note "
        "accurately and return ONLY JSON in this format:\n"
        '{ "transcript": "..." }\n'
        "No markdown. No extra keys unless needed."
    )

    def _call_model() -> dict[str, Any]:
        started_at = time.perf_counter()
        print(f"[AI Audio] Calling Gemini model: {GEMINI_MODEL_3_1_FLASH}")
        audio_bytes = _download_media_bytes(media_url)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL_3_1_FLASH,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            [
                {"mime_type": media_content_type, "data": audio_bytes},
            ],
            generation_config={"response_mime_type": "application/json"},
        )
        response_text = response.text or "{}"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        print(
            f"[AI Audio] Response received in {elapsed_ms} ms "
            f"(chars={len(response_text)})"
        )
        parsed = extract_json_from_model_text(response_text)
        print(f"[AI Audio] Parsed keys: {sorted(parsed.keys())}")
        transcript_preview = str(parsed.get("transcript") or "").strip()
        if transcript_preview:
            if len(transcript_preview) > 400:
                transcript_preview = transcript_preview[:400] + "..."
            print(f"[AI Audio] Transcript: {transcript_preview}")
        log_agent_event(
            agent="memory_clerk",
            stage="voice_transcribe_complete",
            trace_id=trace_id,
            details={"has_transcript": bool(transcript_preview)},
        )
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

