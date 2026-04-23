from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.agent_trace import log_agent_event


def _local_date_from_iso(raw_ts: str, timezone_name: str) -> str | None:
    try:
        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        tz = ZoneInfo(timezone_name)
        return dt.astimezone(tz).date().isoformat()
    except ValueError:
        return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _partition_by_local_day(
    entries: list[dict[str, Any]],
    timezone_name: str,
    date_iso: str,
    *,
    include_missing_logged_at_as_today: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today_entries: list[dict[str, Any]] = []
    remaining_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        logged_at = str(entry.get("logged_at") or "").strip()
        if not logged_at and include_missing_logged_at_as_today:
            today_entries.append(entry)
            continue
        entry_local_date = _local_date_from_iso(logged_at, timezone_name) if logged_at else None
        if entry_local_date == date_iso:
            today_entries.append(entry)
        else:
            remaining_entries.append(entry)
    return today_entries, remaining_entries


def _reset_current_day(current_day: dict[str, Any]) -> dict[str, Any]:
    reset = dict(current_day)
    calorie_budget = int(round(_safe_float(reset.get("calorie_budget"))))
    metrics = reset.get("metrics") if isinstance(reset.get("metrics"), dict) else {}
    tdee_cals = int(round(_safe_float(metrics.get("tdee_cals"))))
    reset["cals"] = 0
    reset["active_cals_burnt"] = 0
    reset["net_deficit"] = 0
    reset["workout_complete"] = False
    # Keep water as daily metric; reset for next day.
    reset["water"] = 0
    reset["metrics"] = {
        "intake_cals": 0,
        "active_cals_burnt": 0,
        "tdee_cals": tdee_cals,
        "net_deficit_cals": 0,
        "calorie_budget_cals": calorie_budget,
        "vs_budget_cals": calorie_budget,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    return reset


def compress_today_for_archive(
    living_profile: dict[str, Any],
    timezone_name: str,
    *,
    trace_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """
    End-of-day nutrition compression:
    - Move today's detailed entries into one summary record.
    - Keep prior detailed entries untouched.
    """
    logs = living_profile.get("logs") or {}
    nutrition_log = logs.get("nutrition_log") or []
    activity_log = logs.get("activity_log") or []
    if not isinstance(nutrition_log, list):
        nutrition_log = []
    if not isinstance(activity_log, list):
        activity_log = []

    tz = ZoneInfo(timezone_name)
    today_local = datetime.now(tz).date().isoformat()

    today_nutrition_entries, remaining_nutrition_entries = _partition_by_local_day(
        [entry for entry in nutrition_log if isinstance(entry, dict)],
        timezone_name,
        today_local,
        include_missing_logged_at_as_today=True,
    )
    today_activity_entries, remaining_activity_entries = _partition_by_local_day(
        [entry for entry in activity_log if isinstance(entry, dict)],
        timezone_name,
        today_local,
        include_missing_logged_at_as_today=True,
    )

    if not today_nutrition_entries and not today_activity_entries:
        return living_profile, None

    total_calories = sum(_safe_float(e.get("estimated_calories")) for e in today_nutrition_entries)
    total_protein = sum(
        _safe_float((e.get("estimated_macros") or {}).get("protein_g"))
        for e in today_nutrition_entries
    )
    total_carbs = sum(
        _safe_float((e.get("estimated_macros") or {}).get("carbs_g")) for e in today_nutrition_entries
    )
    total_fat = sum(
        _safe_float((e.get("estimated_macros") or {}).get("fat_g")) for e in today_nutrition_entries
    )
    total_activity_burn = sum(_safe_float(e.get("burn_cals")) for e in today_activity_entries)

    activity_names: list[str] = []
    for entry in today_activity_entries:
        name = str(entry.get("name") or "").strip()
        if name:
            activity_names.append(name)
    unique_activities = list(dict.fromkeys(activity_names))[:4]

    archive_summary = {
        "date": today_local,
        "nutrition_entries_count": len(today_nutrition_entries),
        "activity_entries_count": len(today_activity_entries),
        "total_calories": int(round(total_calories)),
        "active_cals_burnt": int(round(total_activity_burn)),
        "total_macros": {
            "protein_g": round(total_protein, 1),
            "carbs_g": round(total_carbs, 1),
            "fat_g": round(total_fat, 1),
        },
        "activity_names": unique_activities,
    }
    compact_line = (
        f"Hit {archive_summary['total_calories']} cals, "
        f"{archive_summary['total_macros']['protein_g']}g protein, "
        f"burnt {archive_summary['active_cals_burnt']} active cals"
    )
    if unique_activities:
        compact_line = f"{compact_line}, activities: {', '.join(unique_activities)}"

    hot_summary_entry = {
        "date": today_local,
        "summary": compact_line,
        "calories": archive_summary["total_calories"],
        "protein_g": archive_summary["total_macros"]["protein_g"],
        "active_cals_burnt": archive_summary["active_cals_burnt"],
    }

    last_3_days_summary = logs.get("last_3_days_summary") or []
    if not isinstance(last_3_days_summary, list):
        last_3_days_summary = []
    # Upsert by date so retries/manual runs don't duplicate the same day.
    replaced = False
    for idx, row in enumerate(last_3_days_summary):
        if isinstance(row, dict) and str(row.get("date") or "") == today_local:
            last_3_days_summary[idx] = hot_summary_entry
            replaced = True
            break
    if not replaced:
        last_3_days_summary.append(hot_summary_entry)
    if len(last_3_days_summary) > 3:
        last_3_days_summary = last_3_days_summary[-3:]

    current_day = logs.get("current_day") or {}
    if not isinstance(current_day, dict):
        current_day = {}
    current_day = _reset_current_day(current_day)

    logs["nutrition_log"] = remaining_nutrition_entries
    logs["activity_log"] = remaining_activity_entries
    logs["last_3_days_summary"] = last_3_days_summary
    logs["current_day"] = current_day

    updated_profile = dict(living_profile)
    updated_profile["logs"] = logs

    archive_payload = {
        "date": today_local,
        "summary_line": compact_line,
        "metrics": archive_summary,
        "nutrition_entries": today_nutrition_entries,
        "activity_entries": today_activity_entries,
    }

    log_agent_event(
        agent="dietitian",
        stage="eod_compression_complete",
        trace_id=trace_id,
        details={
            "date": today_local,
            "nutrition_entries": len(today_nutrition_entries),
            "activity_entries": len(today_activity_entries),
            "remaining_nutrition_entries": len(remaining_nutrition_entries),
            "remaining_activity_entries": len(remaining_activity_entries),
        },
    )
    return updated_profile, archive_payload
