from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from app.clients.supabase_client import supabase
from app.services.runtime_cache import MISSING, get_cached, invalidate_cached, set_cached

PLAN_INTENTS = {
    "plan_create_request",
    "plan_status_query",
    "plan_edit_request",
    "plan_change_signal",
}
YES_WORDS = {"yes", "haan", "ha", "y", "ok", "okay", "kar", "karo", "do it"}
NO_WORDS = {"no", "nahi", "mat", "cancel", "stop", "nah"}

FULL_PLAN_VIEW_MARKERS = {
    "full plan",
    "show full",
    "show full plan",
    "complete plan",
    "12 week plan",
    "12-week plan",
    "entire plan",
    "all weeks",
    "week by week",
    "sab weeks",
    "poora plan",
}

TODAY_PLAN_MARKERS = {
    "what to do today",
    "plan for today",
    "today plan",
    "aaj kya karu",
    "aaj kya karna hai",
    "aaj ka plan",
    "today workout",
    "today meal plan",
}

TODAY_DIET_MARKERS = {
    "what should i eat today",
    "what all should i eat today",
    "what to eat today",
    "diet for today",
    "meal plan for today",
    "aaj kya khau",
    "aaj kya khana hai",
    "aaj ka diet plan",
}
WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PLAN_SHOW_MARKERS = {
    "show me the plan",
    "show plan",
    "show the plan",
    "plan dikha",
    "mera plan dikhao",
}


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def ensure_plan_state(living_profile: dict[str, Any]) -> dict[str, Any]:
    profile = dict(living_profile or {})
    plans = profile.get("plans")
    if not isinstance(plans, dict):
        plans = {}
    active = plans.get("active")
    if not isinstance(active, dict):
        active = {}
    active.setdefault("plan_id", "")
    active.setdefault("version", 0)
    active.setdefault("status", "none")
    active.setdefault("type", "hybrid")
    active.setdefault("horizon", "weekly")
    active.setdefault("horizon_weeks", 0)
    active.setdefault("current_block", {})
    active.setdefault("week_blocks", [])
    active.setdefault("day_actions", [])
    active.setdefault("constraints", {})
    active.setdefault("pending_change_request", {})
    active.setdefault("execution_notes", [])
    active.setdefault("paused_meta", {})
    active.setdefault("goal_anchor", "")
    active.setdefault("rolling", {})
    plans["active"] = active
    plans.setdefault("change_log", [])
    profile["plans"] = plans
    return profile


def append_plan_execution_note(
    living_profile: dict[str, Any],
    *,
    note_type: str,
    note_text: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    notes = active.get("execution_notes") or []
    if not isinstance(notes, list):
        notes = []
    notes.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "type": str(note_type or "note"),
            "text": str(note_text or "").strip(),
            "payload": payload or {},
        }
    )
    if len(notes) > 30:
        notes = notes[-30:]
    active["execution_notes"] = notes
    profile["plans"]["active"] = active
    return profile


def get_latest_plan_execution_note(
    living_profile: dict[str, Any], note_type: str | None = None
) -> dict[str, Any] | None:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    notes = active.get("execution_notes") or []
    if not isinstance(notes, list) or not notes:
        return None
    for note in reversed(notes):
        if not isinstance(note, dict):
            continue
        if note_type and str(note.get("type") or "").strip() != note_type:
            continue
        return note
    return None


def resolve_latest_partial_completion_reason(
    living_profile: dict[str, Any],
    *,
    why_summary: str,
    recommend_adjustment: bool,
    suggested_window_days: int,
) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    notes = active.get("execution_notes") or []
    if not isinstance(notes, list):
        notes = []
    for idx in range(len(notes) - 1, -1, -1):
        note = notes[idx]
        if not isinstance(note, dict):
            continue
        if str(note.get("type") or "").strip() != "partial_completion":
            continue
        payload = note.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        payload["why_analysis"] = {
            "summary": str(why_summary or "").strip(),
            "recommend_adjustment": bool(recommend_adjustment),
            "suggested_window_days": max(3, min(7, int(suggested_window_days or 3))),
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        note["payload"] = payload
        note["text"] = str(note.get("text") or "").strip()
        notes[idx] = note
        break
    active["execution_notes"] = notes[-30:]
    profile["plans"]["active"] = active
    return profile


def pause_active_plan(living_profile: dict[str, Any], reason: str) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    active["status"] = "paused"
    active["paused_meta"] = {
        "paused_at": datetime.now(timezone.utc).isoformat(),
        "reason": str(reason or "").strip(),
        "resume_hint_version": _safe_int(active.get("version")),
    }
    profile["plans"]["active"] = active
    return append_plan_execution_note(
        profile,
        note_type="pause",
        note_text="Plan paused",
        payload={"reason": str(reason or "").strip()},
    )


def resume_active_plan(living_profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    paused_meta = active.get("paused_meta") or {}
    if not isinstance(paused_meta, dict):
        paused_meta = {}
    active["status"] = "active"
    active["paused_meta"] = {}
    profile["plans"]["active"] = active
    profile = append_plan_execution_note(
        profile,
        note_type="resume",
        note_text="Plan resumed",
        payload={"paused_meta": paused_meta},
    )
    return profile, paused_meta


def classify_plan_intent_fallback(user_message: str) -> str | None:
    text = str(user_message or "").strip().lower()
    if not text:
        return None
    if any(
        k in text
        for k in (
            "show full plan",
            "full plan",
            "show full 12 week plan",
            "show full 12-week plan",
            "what is the plan",
            "what's the plan",
            "plan status",
            "this week plan",
            "next week plan",
            "tomorrow plan",
        )
    ):
        return "plan_status_query"
    if any(k in text for k in ("plan for tomorrow", "diet plan", "meal plan", "create plan", "draft plan")):
        return "plan_create_request"
    if any(k in text for k in ("edit plan", "change plan", "modify plan", "adjust plan")):
        return "plan_edit_request"
    if any(k in text for k in ("vacation", "missed today", "traveling", "trip")):
        return "plan_change_signal"
    if any(k in text for k in ("what next week", "tomorrow plan", "this week plan")):
        return "plan_status_query"
    return None


def is_full_plan_view_request(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in FULL_PLAN_VIEW_MARKERS)


def is_today_plan_request(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in TODAY_PLAN_MARKERS)


def is_today_diet_request(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in TODAY_DIET_MARKERS):
        return True
    return ("today" in text or "aaj" in text) and any(
        k in text for k in ("eat", "meal", "diet", "kha", "khana")
    )


def should_render_plan_now(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in PLAN_SHOW_MARKERS)


def extract_requested_day_number(user_message: str) -> int | None:
    text = str(user_message or "").strip().lower()
    if not text:
        return None
    patterns = [
        r"\bday\s*(\d{1,2})\b",
        r"\b(\d{1,2})(?:st|nd|rd|th)\s*day\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            value = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _normalize_day_label(value: str) -> str:
    raw = str(value or "").strip().lower()
    map_days = {
        "monday": "Mon",
        "mon": "Mon",
        "tuesday": "Tue",
        "tue": "Tue",
        "wednesday": "Wed",
        "wed": "Wed",
        "thursday": "Thu",
        "thu": "Thu",
        "friday": "Fri",
        "fri": "Fri",
        "saturday": "Sat",
        "sat": "Sat",
        "sunday": "Sun",
        "sun": "Sun",
    }
    return map_days.get(raw, "Mon")


def _format_day_entry(day_entry: dict[str, Any], *, prefix: str = "") -> list[str]:
    lines: list[str] = []
    day_name = str(day_entry.get("day") or "Day").strip()
    lines.append(f"{prefix}{day_name}:")
    workout = day_entry.get("workout") or {}
    if isinstance(workout, dict):
        lines.append(f"{prefix}- Training:")
        workout_title = str(workout.get("title") or "").strip()
        if workout_title:
            lines.append(f"{prefix}  • Focus: {workout_title}")
        workout_items = workout.get("items") or []
        if isinstance(workout_items, list):
            for item in workout_items[:4]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "Exercise").strip()
                sets = str(item.get("sets") or "").strip()
                reps = str(item.get("reps") or "").strip()
                rest = str(item.get("rest_sec") or "").strip()
                detail = f"{name}"
                if sets or reps:
                    detail += f" ({sets} x {reps})"
                if rest:
                    detail += f", rest {rest}s"
                lines.append(f"{prefix}  • {detail}")
    meals = day_entry.get("meals") or {}
    if isinstance(meals, dict):
        lines.append(f"{prefix}- Diet:")
        target_kcal = meals.get("target_kcal")
        if target_kcal is not None:
            lines.append(f"{prefix}  • Target: {target_kcal} kcal")
        meal_blocks = meals.get("blocks") or []
        if isinstance(meal_blocks, list):
            for mb in meal_blocks[:3]:
                if not isinstance(mb, dict):
                    continue
                meal_name = str(mb.get("meal") or "meal").strip()
                kcal = mb.get("kcal")
                items = mb.get("items") or []
                item_line = ", ".join(
                    [str(x).strip() for x in items[:3] if str(x).strip()]
                )
                suffix = f" ({kcal} kcal)" if kcal is not None else ""
                if item_line:
                    lines.append(f"{prefix}  • {meal_name}: {item_line}{suffix}")
                else:
                    lines.append(f"{prefix}  • {meal_name}{suffix}")
    return lines


def render_rolling_day_request(plan_payload: dict[str, Any], requested_day: int) -> str:
    if not isinstance(plan_payload, dict):
        return "Abhi plan snapshot available nahi hai."
    rolling = plan_payload.get("rolling") or {}
    if not isinstance(rolling, dict):
        return "Rolling plan details abhi available nahi hain."
    unlocked = rolling.get("unlocked_days") or []
    if not isinstance(unlocked, list):
        unlocked = []
    if requested_day <= len(unlocked):
        entry = unlocked[requested_day - 1]
        if isinstance(entry, dict):
            lines = [f"Ye raha Day {requested_day} plan:"]
            lines.extend(_format_day_entry(entry, prefix=""))
            return "\n".join(lines).strip()
    detailed_day_limit = int(float(rolling.get("detailed_day_limit") or 2))
    hint = str(rolling.get("next_unlock_hint") or "").strip()
    return (
        f"Abhi detailed rolling plan Day 1-Day {max(1, detailed_day_limit)} tak unlocked hai.\n"
        f"Day {requested_day} unlock karne ke liye Day 1/2 completion + meal adherence share karo.\n"
        f"{hint}"
    ).strip()


def render_today_plan_view(plan_payload: dict[str, Any], *, day_name: str) -> str:
    if not isinstance(plan_payload, dict):
        return "Abhi stored plan available nahi hai. Bolo to fresh plan bana du."
    week_blocks = plan_payload.get("week_blocks") or []
    if not isinstance(week_blocks, list) or not week_blocks:
        return "Abhi day-wise plan unavailable hai. Bolo to fresh plan generate karte hain."
    target_day = _normalize_day_label(day_name)
    lines = [f"Aaj ka plan ({target_day}):"]
    for block in week_blocks[:12]:
        if not isinstance(block, dict):
            continue
        week_no = int(float(block.get("week") or 0))
        focus = str(block.get("focus") or "Focus").strip()
        days = block.get("days") or []
        if isinstance(days, list):
            chosen = None
            for d in days:
                if isinstance(d, dict) and _normalize_day_label(str(d.get("day") or "")) == target_day:
                    chosen = d
                    break
            if chosen is None and days and isinstance(days[0], dict):
                chosen = days[0]
            if chosen is not None:
                lines.append(f"\nWeek {week_no if week_no > 0 else '?'}: {focus}")
                lines.extend(_format_day_entry(chosen, prefix=""))
                return "\n".join(lines).strip()
    # No day-level details found: intelligent fallback from active week actions.
    first = week_blocks[0] if isinstance(week_blocks[0], dict) else {}
    focus = str(first.get("focus") or "Current block").strip()
    lines.append(f"\nWeek 1 Focus: {focus}")
    actions = first.get("actions") or []
    if isinstance(actions, list):
        for action in actions[:5]:
            text = str(action).strip()
            if text:
                lines.append(f"- {text}")
    lines.append("\nAgar chaho to day-wise detailed schedule regenerate kar sakta hu.")
    return "\n".join(lines).strip()


def render_today_diet_vs_actual(
    plan_payload: dict[str, Any],
    living_profile: dict[str, Any],
    *,
    day_name: str,
    today_iso: str,
) -> str:
    if not isinstance(plan_payload, dict):
        return "Abhi stored diet plan nahi mila. Bolo to fresh diet plan bana du."
    week_blocks = plan_payload.get("week_blocks") or []
    if not isinstance(week_blocks, list) or not week_blocks:
        return "Abhi diet plan snapshot unavailable hai. Fresh plan generate karte hain."

    target_day = _normalize_day_label(day_name)
    planned_blocks: list[dict[str, Any]] = []
    planned_target_kcal: int | None = None
    for block in week_blocks[:12]:
        if not isinstance(block, dict):
            continue
        days = block.get("days") or []
        if isinstance(days, list):
            candidate = None
            for d in days:
                if isinstance(d, dict) and _normalize_day_label(str(d.get("day") or "")) == target_day:
                    candidate = d
                    break
            if candidate is None and days and isinstance(days[0], dict):
                candidate = days[0]
            if isinstance(candidate, dict):
                meals = candidate.get("meals") or {}
                if isinstance(meals, dict):
                    planned_target_kcal = meals.get("target_kcal")
                    raw_blocks = meals.get("blocks") or []
                    if isinstance(raw_blocks, list):
                        planned_blocks = [b for b in raw_blocks if isinstance(b, dict)]
                break

    logs = (living_profile.get("logs") or {})
    nutrition = logs.get("nutrition_log") or []
    today_entries: list[dict[str, Any]] = []
    if isinstance(nutrition, list):
        for entry in nutrition:
            if not isinstance(entry, dict):
                continue
            local_date = str(entry.get("local_date") or "").strip()
            if local_date and local_date != today_iso:
                continue
            summary = str(entry.get("summary") or "").strip()
            if summary:
                today_entries.append(entry)

    metrics = ((logs.get("current_day") or {}).get("metrics") or {})
    try:
        intake_cals = int(float(metrics.get("intake_cals") or metrics.get("cals") or 0))
    except (TypeError, ValueError):
        intake_cals = 0
    lines = [f"Aaj ka diet plan vs actual ({target_day}):"]
    if planned_blocks:
        lines.append("\nPlan me aaj ke meals:")
        for mb in planned_blocks[:5]:
            meal_name = str(mb.get("meal") or "meal").strip()
            kcal = mb.get("kcal")
            items = mb.get("items") or []
            item_line = ", ".join([str(x).strip() for x in items[:4] if str(x).strip()])
            suffix = f" ({kcal} kcal)" if kcal is not None else ""
            if item_line:
                lines.append(f"- {meal_name}: {item_line}{suffix}")
            else:
                lines.append(f"- {meal_name}{suffix}")
    else:
        lines.append("\nPlan me aaj ke explicit meals available nahi mile.")

    if today_entries:
        lines.append("\nAapne aaj log kiya:")
        for entry in today_entries[:8]:
            summary = str(entry.get("summary") or "meal").strip()
            kcal = entry.get("estimated_calories")
            suffix = f" ({kcal} kcal)" if kcal not in (None, "") else ""
            lines.append(f"- {summary}{suffix}")
    else:
        lines.append("\nAaj abhi koi meal log nahi mila.")

    if planned_target_kcal is not None:
        remaining = int(planned_target_kcal) - intake_cals
        lines.append(
            f"\nCalories: plan target {int(planned_target_kcal)} kcal, logged {intake_cals} kcal, remaining {remaining} kcal."
        )
    return "\n".join(lines).strip()


def render_plan_view(plan_payload: dict[str, Any], *, show_full: bool) -> str:
    if not isinstance(plan_payload, dict):
        return "Abhi stored plan available nahi hai. Bolo to fresh plan bana du."
    week_blocks = plan_payload.get("week_blocks") or []
    if not isinstance(week_blocks, list):
        week_blocks = []
    day_actions = plan_payload.get("day_actions") or []
    if not isinstance(day_actions, list):
        day_actions = []

    if not week_blocks and not day_actions:
        return "Abhi plan snapshot empty hai. Bolo to fresh plan generate karte hain."

    meta = plan_payload.get("meta") or {}
    rolling_mode = bool((meta or {}).get("rolling_mode"))
    if rolling_mode:
        rolling = plan_payload.get("rolling") or {}
        roadmap = rolling.get("roadmap_weeks") or []
        unlocked = rolling.get("unlocked_days") or []
        detailed_limit = int(float(rolling.get("detailed_day_limit") or 2))
        lines = [
            "Rolling Plan Mode active:",
            f"- Abhi hum quality-first detailed Day 1-Day {max(1, detailed_limit)} build karte hain.",
            "- Jaise hi aap completion share karoge, next days unlock karte jayenge.",
            "",
            "Roadmap:",
        ]
        max_weeks = 12 if show_full else 4
        for week in roadmap[:max_weeks]:
            if not isinstance(week, dict):
                continue
            week_no = _safe_int(week.get("week")) or "?"
            focus = str(week.get("focus") or "Focus block").strip()
            lines.append(f"\nWeek {week_no}: {focus}")
            actions = week.get("actions") or []
            if isinstance(actions, list):
                for action in actions[:3]:
                    text = str(action).strip()
                    if text:
                        lines.append(f"- {text}")
        if isinstance(unlocked, list) and unlocked:
            lines.append("\nUnlocked detailed days:")
            for day_entry in unlocked[: max(1, detailed_limit)]:
                if isinstance(day_entry, dict):
                    lines.extend(_format_day_entry(day_entry, prefix="  "))
        policy = str(rolling.get("unlock_policy_text") or "").strip()
        if policy:
            lines.append(f"\n{policy}")
        lines.append("\nExample asks: 'What will be Day 3 plan?' or 'add core workout and show me the plan'.")
        return "\n".join(lines).strip()

    if show_full:
        blocks = week_blocks[:12]
        lines = ["Ye raha aapka full plan view:"]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            week_no = int(float(block.get("week") or 0))
            focus = str(block.get("focus") or "Focus block").strip()
            lines.append(f"\nWeek {week_no if week_no > 0 else '?'}: {focus}")
            actions = block.get("actions") or []
            if isinstance(actions, list):
                for action in actions[:7]:
                    text = str(action).strip()
                    if text:
                        lines.append(f"- {text}")
            days = block.get("days") or []
            if isinstance(days, list):
                for day_entry in days[:7]:
                    if not isinstance(day_entry, dict):
                        continue
                    lines.extend(_format_day_entry(day_entry, prefix="  "))
            if (not isinstance(days, list)) or (isinstance(days, list) and not days):
                lines.append("  Day-wise details not fully expanded for this week yet.")
                actions = block.get("actions") or []
                if isinstance(actions, list):
                    for action in actions[:3]:
                        text = str(action).strip()
                        if text:
                            lines.append(f"  • {text}")
        return "\n".join(lines).strip()

    # Default low-cost preview: show only week 1 + today's action style.
    week1 = None
    for block in week_blocks:
        if isinstance(block, dict) and int(float(block.get("week") or 0)) == 1:
            week1 = block
            break
    if week1 is None and week_blocks and isinstance(week_blocks[0], dict):
        week1 = week_blocks[0]

    lines = ["Ye raha Week 1 preview (cost-optimized view):"]
    if isinstance(week1, dict):
        focus = str(week1.get("focus") or "Starter block").strip()
        lines.append(f"\nWeek 1 Focus: {focus}")
        actions = week1.get("actions") or []
        if isinstance(actions, list):
            for action in actions[:7]:
                text = str(action).strip()
                if text:
                    lines.append(f"- {text}")
        days = week1.get("days") or []
        if isinstance(days, list) and days and isinstance(days[0], dict):
            lines.append("\nDay 1 sample:")
            lines.extend(_format_day_entry(days[0], prefix=""))
    elif day_actions:
        lines.append("\nWeek 1 Actions:")
        for action in day_actions[:7]:
            text = str(action).strip()
            if text:
                lines.append(f"- {text}")

    lines.append("\nAgar full 12-week view chahiye ho to bolo: 'Show full plan'.")
    return "\n".join(lines).strip()


def build_plan_intake_questionnaire(extra_questions: list[str] | None = None) -> str:
    lines = [
        "Perfect. Personalized plan banane ke liye ek hi reply me yeh details bhej do:",
        "",
        "1) Primary goal: (fat loss / muscle gain / running / swimming / mobility / other)",
        "2) Target + timeframe: (example: 21K in 16 weeks, 6kg fat loss in 12 weeks)",
        "3) Current level: beginner / intermediate / advanced",
        "4) Weekly availability: kitne din + kitna time per session",
        "5) Work/lifestyle: desk job / shift / field work + preferred workout timings",
        "6) Aggressiveness level: low / moderate / high",
        "7) Diet details: veg/non-veg, allergies, restrictions, budget, meal timings",
        "",
        "Format example:",
        "Goal: ... | Timeframe: ... | Level: ... | Availability: ... | Job: ... | Aggressiveness: ... | Diet: ...",
    ]
    if isinstance(extra_questions, list):
        cleaned = [str(q).strip() for q in extra_questions if str(q).strip()][:3]
        if cleaned:
            lines.append("")
            lines.append("Also confirm:")
            lines.extend([f"- {q}" for q in cleaned])
    return "\n".join(lines).strip()


def has_sufficient_plan_structure(
    *,
    week_blocks: list[dict[str, Any]] | None,
    day_actions: list[str] | None,
    horizon_weeks: int,
    plan_type: str = "hybrid",
) -> bool:
    blocks = week_blocks if isinstance(week_blocks, list) else []
    actions = day_actions if isinstance(day_actions, list) else []
    block_count = len([b for b in blocks if isinstance(b, dict)])
    action_count = len([a for a in actions if str(a).strip()])
    day_covered_weeks = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        days = block.get("days") or []
        if isinstance(days, list) and any(isinstance(d, dict) for d in days):
            day_covered_weeks += 1
    normalized_type = str(plan_type or "hybrid").strip().lower()
    if horizon_weeks >= 8 and normalized_type == "diet":
        return (block_count >= 3 and day_covered_weeks >= 3) or action_count >= 7
    if horizon_weeks >= 8:
        return block_count >= 4 and day_covered_weeks >= 4
    if horizon_weeks >= 4:
        return block_count >= 2 and day_covered_weeks >= 2
    if horizon_weeks >= 2:
        return block_count >= 2 or action_count >= 5
    return block_count >= 1 or action_count >= 3


def has_full_week_day_coverage(
    *,
    week_blocks: list[dict[str, Any]] | None,
    required_weeks: int,
    required_days_per_week: int,
) -> bool:
    blocks = week_blocks if isinstance(week_blocks, list) else []
    if required_weeks <= 0:
        return True
    target_days = max(1, min(7, int(required_days_per_week or 5)))
    valid_weeks = 0
    for block in blocks[:required_weeks]:
        if not isinstance(block, dict):
            continue
        days = block.get("days") or []
        if not isinstance(days, list):
            continue
        seen: set[str] = set()
        for d in days:
            if not isinstance(d, dict):
                continue
            seen.add(_normalize_day_label(str(d.get("day") or "")))
        if len(seen) >= target_days:
            valid_weeks += 1
    return valid_weeks >= required_weeks


def infer_required_training_days_per_week(
    *,
    user_message: str,
    living_profile: dict[str, Any],
    default_days: int = 5,
) -> int:
    target = max(1, min(7, int(default_days or 5)))
    text = str(user_message or "").lower()

    # Prefer explicit mention in current request: "5 days", "4 day/week", etc.
    match = re.search(r"\b([1-7])\s*(?:day|days)\b", text)
    if not match:
        match = re.search(r"\b([1-7])\s*(?:x|times)\s*(?:/|per)?\s*week\b", text)
    if match:
        try:
            return max(1, min(7, int(match.group(1))))
        except (TypeError, ValueError):
            pass

    # Optional profile hints if present.
    lifestyle = (living_profile.get("lifestyle") or {})
    for key in ("training_days_per_week", "workout_days_per_week", "available_days_per_week"):
        value = lifestyle.get(key)
        try:
            if value is not None:
                return max(1, min(7, int(float(value))))
        except (TypeError, ValueError):
            continue
    return target


def infer_detailed_day_limit(user_message: str, *, default_days: int = 2) -> int:
    text = str(user_message or "").lower()
    target = max(1, min(3, int(default_days or 2)))
    if any(k in text for k in ("3 day", "3-day", "three day", "next 3 days")):
        return 3
    if any(k in text for k in ("2 day", "2-day", "two day", "next 2 days")):
        return 2
    if any(k in text for k in ("today only", "day 1 only", "only today")):
        return 1
    return target


def infer_goal_anchor(user_message: str, living_profile: dict[str, Any]) -> str:
    text = str(user_message or "").strip()
    if text:
        return text[:240]
    psych = (living_profile.get("psychology") or {})
    core_why = str(psych.get("core_why") or "").strip()
    if core_why:
        return core_why[:240]
    return "Build sustainable health progress safely."


def _build_rolling_view(
    *,
    week_blocks: list[dict[str, Any]],
    required_training_days_per_week: int,
    detailed_day_limit: int,
) -> dict[str, Any]:
    roadmap_weeks: list[dict[str, Any]] = []
    unlocked_days: list[dict[str, Any]] = []
    target_days = max(1, min(3, int(detailed_day_limit or 2)))

    for block in week_blocks[:12]:
        if not isinstance(block, dict):
            continue
        roadmap_weeks.append(
            {
                "week": _safe_int(block.get("week")) or (len(roadmap_weeks) + 1),
                "focus": str(block.get("focus") or "Progress block").strip(),
                "actions": [
                    str(a).strip()
                    for a in (block.get("actions") or [])
                    if str(a).strip()
                ][:3],
            }
        )

    first_week = week_blocks[0] if week_blocks and isinstance(week_blocks[0], dict) else {}
    days = first_week.get("days") if isinstance(first_week, dict) else []
    if isinstance(days, list):
        for d in days:
            if isinstance(d, dict):
                unlocked_days.append(d)
            if len(unlocked_days) >= target_days:
                break

    if not unlocked_days and isinstance(first_week, dict):
        template = {
            "workout": {"title": str(first_week.get("focus") or "Workout"), "items": []},
            "meals": {"blocks": []},
        }
        for idx in range(target_days):
            unlocked_days.append({"day": WEEKDAY_ORDER[idx], **template})

    if unlocked_days and len(unlocked_days) < target_days:
        base = unlocked_days[0]
        used = {
            _normalize_day_label(str(d.get("day") or ""))
            for d in unlocked_days
            if isinstance(d, dict)
        }
        for day_label in WEEKDAY_ORDER:
            if len(unlocked_days) >= target_days:
                break
            if day_label in used:
                continue
            cloned = dict(base)
            cloned["day"] = day_label
            unlocked_days.append(cloned)
            used.add(day_label)

    return {
        "required_training_days_per_week": max(1, min(7, int(required_training_days_per_week or 5))),
        "detailed_day_limit": target_days,
        "roadmap_weeks": roadmap_weeks,
        "unlocked_days": unlocked_days[:target_days],
        "next_unlock_hint": "Share Day 1 completion + meal adherence to unlock next day.",
        "missing_inputs": [],
        "unlock_policy_text": "Building mode: next detailed days unlock progressively after daily check-ins.",
    }


def has_rolling_valid_structure(rolling_payload: dict[str, Any] | None) -> bool:
    if not isinstance(rolling_payload, dict):
        return False
    roadmap = rolling_payload.get("roadmap_weeks")
    unlocked = rolling_payload.get("unlocked_days")
    if not isinstance(roadmap, list) or not roadmap:
        return False
    if not isinstance(unlocked, list) or not unlocked:
        return False
    first_unlocked = unlocked[0]
    if not isinstance(first_unlocked, dict):
        return False
    return bool(str(first_unlocked.get("day") or "").strip())


def apply_plan_confirmation_if_any(
    living_profile: dict[str, Any], user_message: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    pending = active.get("pending_change_request")
    if not isinstance(pending, dict) or not pending.get("awaiting_confirmation"):
        return profile, None

    text = str(user_message or "").strip().lower()
    tokens = set(text.replace(".", " ").replace(",", " ").split())
    yes = bool(tokens & YES_WORDS) or text in YES_WORDS
    no = bool(tokens & NO_WORDS) or text in NO_WORDS
    if not yes and not no:
        return profile, None

    decision = "approved" if yes else "declined"
    change_note = str(pending.get("change_note") or "").strip()
    active["pending_change_request"] = {}
    profile["plans"]["active"] = active
    return profile, {"decision": decision, "change_note": change_note}


def upsert_pending_change_request(
    living_profile: dict[str, Any], user_message: str
) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    active["pending_change_request"] = {
        "awaiting_confirmation": True,
        "change_note": str(user_message or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    profile["plans"]["active"] = active
    return profile


def build_plan_compact_for_prompt(living_profile: dict[str, Any]) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    current_block = active.get("current_block")
    if not isinstance(current_block, dict):
        current_block = {}
    return {
        "plan_id": active.get("plan_id") or "",
        "version": _safe_int(active.get("version")),
        "status": active.get("status") or "none",
        "type": active.get("type") or "hybrid",
        "horizon": active.get("horizon") or "weekly",
        "horizon_weeks": _safe_int(active.get("horizon_weeks")),
        "constraints": active.get("constraints") or {},
        "current_block": current_block,
        "week_blocks": active.get("week_blocks") or [],
        "day_actions": active.get("day_actions") or [],
        "goal_anchor": active.get("goal_anchor") or "",
        "rolling": active.get("rolling") or {},
    }


def infer_plan_type_and_horizon(user_message: str) -> tuple[str, str]:
    text = str(user_message or "").lower()
    if any(k in text for k in ("diet", "meal", "nutrition", "kha", "eat")):
        plan_type = "diet"
    elif any(k in text for k in ("workout", "exercise", "training", "run", "gym")):
        plan_type = "training"
    else:
        plan_type = "hybrid"

    if any(k in text for k in ("today", "tomorrow", "daily", "aaj", "kal")):
        horizon = "daily"
    elif any(k in text for k in ("month", "monthly", "12 week", "8 week")):
        horizon = "monthly"
    else:
        horizon = "weekly"
    return plan_type, horizon


def _extract_day_actions(plan_text: str) -> list[str]:
    lines = [line.strip(" -•\t") for line in str(plan_text or "").splitlines()]
    actions = [line for line in lines if len(line) > 6]
    if not actions:
        chunks = [c.strip() for c in re.split(r"[.!?]\s+", str(plan_text or "")) if c.strip()]
        actions = chunks[:5]
    return actions[:7]


def _build_week_blocks_from_actions(day_actions: list[str]) -> list[dict[str, Any]]:
    if not day_actions:
        return []
    return [
        {
            "week": 1,
            "focus": "Starter execution block",
            "actions": day_actions[:7],
        }
    ]


def fetch_latest_plan_version(phone_number: str) -> dict[str, Any] | None:
    cache_key = f"plan_latest:{phone_number}"
    cached = get_cached(cache_key)
    if cached is not MISSING:
        return cached
    response = (
        supabase.table("plan_versions")
        .select("plan_id,version,plan_payload,status,change_reason,created_at")
        .eq("phone_number", phone_number)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        set_cached(cache_key, None, ttl_seconds=20)
        return None
    row = rows[0] or {}
    result = row if isinstance(row, dict) else None
    set_cached(cache_key, result, ttl_seconds=20)
    return result


def persist_plan_version(
    *,
    phone_number: str,
    living_profile: dict[str, Any],
    plan_text: str,
    change_reason: str,
    horizon_weeks: int = 12,
    plan_type: str = "hybrid",
    horizon: str = "weekly",
    week_blocks: list[dict[str, Any]] | None = None,
    day_actions: list[str] | None = None,
    required_training_days_per_week: int = 5,
    detailed_day_limit: int = 2,
    goal_anchor: str = "",
    rolling_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = ensure_plan_state(living_profile)
    active = profile["plans"]["active"]
    current_plan_id = str(active.get("plan_id") or "").strip()
    current_version = _safe_int(active.get("version"))
    if not current_plan_id:
        current_plan_id = f"plan-{uuid4().hex[:10]}"
        current_version = 0
    new_version = current_version + 1
    if not isinstance(day_actions, list) or not day_actions:
        day_actions = _extract_day_actions(plan_text)
    else:
        day_actions = [str(x).strip() for x in day_actions if str(x).strip()][:7]
    if not isinstance(week_blocks, list) or not week_blocks:
        week_blocks = _build_week_blocks_from_actions(day_actions)
    else:
        week_blocks = week_blocks[:12]
    resolved_rolling = (
        rolling_payload
        if isinstance(rolling_payload, dict) and has_rolling_valid_structure(rolling_payload)
        else _build_rolling_view(
            week_blocks=week_blocks,
            required_training_days_per_week=required_training_days_per_week,
            detailed_day_limit=detailed_day_limit,
        )
    )
    payload = {
        "meta": {
            "plan_id": current_plan_id,
            "version": new_version,
            "horizon_weeks": horizon_weeks,
            "type": plan_type,
            "horizon": horizon,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "rolling_mode": True,
        },
        "type": plan_type,
        "horizon": horizon,
        "current_block": {"summary_text": plan_text},
        "week_blocks": week_blocks,
        "day_actions": day_actions,
        "constraints": active.get("constraints") or {},
        "goal_anchor": str(goal_anchor or "").strip()[:240],
        "rolling": resolved_rolling,
    }
    (
        supabase.table("plan_versions")
        .insert(
            {
                "phone_number": phone_number,
                "plan_id": current_plan_id,
                "version": new_version,
                "status": "active",
                "change_reason": change_reason,
                "plan_payload": payload,
            }
        )
        .execute()
    )
    invalidate_cached(f"plan_latest:{phone_number}")
    active["plan_id"] = current_plan_id
    active["version"] = new_version
    active["status"] = "active"
    active["type"] = plan_type
    active["horizon"] = horizon
    active["horizon_weeks"] = horizon_weeks
    active["current_block"] = payload["current_block"]
    active["week_blocks"] = week_blocks
    active["day_actions"] = day_actions
    active["goal_anchor"] = payload.get("goal_anchor") or ""
    active["rolling"] = payload.get("rolling") or {}
    active["pending_change_request"] = {}
    profile["plans"]["active"] = active
    change_log = profile["plans"].get("change_log") or []
    if not isinstance(change_log, list):
        change_log = []
    change_log.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "plan_id": current_plan_id,
            "version": new_version,
            "reason": change_reason,
        }
    )
    if len(change_log) > 15:
        change_log = change_log[-15:]
    profile["plans"]["change_log"] = change_log
    return profile

