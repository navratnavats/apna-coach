from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.messages import (
    graduation_generic,
    onboarding_completion_overview,
    graduation_with_targets,
    intake_basic_stats,
    intake_bulk_details,
    intake_confirm_gender,
    intake_core_why,
    intake_equipment,
    intake_generic,
    intake_injury,
    intake_name,
    intake_profile_complete,
    intake_target_weight,
)
from app.services.persona import resolve_user_address

ONBOARDING_SESSION_TIMEOUT_MINUTES = 15
ONBOARDING_FIELDS = [
    "name",
    "age",
    "height",
    "weight",
    "target_weight",
    "gender",
    "core_why",
    "injuries",
    "equipment",
]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_missing_onboarding_fields(living_profile: dict[str, Any]) -> list[str]:
    if bool(living_profile.get("onboarding_complete")):
        return []
    identity = living_profile.get("identity") or {}
    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    psychology = living_profile.get("psychology") or {}
    lifestyle = living_profile.get("lifestyle") or {}
    onboarding = living_profile.get("onboarding") or {}
    name = str(identity.get("name") or "").strip()

    missing: list[str] = []

    age = _safe_float(biometrics.get("age"))
    height = _safe_float(biometrics.get("height"))
    weight = _safe_float(biometrics.get("weight"))
    target = _safe_float(biometrics.get("target"))
    gender = str(identity.get("gender") or "").strip().lower()
    core_why = str(psychology.get("core_why") or "").strip()
    injuries = physiology.get("injuries") or []
    equipment = lifestyle.get("available_equipment") or []
    confirmed_fields_raw = onboarding.get("confirmed_fields") or []
    confirmed_fields = {
        str(x).strip().lower() for x in confirmed_fields_raw if str(x).strip()
    }

    if not name:
        missing.append("name")
    if age <= 0:
        missing.append("age")
    if height <= 0:
        missing.append("height")
    if weight <= 0:
        missing.append("weight")
    if target <= 0:
        missing.append("target_weight")
    if gender not in {"male", "female", "m", "f", "man", "woman"}:
        missing.append("gender")
    if not core_why:
        missing.append("core_why")
    # Require explicit capture for injuries/equipment to avoid silent completion
    # when model auto-fills defaults.
    if "injuries" not in confirmed_fields:
        missing.append("injuries")
    if "equipment" not in confirmed_fields:
        missing.append("equipment")

    return missing


def refresh_onboarding_session_state(
    living_profile: dict[str, Any],
    *,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], list[str], bool]:
    profile = dict(living_profile or {})
    onboarding = profile.get("onboarding") or {}
    if not isinstance(onboarding, dict):
        onboarding = {}
    now = now_utc or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    missing_fields = get_missing_onboarding_fields(profile)

    was_expired = False
    last_updated_raw = str(onboarding.get("last_updated_at") or "").strip()
    if last_updated_raw and missing_fields:
        try:
            last_updated = datetime.fromisoformat(last_updated_raw.replace("Z", "+00:00"))
            if now - last_updated > timedelta(minutes=ONBOARDING_SESSION_TIMEOUT_MINUTES):
                was_expired = True
        except Exception:
            was_expired = False

    onboarding["pending_fields"] = missing_fields
    onboarding["is_active"] = bool(missing_fields)
    onboarding["last_seen_at"] = now_iso
    if was_expired:
        onboarding["session_expired_at"] = now_iso
    if not missing_fields:
        onboarding["completed_at"] = now_iso
    profile["onboarding"] = onboarding
    return profile, missing_fields, was_expired


def touch_onboarding_session(
    living_profile: dict[str, Any],
    *,
    missing_fields: list[str],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    profile = dict(living_profile or {})
    onboarding = profile.get("onboarding") or {}
    if not isinstance(onboarding, dict):
        onboarding = {}
    now = now_utc or datetime.now(timezone.utc)
    onboarding["pending_fields"] = list(missing_fields)
    onboarding["is_active"] = bool(missing_fields)
    onboarding["last_updated_at"] = now.isoformat()
    if not missing_fields:
        onboarding["completed_at"] = now.isoformat()
    profile["onboarding"] = onboarding
    return profile


def reset_onboarding_profile_fields(living_profile: dict[str, Any]) -> dict[str, Any]:
    profile = dict(living_profile or {})

    identity = dict(profile.get("identity") or {})
    identity["name"] = ""
    identity["gender"] = ""
    identity["preferred_title"] = ""
    profile["identity"] = identity

    physiology = dict(profile.get("physiology") or {})
    biometrics = dict(physiology.get("biometrics") or {})
    biometrics["age"] = 0
    biometrics["height"] = 0
    biometrics["weight"] = 0
    biometrics["target"] = 0
    biometrics.pop("daily_targets", None)
    physiology["biometrics"] = biometrics
    physiology["injuries"] = []
    profile["physiology"] = physiology

    psychology = dict(profile.get("psychology") or {})
    psychology["core_why"] = ""
    profile["psychology"] = psychology

    lifestyle = dict(profile.get("lifestyle") or {})
    lifestyle["available_equipment"] = []
    profile["lifestyle"] = lifestyle

    profile["onboarding_complete"] = False
    return profile


def build_intake_prompt(living_profile: dict[str, Any], missing_fields: list[str]) -> str:
    address = resolve_user_address(living_profile)
    if not missing_fields:
        return intake_profile_complete(address)

    if len(missing_fields) >= 2:
        return intake_bulk_details(address, missing_fields)

    field_set = set(missing_fields)

    if "name" in field_set:
        return intake_name()

    if {"age", "height", "weight"} & field_set:
        return intake_basic_stats(address)

    if "gender" in field_set:
        return intake_confirm_gender(address)

    if "target_weight" in field_set:
        return intake_target_weight(address)

    if "core_why" in field_set:
        return intake_core_why()

    if "injuries" in field_set:
        return intake_injury(address)

    if "equipment" in field_set:
        return intake_equipment(address)

    # Generic fallback
    return intake_generic(address)


def build_graduation_message(living_profile: dict[str, Any]) -> str:
    address = resolve_user_address(living_profile)
    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    targets = biometrics.get("daily_targets") or {}
    cals = int(float(targets.get("cals") or 0))
    protein = int(float(targets.get("protein_g") or 0))
    if cals > 0 and protein > 0:
        return graduation_with_targets(address, cals, protein)
    return graduation_generic(address)


def build_onboarding_completion_message(living_profile: dict[str, Any]) -> str:
    address = resolve_user_address(living_profile)
    identity = living_profile.get("identity") or {}
    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    psychology = living_profile.get("psychology") or {}
    lifestyle = living_profile.get("lifestyle") or {}

    injuries = physiology.get("injuries") or []
    injury_text = "none"
    if isinstance(injuries, list) and injuries:
        parts = []
        for injury in injuries[:3]:
            if isinstance(injury, dict):
                part = str(injury.get("part") or "").strip()
                severity = str(injury.get("severity") or "").strip()
                if part:
                    parts.append(f"{part} ({severity})" if severity else part)
        if parts:
            injury_text = ", ".join(parts)

    equipment = lifestyle.get("available_equipment") or []
    equipment_text = ", ".join([str(x).strip() for x in equipment if str(x).strip()]) or "not specified"

    lines = [
        f"Name: {str(identity.get('name') or 'not specified').strip()}",
        f"Age: {int(_safe_float(biometrics.get('age')) or 0)}",
        f"Height (cm): {int(_safe_float(biometrics.get('height')) or 0)}",
        f"Current weight (kg): {int(_safe_float(biometrics.get('weight')) or 0)}",
        f"Target weight (kg): {int(_safe_float(biometrics.get('target')) or 0)}",
        f"Gender: {str(identity.get('gender') or 'not specified').strip()}",
        f"Core why: {str(psychology.get('core_why') or 'not specified').strip()}",
        f"Injuries: {injury_text}",
        f"Equipment/setup: {equipment_text}",
    ]
    return onboarding_completion_overview(address, profile_lines=lines)
