from __future__ import annotations

from typing import Any

from app.services.agent_trace import log_agent_event


ALLOWED_SHAPE: dict[str, Any] = {
    "identity": {
        "name": True,
        "preferred_title": True,
        "language_mix": True,
        "audio_preference": True,
        "gender": True,
        "regional_context": True,
    },
    "physiology": {
        "biometrics": {
            "weight": True,
            "target": True,
            "height": True,
            "body_fat_est": True,
            "age": True,
            "daily_targets": True,
        },
        "injuries": True,
        "medical_flags": True,
    },
    "psychology": {
        "motivation_style": True,
        "core_why": True,
        "engagement_score": True,
        "quit_signals": True,
    },
    "lifestyle": {
        "training_environment": True,
        "available_equipment": True,
        "preferred_workout_time": True,
        "dietary_restrictions": True,
    },
    "logs": {
        "current_day": True,
        "nutrition_log": True,
        "volume_trends": True,
        "last_3_workout_summaries": True,
        "activity_log": True,
        "activity_adjustment": True,
        "coach_message_count": True,
    },
    "onboarding": True,
    "onboarding_complete": True,
}


def _sanitize_value(
    value: Any,
    shape: Any,
    *,
    path: str,
    dropped_paths: list[str],
) -> Any:
    if shape is True:
        return value
    if not isinstance(value, dict) or not isinstance(shape, dict):
        dropped_paths.append(path)
        return None

    cleaned: dict[str, Any] = {}
    for key, nested_value in value.items():
        nested_shape = shape.get(key)
        nested_path = f"{path}.{key}" if path else key
        if nested_shape is None:
            dropped_paths.append(nested_path)
            continue
        sanitized = _sanitize_value(
            nested_value,
            nested_shape,
            path=nested_path,
            dropped_paths=dropped_paths,
        )
        if sanitized is not None:
            cleaned[key] = sanitized
    return cleaned


def sanitize_memory_updates(
    updates: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(updates, dict):
        log_agent_event(
            agent="memory_clerk",
            stage="schema_warning",
            status="non_dict_update",
            trace_id=trace_id,
        )
        return {}

    dropped_paths: list[str] = []
    cleaned = _sanitize_value(
        updates,
        ALLOWED_SHAPE,
        path="",
        dropped_paths=dropped_paths,
    )
    if not isinstance(cleaned, dict):
        cleaned = {}

    if dropped_paths:
        log_agent_event(
            agent="memory_clerk",
            stage="schema_warning",
            status="dropped_unknown_keys",
            trace_id=trace_id,
            details={"dropped_paths": dropped_paths[:20], "dropped_count": len(dropped_paths)},
        )

    return cleaned
