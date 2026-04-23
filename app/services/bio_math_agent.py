from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.agent_trace import log_agent_event


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _recent_volume_count(living_profile: dict[str, Any], days: int = 7) -> int:
    logs = living_profile.get("logs") or {}
    volume_trends = logs.get("volume_trends") or []
    if not isinstance(volume_trends, list):
        return 0

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)
    count = 0
    for entry in volume_trends:
        if not isinstance(entry, dict):
            continue
        raw_ts = str(entry.get("logged_at") or "").strip()
        if not raw_ts:
            continue
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.astimezone(timezone.utc) >= cutoff:
                count += 1
        except ValueError:
            continue
    return count


def _activity_multiplier(living_profile: dict[str, Any]) -> float:
    # Moderate when recent workout volume appears active; else lightly active.
    recent_volume = _recent_volume_count(living_profile, days=7)
    return 1.55 if recent_volume >= 3 else 1.375


def calculate_tdee_and_macros(
    *,
    weight: float,
    height_cm: float,
    age: int,
    target_weight: float,
    living_profile: dict[str, Any],
) -> dict[str, int]:
    """
    Deterministic Agent 4 math:
    - Mifflin-St Jeor BMR
    - Activity multiplier from workout volume
    - Safe cut deficit 500-700
    - Protein floor at 1.8 g/kg of target body weight
    """
    identity = living_profile.get("identity") or {}
    gender = str(identity.get("gender") or "").strip().lower()
    # Mifflin-St Jeor uses +5 (male) / -161 (female). Default to +5 if unspecified.
    sex_constant = -161 if gender in {"female", "woman", "f"} else 5

    bmr = (10 * weight) + (6.25 * height_cm) - (5 * age) + sex_constant
    multiplier = _activity_multiplier(living_profile)
    tdee = bmr * multiplier

    # Bigger gap to target -> use stronger but still safe deficit.
    weight_gap = max(0.0, weight - target_weight)
    deficit = 700 if weight_gap >= 12 else 500
    target_cals = max(1200.0, tdee - deficit)

    protein_g = max(1.8 * target_weight, 0.0)
    protein_cals = protein_g * 4

    fat_cals = target_cals * 0.25
    fat_g = fat_cals / 9

    carb_cals = max(0.0, target_cals - protein_cals - fat_cals)
    carbs_g = carb_cals / 4

    return {
        "tdee_cals": _safe_int(round(tdee)),
        "deficit_cals": _safe_int(round(deficit)),
        "cals": _safe_int(round(target_cals)),
        "protein_g": _safe_int(round(protein_g)),
        "carbs_g": _safe_int(round(carbs_g)),
        "fat_g": _safe_int(round(fat_g)),
    }


MET_CATEGORY_BASE: dict[str, dict[str, float]] = {
    "run": {"light": 7.0, "moderate": 9.0, "vigorous": 11.0},
    "walk": {"light": 2.8, "moderate": 3.5, "vigorous": 4.3},
    "cycling": {"light": 5.5, "moderate": 7.5, "vigorous": 10.0},
    "swimming": {"light": 6.0, "moderate": 8.0, "vigorous": 10.0},
    "strength_training": {"light": 3.5, "moderate": 5.0, "vigorous": 6.0},
    "racquet_sport": {"light": 5.0, "moderate": 6.5, "vigorous": 8.0},
    "team_sport": {"light": 5.0, "moderate": 7.0, "vigorous": 9.0},
    "general_cardio": {"light": 4.5, "moderate": 6.5, "vigorous": 8.5},
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_intensity(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"light", "easy", "low"}:
        return "light"
    if raw in {"vigorous", "hard", "high", "intense"}:
        return "vigorous"
    return "moderate"


def _normalize_rest_style(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"no_rest", "continuous", "pura_continuous", "minimal_rest"}:
        return "no_rest"
    if raw in {"long_rest", "heavy_rest"}:
        return "long_rest"
    return "normal"


def _rest_work_ratio(activity_category: str, rest_style: str) -> float:
    if activity_category != "strength_training":
        return 1.0
    if rest_style == "no_rest":
        return 0.95
    if rest_style == "long_rest":
        return 0.55
    return 0.65


def normalize_activity_for_burn(raw_activity: dict[str, Any]) -> dict[str, Any]:
    category = str(raw_activity.get("category") or "general_cardio").strip().lower()
    if category not in MET_CATEGORY_BASE:
        category = "general_cardio"

    intensity = _normalize_intensity(raw_activity.get("intensity"))
    met_from_table = MET_CATEGORY_BASE[category][intensity]
    met_score = _safe_float(raw_activity.get("met_score"))
    if met_score <= 0:
        met_score = met_from_table
    met_score = _clamp(met_score, met_from_table - 2.0, met_from_table + 2.0)

    duration_mins = _safe_float(raw_activity.get("duration_mins"))
    duration_mins = _clamp(duration_mins, 5.0, 240.0)

    rest_style = _normalize_rest_style(raw_activity.get("rest_style"))
    work_ratio = _rest_work_ratio(category, rest_style)
    effective_duration_mins = duration_mins * work_ratio

    return {
        "name": str(raw_activity.get("name") or category).strip() or category,
        "category": category,
        "intensity": intensity,
        "rest_style": rest_style,
        "work_ratio": round(work_ratio, 3),
        "duration_mins": round(duration_mins, 1),
        "effective_duration_mins": round(effective_duration_mins, 1),
        "met_score_used": round(met_score, 2),
        "assumption_note": (
            "No-rest override applied."
            if rest_style == "no_rest"
            else "Normal rest-adjusted effective duration used."
            if category == "strength_training"
            else "Standard MET mapping used."
        ),
    }


def calculate_activity_burn(
    *, met_score: float, duration_mins: float, weight_kg: float
) -> int:
    calories = (met_score * 3.5 * weight_kg / 200.0) * duration_mins
    return _safe_int(round(max(0.0, calories)))


def calculate_net_deficit(*, tdee_cals: float, active_cals_burnt: float, food_cals: float) -> int:
    return _safe_int(round((tdee_cals + active_cals_burnt) - food_cals))


def compute_daily_targets_if_ready(
    living_profile: dict[str, Any],
    trace_id: str | None = None,
) -> tuple[dict[str, int] | None, str | None]:
    identity = living_profile.get("identity") or {}
    physiology = living_profile.get("physiology") or {}
    biometrics = physiology.get("biometrics") or {}

    weight = _safe_float(biometrics.get("weight"))
    target_weight = _safe_float(biometrics.get("target"))
    height_cm = _safe_float(biometrics.get("height"))
    age = _safe_int(biometrics.get("age"))
    gender = str(identity.get("gender") or "").strip().lower()

    if height_cm <= 0 or age <= 0:
        log_agent_event(
            agent="bio_math",
            stage="gate_blocked",
            status="missing_height_or_age",
            trace_id=trace_id,
        )
        return None, "missing_height_or_age"
    if gender not in {"male", "female", "m", "f", "man", "woman"}:
        log_agent_event(
            agent="bio_math",
            stage="gate_blocked",
            status="missing_gender",
            trace_id=trace_id,
        )
        return None, "missing_gender"
    if weight <= 0 or target_weight <= 0:
        log_agent_event(
            agent="bio_math",
            stage="gate_blocked",
            status="missing_weight_or_target",
            trace_id=trace_id,
        )
        return None, "missing_weight_or_target"

    targets = calculate_tdee_and_macros(
        weight=weight,
        height_cm=height_cm,
        age=age,
        target_weight=target_weight,
        living_profile=living_profile,
    )
    log_agent_event(
        agent="bio_math",
        stage="targets_computed",
        trace_id=trace_id,
        details=targets,
    )
    return targets, None
