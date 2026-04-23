from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.clients.supabase_client import supabase


def consume_daily_turn_quota(
    *,
    phone_number: str,
    timezone_name: str,
    daily_limit: int,
    warning_threshold: int,
) -> dict[str, int | bool]:
    tz = ZoneInfo(timezone_name)
    usage_date = datetime.now(tz).date().isoformat()
    try:
        response = (
            supabase.table("daily_turn_usage")
            .select("id,used_turns")
            .eq("phone_number", phone_number)
            .eq("usage_date", usage_date)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        existing = rows[0] if rows else None

        if existing:
            used_turns = int(float(existing.get("used_turns") or 0))
            next_turn = used_turns + 1
            (
                supabase.table("daily_turn_usage")
                .update(
                    {
                        "used_turns": next_turn,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", existing.get("id"))
                .execute()
            )
        else:
            next_turn = 1
            (
                supabase.table("daily_turn_usage")
                .insert(
                    {
                        "phone_number": phone_number,
                        "usage_date": usage_date,
                        "used_turns": next_turn,
                    }
                )
                .execute()
            )
    except Exception:
        # Fail-open so trial is not broken if migration isn't applied yet.
        next_turn = 1

    blocked = next_turn > daily_limit
    warn = (not blocked) and next_turn >= warning_threshold
    remaining = max(0, daily_limit - next_turn)
    return {
        "used_turns": next_turn,
        "daily_limit": daily_limit,
        "remaining_turns": remaining,
        "blocked": blocked,
        "warn": warn,
    }

