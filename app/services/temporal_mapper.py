from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

PHONE_TZ_MAP = {
    "+91": "Asia/Kolkata",
    "+971": "Asia/Dubai",
    "+44": "Europe/London",
    "+1": "America/New_York",
}

MEAL_SLOT_TERMS = {
    "breakfast": {"breakfast", "subah", "nashta", "morning"},
    "morning_snack": {"morning snack"},
    "lunch": {"lunch", "dopahar", "afternoon"},
    "evening_snack": {"evening snack", "snack", "shaam"},
    "dinner": {"dinner", "raat", "night"},
}
ACTIVITY_SLOT_TERMS = {
    "morning_session": {"morning", "subah", "am"},
    "afternoon_session": {"afternoon", "dopahar", "noon"},
    "evening_session": {"evening", "shaam", "night", "pm"},
}


def infer_user_timezone(living_profile: dict[str, Any], phone_number: str) -> tuple[str, str]:
    identity = living_profile.get("identity") or {}
    explicit_tz = str(identity.get("timezone") or "").strip()
    if explicit_tz:
        try:
            ZoneInfo(explicit_tz)
            return explicit_tz, "profile"
        except Exception:
            pass
    regional = str(identity.get("regional_context") or "").strip().upper()
    if regional == "IN":
        return "Asia/Kolkata", "regional_context"
    for prefix, tz_name in PHONE_TZ_MAP.items():
        if str(phone_number or "").startswith(prefix):
            return tz_name, "phone_prefix"
    return "Asia/Kolkata", "default"


def _explicit_meal_slots(text: str) -> list[str]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return []
    matched: list[tuple[int, str]] = []
    for slot, terms in MEAL_SLOT_TERMS.items():
        last_pos = -1
        for term in terms:
            pos = lowered.rfind(term)
            if pos > last_pos:
                last_pos = pos
        if last_pos >= 0:
            matched.append((last_pos, slot))
    matched.sort(key=lambda item: item[0])
    return [slot for _, slot in matched]


def _explicit_meal_slot(text: str) -> str | None:
    slots = _explicit_meal_slots(text)
    if slots:
        return slots[-1]
    return None


def infer_meal_slot(*, message_text: str, summary: str, local_dt: datetime) -> tuple[str, str]:
    summary_explicit = _explicit_meal_slot(summary)
    if summary_explicit:
        return summary_explicit, "explicit_user_time"
    message_slots = _explicit_meal_slots(message_text)
    if len(message_slots) == 1:
        return message_slots[0], "explicit_user_time"
    hour = local_dt.hour
    if 5 <= hour < 10:
        return "breakfast", "message_time_inferred"
    if 10 <= hour < 12:
        return "morning_snack", "message_time_inferred"
    if 12 <= hour < 16:
        return "lunch", "message_time_inferred"
    if 16 <= hour < 19:
        return "evening_snack", "message_time_inferred"
    if 19 <= hour < 24:
        return "dinner", "message_time_inferred"
    return "other", "default_window"


def infer_activity_slot(*, message_text: str, local_dt: datetime) -> tuple[str, str]:
    lowered = str(message_text or "").strip().lower()
    for slot, terms in ACTIVITY_SLOT_TERMS.items():
        if any(term in lowered for term in terms):
            return slot, "explicit_user_time"
    hour = local_dt.hour
    if 5 <= hour < 12:
        return "morning_session", "message_time_inferred"
    if 12 <= hour < 17:
        return "afternoon_session", "message_time_inferred"
    if 17 <= hour < 22:
        return "evening_session", "message_time_inferred"
    return "other_session", "default_window"


def _extract_explicit_local_time(text: str, local_dt: datetime) -> datetime | None:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None

    # Matches:
    # 8am / 8 am / 8:30am / 08:30 pm / at 7 / around 6
    match = re.search(
        r"\b(?:at|around)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lowered,
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    marker = str(match.group(3) or "").strip().lower()
    if minute < 0 or minute > 59:
        return None
    if marker == "am":
        if hour == 12:
            hour = 0
    elif marker == "pm":
        if hour < 12:
            hour += 12
    else:
        # 24-hour fallback: if impossible hour, reject.
        if hour > 23:
            return None
    if hour < 0 or hour > 23:
        return None
    return local_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def build_temporal_metadata(
    *,
    timezone_name: str,
    message_text: str,
    summary: str,
    kind: str,
    temporal_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    tz = ZoneInfo(timezone_name)
    local_dt = now_utc.astimezone(tz)
    explicit_local_dt = _extract_explicit_local_time(
        f"{message_text}\n{summary}",
        local_dt,
    )
    hint = temporal_hint or {}
    hint_clock = str(hint.get("clock_time_local") or "").strip()
    if hint_clock and ":" in hint_clock:
        try:
            hh, mm = hint_clock.split(":", 1)
            explicit_local_dt = local_dt.replace(
                hour=int(hh),
                minute=int(mm),
                second=0,
                microsecond=0,
            )
        except Exception:
            pass
    event_local_dt = explicit_local_dt or local_dt
    relative_ref = str(hint.get("relative_ref") or "").strip().lower()
    if relative_ref.startswith("yesterday_"):
        event_local_dt = event_local_dt - timedelta(days=1)
    elif relative_ref == "yesterday":
        event_local_dt = event_local_dt - timedelta(days=1)
    if kind == "nutrition":
        slot, source = infer_meal_slot(
            message_text=message_text,
            summary=summary,
            local_dt=event_local_dt,
        )
        hint_slot = str(hint.get("slot_hint") or "").strip().lower()
        if hint_slot in {"breakfast", "morning_snack", "lunch", "evening_snack", "dinner", "other"}:
            slot = hint_slot
            source = "llm_temporal_hint"
        slot_key = "meal_slot"
    else:
        slot, source = infer_activity_slot(
            message_text=message_text,
            local_dt=event_local_dt,
        )
        hint_slot = str(hint.get("slot_hint") or "").strip().lower()
        if hint_slot in {
            "morning_session",
            "afternoon_session",
            "evening_session",
            "other_session",
        }:
            slot = hint_slot
            source = "llm_temporal_hint"
        slot_key = "session_slot"
    if explicit_local_dt is not None:
        source = "explicit_user_time"
    return {
        "logged_at": now_utc.isoformat(),
        "logged_at_local": event_local_dt.isoformat(),
        "local_date": event_local_dt.date().isoformat(),
        "timezone": timezone_name,
        "event_time_source": source,
        slot_key: slot,
    }
