from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, TypeVar

import google.generativeai as genai

T = TypeVar("T")


def _looks_like_gemini_api_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    module_name = str(getattr(type(exc), "__module__", "")).lower()
    class_name = str(getattr(type(exc), "__name__", "")).lower()
    return (
        "google" in module_name
        or "api_core" in module_name
        or "serviceunavailable" in class_name
        or "toomanyrequests" in class_name
        or "resourceexhausted" in class_name
        or any(token in text for token in ("429", "499", "503", "quota", "rate limit", "service unavailable"))
    )


async def run_json_contract(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict[str, Any],
    max_retries: int,
    validator: Callable[[dict[str, Any]], T],
    on_attempt_response: Callable[[dict[str, Any], str, int, Any], None] | None = None,
) -> T:
    previous_error = ""
    previous_output: dict[str, Any] = {}
    retries = max(1, int(max_retries or 1))

    def _call_once(attempt_payload: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt,
            )
            response = model.generate_content(
                json.dumps(attempt_payload, ensure_ascii=False),
                generation_config={"response_mime_type": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            if _looks_like_gemini_api_error(exc):
                print(
                    "[GEMINI_API_ERROR][run_json_contract] "
                    f"model={model_name} elapsed_ms={elapsed_ms} "
                    f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                )
            raise
        response_text = response.text or "{}"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if on_attempt_response is not None:
            try:
                on_attempt_response(attempt_payload, response_text, elapsed_ms, response)
            except Exception:
                pass
        raw = json.loads(response_text.strip())
        if not isinstance(raw, dict):
            raise ValueError("invalid_json_object")
        return raw

    for attempt in range(1, retries + 1):
        attempt_payload = {
            **payload,
            "retry_context": (
                {
                    "attempt": attempt,
                    "previous_failure_reason": previous_error,
                    "previous_output": previous_output,
                    "instruction": "Fix previous failure reason and return strict schema-valid JSON only.",
                }
                if attempt > 1
                else {}
            ),
        }
        try:
            raw = await asyncio.to_thread(_call_once, attempt_payload)
            if raw == previous_output and attempt > 1:
                raise ValueError("repeated_same_output")
            return validator(raw)
        except Exception as exc:  # noqa: BLE001
            previous_error = str(exc)
            if _looks_like_gemini_api_error(exc):
                print(
                    "[GEMINI_API_ERROR][run_json_contract][attempt_failed] "
                    f"model={model_name} attempt={attempt}/{retries} "
                    f"error_type={type(exc).__name__} error={previous_error[:300]}"
                )
            if "raw" in locals() and isinstance(raw, dict):
                previous_output = raw

    raise RuntimeError(previous_error or "contract_retries_exhausted")
