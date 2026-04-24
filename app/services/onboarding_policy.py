from __future__ import annotations

import asyncio
from typing import Any

from app.clients import gemini_client  # noqa: F401 - side-effect config
from app.config import GEMINI_API_KEY, GEMINI_COACH_MODEL
from app.services.llm_contract_runner import run_json_contract

MAX_ONBOARDING_POLICY_RETRIES = 3
ALLOWED_DECISIONS = {"allow", "redirect"}
ALLOWED_CONTROL_INTENTS = {"none", "help", "status", "left", "explain", "edit", "restart"}
MAX_CONTROL_RETRIES = 3
ONBOARDING_FIELD_SET = {
    "name",
    "age",
    "height",
    "weight",
    "target_weight",
    "gender",
    "core_why",
    "injuries",
    "equipment",
}


async def evaluate_onboarding_message(
    *,
    user_message: str,
    pending_fields: list[str],
    has_media: bool,
) -> dict[str, Any]:
    text = str(user_message or "").strip().lower()
    if has_media:
        return {"decision": "allow", "reason": "has_media"}
    if not text:
        return {"decision": "redirect", "reason": "empty_input"}

    deny_markers = (
        "ignore previous instructions",
        "system prompt",
        "jailbreak",
        "token",
        "api key",
        "hack",
        "sql injection",
    )
    if any(marker in text for marker in deny_markers):
        return {"decision": "redirect", "reason": "unsafe_or_irrelevant"}

    if GEMINI_API_KEY:
        llm_result = await _evaluate_with_llm(
            user_message=user_message,
            pending_fields=pending_fields,
        )
        if llm_result is not None:
            return llm_result

    # Deterministic fallback when LLM is unavailable or fails.
    pending = [f for f in pending_fields if f in ONBOARDING_FIELD_SET]
    signal_map = {
        "name": ("my name is", "i am ", "call me", "naam", "name"),
        "age": ("years old", "yr", "age", "umar"),
        "height": ("cm", "ft", "feet", "height", "lamba"),
        "weight": ("kg", "kilo", "weight", "wazan"),
        "target_weight": ("target", "goal weight", "kg", "kilo"),
        "gender": ("male", "female", "man", "woman", "ladka", "ladki"),
        "core_why": ("goal", "because", "wedding", "run", "fat loss", "muscle"),
        "injuries": ("injury", "pain", "knee", "back", "none", "no injury"),
        "equipment": ("dumbbell", "band", "gym", "home", "equipment", "barbell"),
    }
    matched_fields: list[str] = []
    for field in pending:
        markers = signal_map.get(field, ())
        if any(marker in text for marker in markers):
            matched_fields.append(field)
    if matched_fields:
        return {
            "decision": "allow",
            "reason": "deterministic_matched_pending_fields",
            "matched_fields": matched_fields,
        }

    return {"decision": "redirect", "reason": "does_not_match_pending_fields"}


async def _evaluate_with_llm(
    *,
    user_message: str,
    pending_fields: list[str],
) -> dict[str, Any] | None:
    pending = [f for f in pending_fields if f in ONBOARDING_FIELD_SET]
    if not pending:
        return {"decision": "allow", "reason": "no_pending_fields", "matched_fields": []}

    system_prompt = (
        "You are Onboarding Policy Evaluator for a fitness app.\n"
        "Goal: decide if user's latest message contains actual value updates for pending onboarding fields.\n"
        "Return ONLY JSON with schema:\n"
        "{"
        "\"decision\":\"allow|redirect\","
        "\"reason\":\"short_snake_case\","
        "\"matched_fields\":[\"name|age|height|weight|target_weight|gender|core_why|injuries|equipment\"],"
        "\"confidence\":\"high|medium|low\""
        "}\n"
        "Critical rules:\n"
        "- Match field only when a concrete value is present.\n"
        "- Ignore bare labels/list headings like 'Name, Age, Height' without user values.\n"
        "- If user gives at least one concrete value for a pending field, decision should be allow.\n"
        "- If no pending field value is present, decision should be redirect.\n"
        "- Keep strict JSON only."
    )

    def _validate(raw: dict[str, Any]) -> dict[str, Any]:
        decision = str(raw.get("decision") or "").strip().lower()
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("invalid_decision")
        fields = raw.get("matched_fields") or []
        if not isinstance(fields, list):
            raise ValueError("invalid_matched_fields")
        normalized = []
        for item in fields:
            field = str(item).strip().lower()
            if field in ONBOARDING_FIELD_SET and field in pending:
                normalized.append(field)
        return {
            "decision": "allow" if normalized else decision,
            "reason": str(raw.get("reason") or "llm_onboarding_policy").strip().lower(),
            "matched_fields": sorted(set(normalized)),
            "confidence": str(raw.get("confidence") or "low").strip().lower(),
        }

    try:
        return await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message, "pending_fields": pending},
            max_retries=MAX_ONBOARDING_POLICY_RETRIES,
            validator=_validate,
        )
    except Exception:  # noqa: BLE001
        return None


async def classify_onboarding_control_intent(
    *,
    user_message: str,
    pending_fields: list[str],
) -> dict[str, Any]:
    text = str(user_message or "").strip()
    lowered = text.lower()
    if not text:
        return {"intent": "none", "confidence": "high", "reason": "empty_input"}

    if GEMINI_API_KEY:
        llm_result = await _classify_control_with_llm(
            user_message=text,
            pending_fields=pending_fields,
        )
        if llm_result is not None:
            return llm_result

    # Deterministic fallback: only allow strict standalone control commands.
    strict_commands = {
        "help": {"help", "onboarding help"},
        "status": {"status", "onboarding status"},
        "left": {"left", "what is left", "kya bacha"},
        "explain": {"explain", "is step ka matlab", "what does this step mean"},
        "edit": {"edit", "onboarding edit"},
        "restart": {"restart", "onboarding restart", "reset onboarding"},
    }
    normalized = " ".join(lowered.split())
    for intent, phrases in strict_commands.items():
        if normalized in phrases:
            return {"intent": intent, "confidence": "fallback", "reason": "strict_command_match"}
    return {"intent": "none", "confidence": "fallback", "reason": "no_strict_command_match"}


async def _classify_control_with_llm(
    *,
    user_message: str,
    pending_fields: list[str],
) -> dict[str, Any] | None:
    pending = [f for f in pending_fields if f in ONBOARDING_FIELD_SET]
    system_prompt = (
        "You classify onboarding CONTROL intent only.\n"
        "Return ONLY JSON with schema:\n"
        "{"
        "\"intent\":\"none|help|status|left|explain|edit|restart\","
        "\"confidence\":\"high|medium|low\","
        "\"reason\":\"short_snake_case\""
        "}\n"
        "Rules:\n"
        "- Queries about app features/capabilities ('what can you do', 'features batao', 'app kya kya karta hai') are NOT onboarding control; set intent='none'.\n"
        "- If message contains onboarding profile values (name/age/height/weight/target/gender/injuries/equipment), intent must be 'none'.\n"
        "- Choose restart/edit only for explicit standalone control asks.\n"
        "- If unsure, output intent='none'.\n"
        "- Strict JSON only."
    )

    def _validate(raw: dict[str, Any]) -> dict[str, Any]:
        intent = str(raw.get("intent") or "").strip().lower()
        if intent not in ALLOWED_CONTROL_INTENTS:
            raise ValueError("invalid_intent")
        confidence = str(raw.get("confidence") or "low").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid_confidence")
        return {
            "intent": intent,
            "confidence": confidence,
            "reason": str(raw.get("reason") or "llm_control_classifier").strip().lower(),
        }

    try:
        return await run_json_contract(
            model_name=GEMINI_COACH_MODEL,
            system_prompt=system_prompt,
            payload={"user_message": user_message, "pending_fields": pending},
            max_retries=MAX_CONTROL_RETRIES,
            validator=_validate,
        )
    except Exception:  # noqa: BLE001
        return None
