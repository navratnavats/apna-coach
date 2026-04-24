from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.clients.supabase_client import supabase
from app.services.runtime_cache import MISSING, get_cached, set_cached

WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def resolve_target_date(user_message: str, timezone_name: str) -> str | None:
    text = str(user_message or "").strip().lower()
    tz = ZoneInfo(timezone_name)
    now_local = datetime.now(tz).date()

    if "today" in text or "aaj" in text:
        return now_local.isoformat()
    if "yesterday" in text or "kal" in text:
        return (now_local - timedelta(days=1)).isoformat()

    for day_name, day_num in WEEKDAY_MAP.items():
        if day_name in text:
            delta = (now_local.weekday() - day_num) % 7
            if delta == 0:
                delta = 7
            return (now_local - timedelta(days=delta)).isoformat()
    return None


def fetch_historical_day(phone_number: str, date_iso: str) -> dict[str, Any] | None:
    cache_key = f"historical_day:{phone_number}:{date_iso}"
    cached = get_cached(cache_key)
    if cached is not MISSING:
        return cached
    response = (
        supabase.table("historical_archive")
        .select("archive_date,summary_line,metrics,nutrition_entries,activity_entries")
        .eq("phone_number", phone_number)
        .eq("archive_date", date_iso)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        set_cached(cache_key, None, ttl_seconds=120)
        return None
    row = rows[0] or {}
    result = {
        "archive_date": row.get("archive_date"),
        "summary_line": row.get("summary_line") or "",
        "metrics": row.get("metrics") or {},
        "nutrition_entries": row.get("nutrition_entries") or [],
        "activity_entries": row.get("activity_entries") or [],
    }
    set_cached(cache_key, result, ttl_seconds=120)
    return result


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _entry_key(entry: dict[str, Any], kind: str) -> str:
    if kind == "nutrition":
        return (
            f"{entry.get('local_date')}|{entry.get('meal_slot')}|{entry.get('summary')}|"
            f"{entry.get('logged_at_local')}|{entry.get('estimated_calories')}"
        )
    return (
        f"{entry.get('local_date')}|{entry.get('session_slot')}|{entry.get('name')}|"
        f"{entry.get('logged_at_local')}|{entry.get('duration_mins')}|{entry.get('burn_cals')}"
    )


def _merge_entries(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in existing + incoming:
        if not isinstance(entry, dict):
            continue
        key = _entry_key(entry, kind)
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    return merged


def _build_metrics(
    nutrition_entries: list[dict[str, Any]],
    activity_entries: list[dict[str, Any]],
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_calories = sum(_safe_float(e.get("estimated_calories")) for e in nutrition_entries)
    total_protein = sum(
        _safe_float((e.get("estimated_macros") or {}).get("protein_g")) for e in nutrition_entries
    )
    total_carbs = sum(
        _safe_float((e.get("estimated_macros") or {}).get("carbs_g")) for e in nutrition_entries
    )
    total_fat = sum(_safe_float((e.get("estimated_macros") or {}).get("fat_g")) for e in nutrition_entries)
    total_activity_burn = sum(_safe_float(e.get("burn_cals")) for e in activity_entries)
    activity_names: list[str] = []
    for entry in activity_entries:
        name = str(entry.get("name") or "").strip()
        if name:
            activity_names.append(name)
    unique_activities = list(dict.fromkeys(activity_names))[:6]
    prev = previous if isinstance(previous, dict) else {}
    return {
        "date": prev.get("date") or "",
        "nutrition_entries_count": len(nutrition_entries),
        "activity_entries_count": len(activity_entries),
        "total_calories": int(round(total_calories)),
        "active_cals_burnt": int(round(total_activity_burn)),
        "total_macros": {
            "protein_g": round(total_protein, 1),
            "carbs_g": round(total_carbs, 1),
            "fat_g": round(total_fat, 1),
        },
        "activity_names": unique_activities,
    }


def upsert_historical_day(
    *,
    phone_number: str,
    archive_date: str,
    summary_line: str,
    metrics: dict[str, Any],
    nutrition_entries: list[dict[str, Any]],
    activity_entries: list[dict[str, Any]],
) -> None:
    existing = fetch_historical_day(phone_number, archive_date)
    if existing:
        merged_nutrition = _merge_entries(
            existing.get("nutrition_entries") or [],
            nutrition_entries,
            kind="nutrition",
        )
        merged_activity = _merge_entries(
            existing.get("activity_entries") or [],
            activity_entries,
            kind="activity",
        )
        merged_metrics = _build_metrics(
            merged_nutrition,
            merged_activity,
            previous=(existing.get("metrics") or {}),
        )
        merged_metrics["date"] = archive_date
        next_version = int(float((existing.get("metrics") or {}).get("archive_version") or 1)) + 1
        merged_metrics["archive_version"] = next_version
        final_summary = summary_line or str(existing.get("summary_line") or "")
    else:
        merged_nutrition = nutrition_entries
        merged_activity = activity_entries
        merged_metrics = _build_metrics(
            merged_nutrition,
            merged_activity,
            previous=metrics,
        )
        merged_metrics["date"] = archive_date
        merged_metrics["archive_version"] = 1
        final_summary = summary_line

    (
        supabase.table("historical_archive")
        .upsert(
            {
                "phone_number": phone_number,
                "archive_date": archive_date,
                "summary_line": final_summary,
                "metrics": merged_metrics,
                "nutrition_entries": merged_nutrition,
                "activity_entries": merged_activity,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
            on_conflict="phone_number,archive_date",
        )
        .execute()
    )
    set_cached(
        f"historical_day:{phone_number}:{archive_date}",
        {
            "archive_date": archive_date,
            "summary_line": final_summary,
            "metrics": merged_metrics,
            "nutrition_entries": merged_nutrition,
            "activity_entries": merged_activity,
        },
        ttl_seconds=120,
    )
