from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.clients.supabase_client import supabase
from app.services.runtime_cache import MISSING, get_cached, set_cached


def consume_daily_turn_quota(
    *,
    phone_number: str,
    timezone_name: str,
    daily_limit: int,
    warning_threshold: int,
) -> dict[str, int | bool]:
    tz = ZoneInfo(timezone_name)
    usage_date = datetime.now(tz).date().isoformat()
    cache_key = f"quota_usage:{phone_number}:{usage_date}"
    utc_now = datetime.now(timezone.utc).isoformat()
    try:
        cached = get_cached(cache_key)
        if cached is not MISSING and isinstance(cached, dict) and cached.get("id"):
            used_turns = int(float(cached.get("used_turns") or 0))
            row_id = cached.get("id")
            next_turn = used_turns + 1
            (
                supabase.table("daily_turn_usage")
                .update(
                    {
                        "used_turns": next_turn,
                        "updated_at": utc_now,
                    }
                )
                .eq("id", row_id)
                .execute()
            )
            set_cached(cache_key, {"id": row_id, "used_turns": next_turn}, ttl_seconds=45)
        else:
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
                row_id = existing.get("id")
                next_turn = used_turns + 1
                (
                    supabase.table("daily_turn_usage")
                    .update(
                        {
                            "used_turns": next_turn,
                            "updated_at": utc_now,
                        }
                    )
                    .eq("id", row_id)
                    .execute()
                )
                set_cached(cache_key, {"id": row_id, "used_turns": next_turn}, ttl_seconds=45)
            else:
                next_turn = 1
                insert_resp = (
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
                inserted_rows = insert_resp.data or []
                inserted = inserted_rows[0] if inserted_rows else {}
                set_cached(
                    cache_key,
                    {"id": inserted.get("id"), "used_turns": next_turn},
                    ttl_seconds=45,
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

