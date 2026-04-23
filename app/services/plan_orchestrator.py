from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from app.clients.supabase_client import supabase

PLAN_INTENTS = {
    "plan_create_request",
    "plan_status_query",
    "plan_edit_request",
    "plan_change_signal",
}
YES_WORDS = {"yes", "haan", "ha", "y", "ok", "okay", "kar", "karo", "do it"}
NO_WORDS = {"no", "nahi", "mat", "cancel", "stop", "nah"}


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
    plans["active"] = active
    plans.setdefault("change_log", [])
    profile["plans"] = plans
    return profile


def classify_plan_intent_fallback(user_message: str) -> str | None:
    text = str(user_message or "").strip().lower()
    if not text:
        return None
    if any(k in text for k in ("12 week", "8 week", "plan for tomorrow", "diet plan", "meal plan")):
        return "plan_create_request"
    if any(k in text for k in ("edit plan", "change plan", "modify plan", "adjust plan")):
        return "plan_edit_request"
    if any(k in text for k in ("vacation", "missed today", "traveling", "trip")):
        return "plan_change_signal"
    if any(k in text for k in ("what next week", "tomorrow plan", "this week plan")):
        return "plan_status_query"
    return None


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
        return None
    row = rows[0] or {}
    return row if isinstance(row, dict) else None


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
        week_blocks = week_blocks[:4]
    payload = {
        "meta": {
            "plan_id": current_plan_id,
            "version": new_version,
            "horizon_weeks": horizon_weeks,
            "type": plan_type,
            "horizon": horizon,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "type": plan_type,
        "horizon": horizon,
        "current_block": {"summary_text": plan_text},
        "week_blocks": week_blocks,
        "day_actions": day_actions,
        "constraints": active.get("constraints") or {},
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
    active["plan_id"] = current_plan_id
    active["version"] = new_version
    active["status"] = "active"
    active["type"] = plan_type
    active["horizon"] = horizon
    active["horizon_weeks"] = horizon_weeks
    active["current_block"] = payload["current_block"]
    active["week_blocks"] = week_blocks
    active["day_actions"] = day_actions
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

