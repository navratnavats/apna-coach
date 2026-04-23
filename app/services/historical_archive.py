from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.clients.supabase_client import supabase

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
        return None
    row = rows[0] or {}
    return {
        "archive_date": row.get("archive_date"),
        "summary_line": row.get("summary_line") or "",
        "metrics": row.get("metrics") or {},
        "nutrition_entries": row.get("nutrition_entries") or [],
        "activity_entries": row.get("activity_entries") or [],
    }
