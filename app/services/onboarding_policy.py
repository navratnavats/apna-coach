from __future__ import annotations

from typing import Any


def evaluate_onboarding_message(
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
    for field in pending_fields:
        markers = signal_map.get(field, ())
        if any(marker in text for marker in markers):
            return {"decision": "allow", "reason": f"matches_{field}"}

    return {"decision": "redirect", "reason": "does_not_match_pending_fields"}
