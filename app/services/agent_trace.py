from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

from app.clients.supabase_client import supabase

_TRACE_CONTEXT: ContextVar[dict[str, str]] = ContextVar("agent_trace_context", default={})


def set_trace_context(
    *, trace_id: str | None = None, turn_id: str | None = None, phone_number: str | None = None
) -> Token:
    context: dict[str, str] = {}
    if trace_id:
        context["trace_id"] = trace_id
    if turn_id:
        context["turn_id"] = turn_id
    if phone_number:
        context["phone_number"] = phone_number
    return _TRACE_CONTEXT.set(context)


def reset_trace_context(token: Token) -> None:
    _TRACE_CONTEXT.reset(token)


def _persist_trace_event(payload: dict[str, Any]) -> None:
    try:
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}
        (
            supabase.table("agent_trace_events")
            .insert(
                {
                    "ts": payload.get("ts"),
                    "trace_id": payload.get("trace_id"),
                    "turn_id": payload.get("turn_id"),
                    "phone_number": payload.get("phone_number"),
                    "agent": payload.get("agent"),
                    "stage": payload.get("stage"),
                    "status": payload.get("status"),
                    "details": details,
                }
            )
            .execute()
        )
    except Exception:
        # Trace persistence must never break the user flow.
        return


def log_agent_event(
    *,
    agent: str,
    stage: str,
    status: str = "ok",
    trace_id: str | None = None,
    turn_id: str | None = None,
    phone_number: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Structured single-line logs for end-to-end agent flow tracing.
    """
    context = _TRACE_CONTEXT.get() or {}
    effective_trace_id = trace_id or context.get("trace_id")
    effective_turn_id = turn_id or context.get("turn_id")
    effective_phone = phone_number or context.get("phone_number")

    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "stage": stage,
        "status": status,
    }
    if effective_trace_id:
        payload["trace_id"] = effective_trace_id
    if effective_turn_id:
        payload["turn_id"] = effective_turn_id
    elif effective_trace_id and str(effective_trace_id).startswith("turn-"):
        payload["turn_id"] = effective_trace_id
    resolved_phone = effective_phone
    if not resolved_phone and isinstance(details, dict):
        maybe_phone = details.get("phone_number")
        if isinstance(maybe_phone, str) and maybe_phone.strip():
            resolved_phone = maybe_phone.strip()
    if resolved_phone:
        payload["phone_number"] = resolved_phone
    if details:
        payload["details"] = details
    # Keep terminal noise low: print only warning/error states.
    normalized_status = str(status or "ok").strip().lower()
    if normalized_status in {"warn", "warning", "error", "failed"}:
        print(f"[AgentTrace] {json.dumps(payload, ensure_ascii=False)}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_persist_trace_event, payload))
    except RuntimeError:
        _persist_trace_event(payload)
