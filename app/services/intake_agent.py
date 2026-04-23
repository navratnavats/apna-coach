from __future__ import annotations

from typing import Any

from app.services.messages import (
    graduation_generic,
    graduation_with_targets,
    intake_basic_stats,
    intake_confirm_gender,
    intake_core_why,
    intake_equipment,
    intake_generic,
    intake_injury,
    intake_profile_complete,
    intake_target_weight,
)
from app.services.persona import resolve_user_address


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_missing_onboarding_fields(living_profile: dict[str, Any]) -> list[str]:
    identity = living_profile.get("identity") or {}
    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}
    psychology = living_profile.get("psychology") or {}
    lifestyle = living_profile.get("lifestyle") or {}

    missing: list[str] = []

    age = _safe_float(biometrics.get("age"))
    height = _safe_float(biometrics.get("height"))
    weight = _safe_float(biometrics.get("weight"))
    target = _safe_float(biometrics.get("target"))
    gender = str(identity.get("gender") or "").strip().lower()
    core_why = str(psychology.get("core_why") or "").strip()
    injuries = physiology.get("injuries") or []
    equipment = lifestyle.get("available_equipment") or []

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
    if not isinstance(injuries, list) or len(injuries) == 0:
        missing.append("injuries")
    if not isinstance(equipment, list) or len(equipment) == 0:
        missing.append("equipment")

    return missing


def build_intake_prompt(living_profile: dict[str, Any], missing_fields: list[str]) -> str:
    address = resolve_user_address(living_profile)
    if not missing_fields:
        return intake_profile_complete(address)

    field_set = set(missing_fields)

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
