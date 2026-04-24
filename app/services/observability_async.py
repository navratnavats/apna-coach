from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from app.clients.supabase_client import supabase
from app.config import OBS_LOG_FULL_PAYLOAD, OBS_PREVIEW_MAX_CHARS

_Q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=5000)
_WORKER_STARTED = False
_LOCK = threading.Lock()
_TOKEN_AGG_LOCK = threading.Lock()
_TOKEN_AGG: dict[str, dict[str, int]] = {}


def _start_worker_if_needed() -> None:
    global _WORKER_STARTED
    with _LOCK:
        if _WORKER_STARTED:
            return
        t = threading.Thread(target=_worker_loop, daemon=True, name="observability-writer")
        t.start()
        _WORKER_STARTED = True


def _worker_loop() -> None:
    while True:
        item = _Q.get()
        try:
            kind = item.get("kind")
            payload = item.get("payload") or {}
            if kind == "llm_call":
                supabase.table("llm_call_events").insert(payload).execute()
            elif kind == "operation_metric":
                supabase.table("operation_metrics").upsert(
                    payload,
                    on_conflict="operation_id",
                ).execute()
        except Exception:
            pass
        finally:
            _Q.task_done()


def extract_gemini_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
    completion = int(getattr(usage, "candidates_token_count", 0) or 0)
    total = int(getattr(usage, "total_token_count", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _shorten(value: Any, max_chars: int = OBS_PREVIEW_MAX_CHARS) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False) if value is not None else ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...<truncated>"


def enqueue_llm_call_event(
    *,
    operation_id: str | None,
    trace_id: str | None,
    turn_id: str | None,
    phone_number: str | None,
    agent: str,
    stage: str,
    model: str,
    latency_ms: int,
    request_payload: Any,
    response_text: str,
    usage: dict[str, int] | None = None,
    status: str = "ok",
    error_text: str = "",
) -> None:
    _start_worker_if_needed()
    tok = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    request_preview = _shorten(request_payload) if OBS_LOG_FULL_PAYLOAD else ""
    response_preview = _shorten(response_text) if OBS_LOG_FULL_PAYLOAD else ""
    request_chars = len(_shorten(request_payload, 4000))
    response_chars = len(str(response_text or ""))
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id or trace_id or turn_id,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "phone_number": phone_number,
        "agent": agent,
        "stage": stage,
        "model": model,
        "status": status,
        "latency_ms": int(latency_ms or 0),
        "prompt_tokens": int(tok.get("prompt_tokens") or 0),
        "completion_tokens": int(tok.get("completion_tokens") or 0),
        "total_tokens": int(tok.get("total_tokens") or 0),
        "request_chars": request_chars,
        "response_chars": response_chars,
        "request_preview": request_preview,
        "response_preview": response_preview,
        "error_text": _shorten(error_text, 400),
    }
    try:
        _Q.put_nowait({"kind": "llm_call", "payload": payload})
    except queue.Full:
        return
    op_key = str(payload.get("operation_id") or "").strip()
    if op_key:
        with _TOKEN_AGG_LOCK:
            row = _TOKEN_AGG.setdefault(
                op_key,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            row["prompt_tokens"] += int(tok.get("prompt_tokens") or 0)
            row["completion_tokens"] += int(tok.get("completion_tokens") or 0)
            row["total_tokens"] += int(tok.get("total_tokens") or 0)


def enqueue_operation_metric(
    *,
    operation_id: str,
    trace_id: str | None,
    turn_id: str | None,
    phone_number: str,
    total_latency_ms: int,
    llm_calls_estimate: int,
    final_status: str,
    routed_intent: str,
    router_confidence: str,
    intent_source: str,
) -> None:
    _start_worker_if_needed()
    with _TOKEN_AGG_LOCK:
        token_totals = _TOKEN_AGG.pop(
            operation_id,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "trace_id": trace_id,
        "turn_id": turn_id,
        "phone_number": phone_number,
        "total_latency_ms": int(total_latency_ms or 0),
        "llm_calls_estimate": int(llm_calls_estimate or 0),
        "final_status": final_status or "ok",
        "routed_intent": routed_intent or "",
        "router_confidence": router_confidence or "",
        "intent_source": intent_source or "",
        "total_prompt_tokens": int(token_totals.get("prompt_tokens") or 0),
        "total_completion_tokens": int(token_totals.get("completion_tokens") or 0),
        "total_tokens": int(token_totals.get("total_tokens") or 0),
    }
    try:
        _Q.put_nowait({"kind": "operation_metric", "payload": payload})
    except queue.Full:
        return
