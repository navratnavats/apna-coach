from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def log_agent_event(
    *,
    agent: str,
    stage: str,
    status: str = "ok",
    trace_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Structured single-line logs for end-to-end agent flow tracing.
    """
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "stage": stage,
        "status": status,
    }
    if trace_id:
        payload["trace_id"] = trace_id
    if details:
        payload["details"] = details
    print(f"[AgentTrace] {json.dumps(payload, ensure_ascii=False)}")
