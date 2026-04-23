from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.clients.supabase_client import supabase
from app.config import TWILIO_WHATSAPP_FROM, WHATSAPP_VERIFY_TOKEN
from app.services.coach_reply import generate_coach_reply
from app.services.memory_clerk import (
    ai_memory_clerk,
    ai_nutrition_from_image,
    deep_merge_profile,
    load_default_living_profile,
    next_onboarding_prompt,
)
from app.services.twilio_messaging import send_whatsapp_message

router = APIRouter()
RECENT_EVENT_TTL_SECONDS = 45
_recent_twilio_events: dict[str, float] = {}
_phone_locks: dict[str, asyncio.Lock] = {}
AI_STAGE_TIMEOUT_SECONDS = 25
TOTAL_PIPELINE_TIMEOUT_SECONDS = 45
OUTBOUND_STATUS_VALUES = {"sent", "delivered", "read", "failed", "undelivered"}


def _safe_twiml_message(text: str) -> str:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<Response>
    <Message>{escaped}</Message>
</Response>"""


def _cleanup_recent_events(now_ts: float) -> None:
    expired = [
        key
        for key, seen_ts in _recent_twilio_events.items()
        if now_ts - seen_ts > RECENT_EVENT_TTL_SECONDS
    ]
    for key in expired:
        _recent_twilio_events.pop(key, None)


async def _process_and_send_twilio_reply(
    phone_number: str, body: str, media_url: Optional[str] = None
) -> None:
    request_started_at = time.perf_counter()
    reply_message = "Bhai, give me a second, optimizing your plan..."
    phone_lock = _phone_locks.setdefault(phone_number, asyncio.Lock())

    async def _run_pipeline() -> None:
        nonlocal reply_message
        existing_user_resp = (
            supabase.table("users")
            .select("id, living_profile")
            .eq("phone_number", phone_number)
            .limit(1)
            .execute()
        )
        existing_rows = existing_user_resp.data or []
        print(f"[Twilio Flow] Existing user found: {bool(existing_rows)}")

        base_profile: dict[str, Any]
        if not existing_rows:
            default_profile = load_default_living_profile()
            (
                supabase.table("users")
                .insert({"phone_number": phone_number, "living_profile": default_profile})
                .execute()
            )
            base_profile = default_profile
        else:
            base_profile = existing_rows[0].get("living_profile") or {}

        memory_started_at = time.perf_counter()
        extracted_updates = await asyncio.wait_for(
            ai_memory_clerk(body, base_profile),
            timeout=AI_STAGE_TIMEOUT_SECONDS,
        )
        memory_elapsed_ms = int((time.perf_counter() - memory_started_at) * 1000)
        print(f"[Twilio Timing] Memory clerk completed in {memory_elapsed_ms} ms")
        print(
            f"[Twilio Flow] Extracted updates: {json.dumps(extracted_updates, ensure_ascii=False)}"
        )
        merged_profile = deep_merge_profile(base_profile, extracted_updates)
        nutrition_logged_this_turn = False

        if media_url:
            vision_updates = await asyncio.wait_for(
                ai_nutrition_from_image(media_url, merged_profile),
                timeout=AI_STAGE_TIMEOUT_SECONDS,
            )
            food_log_entry = vision_updates.get("food_log_entry")
            if isinstance(food_log_entry, dict):
                logs = merged_profile.get("logs") or {}
                nutrition_log = logs.get("nutrition_log") or []
                if not isinstance(nutrition_log, list):
                    nutrition_log = []
                nutrition_log.append(food_log_entry)
                logs["nutrition_log"] = nutrition_log
                merged_profile["logs"] = logs
                nutrition_logged_this_turn = True
                print("[Twilio Flow] Nutrition entry appended from image.")

        completion_prompt = next_onboarding_prompt(merged_profile)
        if "100% complete" in completion_prompt:
            merged_profile["onboarding_complete"] = True

        (
            supabase.table("users")
            .update({"living_profile": merged_profile})
            .eq("phone_number", phone_number)
            .execute()
        )
        print("[Twilio Flow] Living profile update saved to Supabase.")

        refreshed_resp = (
            supabase.table("users")
            .select("living_profile")
            .eq("phone_number", phone_number)
            .limit(1)
            .execute()
        )
        refreshed_rows = refreshed_resp.data or []
        fresh_profile = (
            refreshed_rows[0].get("living_profile") if refreshed_rows else merged_profile
        )

        logs = fresh_profile.get("logs") or {}
        current_count = logs.get("coach_message_count", 0)
        try:
            next_count = int(current_count) + 1
        except (TypeError, ValueError):
            next_count = 1
        logs["coach_message_count"] = next_count
        fresh_profile["logs"] = logs

        (
            supabase.table("users")
            .update({"living_profile": fresh_profile})
            .eq("phone_number", phone_number)
            .execute()
        )

        coach_started_at = time.perf_counter()
        coach_reply = await asyncio.wait_for(
            generate_coach_reply(
                body,
                fresh_profile,
                session_context={"nutrition_logged_this_turn": nutrition_logged_this_turn},
            ),
            timeout=AI_STAGE_TIMEOUT_SECONDS,
        )
        coach_elapsed_ms = int((time.perf_counter() - coach_started_at) * 1000)
        print(f"[Twilio Timing] Coach reply completed in {coach_elapsed_ms} ms")
        if coach_reply.strip():
            reply_message = coach_reply.strip()

    try:
        async with phone_lock:
            await asyncio.wait_for(
                _run_pipeline(),
                timeout=TOTAL_PIPELINE_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        print(f"[Twilio Error] Pipeline timeout for {phone_number}")
        reply_message = (
            "Bhai, thoda load zyada tha. Tension mat le, retry kar raha hoon. "
            "Please send the message once again."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio Error] Brain pipeline failed: {exc}")
        reply_message = (
            "Bhai, AI service abhi thoda busy hai. Tension mat le, 20-30 seconds me "
            "dobara ping kar."
        )
    finally:
        total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
        print(f"[Twilio Timing] Total async processing time: {total_elapsed_ms} ms")

    try:
        await send_whatsapp_message(phone_number, reply_message)
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio Outbound Error] Failed to send final message: {exc}")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "bhai_is_alive"}


@router.get("/webhook")
async def verify_whatsapp_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_challenge: Optional[int] = Query(default=None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
) -> int:
    is_valid_request = (
        hub_mode == "subscribe"
        and hub_verify_token == WHATSAPP_VERIFY_TOKEN
        and hub_challenge is not None
    )

    if not is_valid_request:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        )

    return hub_challenge


@router.post("/webhook")
async def receive_whatsapp_webhook(request: Request) -> JSONResponse:
    payload: Any = await request.json()
    print("Incoming WhatsApp webhook payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return JSONResponse(content={"status": "ok"})


@router.post("/twilio-webhook")
async def receive_twilio_webhook(
    background_tasks: BackgroundTasks,
    From: Optional[str] = Form(None),
    Body: Optional[str] = Form(None),
    NumMedia: Optional[str] = Form(None),
    MediaUrl0: Optional[str] = Form(None),
    MessageStatus: Optional[str] = Form(None),
    SmsStatus: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
) -> Response:
    if not From:
        return Response(
            content=_safe_twiml_message("Status callback received."),
            media_type="application/xml",
        )

    phone_number = From.removeprefix("whatsapp:").strip()
    incoming_text = (Body or "").strip()
    print(f"[Twilio Incoming] {phone_number} says: {incoming_text}")
    media_count = 0
    try:
        media_count = int(NumMedia or "0")
    except (TypeError, ValueError):
        media_count = 0
    media_url = MediaUrl0 if media_count > 0 and MediaUrl0 else None
    if media_url:
        print(f"[Twilio Incoming] Media detected. MediaUrl0 present for {phone_number}.")

    # Guard 1: status callbacks are not user chat messages.
    # IMPORTANT: inbound user events may carry SmsStatus=received, so do not drop
    # them when actual message content (text/media) is present.
    callback_status = (MessageStatus or SmsStatus or "").strip().lower()
    has_user_content = bool(incoming_text) or media_count > 0
    is_outbound_status_callback = callback_status in OUTBOUND_STATUS_VALUES
    if is_outbound_status_callback and not has_user_content:
        print(
            f"[Twilio Event] type=status_callback from={phone_number} "
            f"status={callback_status}"
        )
        return Response(
            content=_safe_twiml_message("Status callback received."),
            media_type="application/xml",
        )

    # Guard 2: ignore empty non-content events.
    if not incoming_text and media_count == 0:
        print(f"[Twilio Event] type=empty_filtered from={phone_number}")
        return Response(
            content=_safe_twiml_message("No message content received."),
            media_type="application/xml",
        )

    # Guard 3: ignore sandbox-origin system echoes/events.
    sandbox_source = TWILIO_WHATSAPP_FROM.removeprefix("whatsapp:").strip()
    if sandbox_source and phone_number == sandbox_source and not incoming_text and media_count == 0:
        print(f"[Twilio Event] type=sandbox_filtered from={phone_number}")
        return Response(
            content=_safe_twiml_message("Sandbox event ignored."),
            media_type="application/xml",
        )

    # Guard 4: dedupe near-identical repeated events/retries.
    now_ts = time.time()
    _cleanup_recent_events(now_ts)
    event_key = f"{phone_number}|{incoming_text}|{media_url or ''}|{MessageSid or ''}"
    if event_key in _recent_twilio_events:
        print(f"[Twilio Event] type=duplicate from={phone_number}")
        return Response(
            content=_safe_twiml_message("Duplicate event skipped."),
            media_type="application/xml",
        )
    _recent_twilio_events[event_key] = now_ts
    print(
        f"[Twilio Event] type=real_user from={phone_number} "
        f"has_text={bool(incoming_text)} media_count={media_count}"
    )

    background_tasks.add_task(
        _process_and_send_twilio_reply, phone_number, incoming_text, media_url
    )
    print("[Twilio Ack] Responding immediately; processing reply in background.")
    return Response(
        content=_safe_twiml_message(
            "Bhai, got your message. Processing with AI now, sending full reply shortly."
        ),
        media_type="application/xml",
    )

