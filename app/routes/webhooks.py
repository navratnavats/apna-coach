from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.clients.supabase_client import supabase
from app.config import WHATSAPP_VERIFY_TOKEN
from app.services.coach_reply import generate_coach_reply
from app.services.memory_clerk import (
    ai_memory_clerk,
    deep_merge_profile,
    load_default_living_profile,
    next_onboarding_prompt,
)
from app.services.twilio_messaging import send_whatsapp_message

router = APIRouter()


def _safe_twiml_message(text: str) -> str:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<Response>
    <Message>{escaped}</Message>
</Response>"""


async def _process_and_send_twilio_reply(phone_number: str, body: str) -> None:
    request_started_at = time.perf_counter()
    reply_message = "Bhai, give me a second, optimizing your plan..."

    try:
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
        extracted_updates = await ai_memory_clerk(body, base_profile)
        memory_elapsed_ms = int((time.perf_counter() - memory_started_at) * 1000)
        print(f"[Twilio Timing] Memory clerk completed in {memory_elapsed_ms} ms")
        print(
            f"[Twilio Flow] Extracted updates: {json.dumps(extracted_updates, ensure_ascii=False)}"
        )
        merged_profile = deep_merge_profile(base_profile, extracted_updates)

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
        coach_reply = await generate_coach_reply(body, fresh_profile)
        coach_elapsed_ms = int((time.perf_counter() - coach_started_at) * 1000)
        print(f"[Twilio Timing] Coach reply completed in {coach_elapsed_ms} ms")
        if coach_reply.strip():
            reply_message = coach_reply.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio Error] Brain pipeline failed: {exc}")
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
) -> Response:
    if not From or not Body:
        return Response(
            content=_safe_twiml_message("Status callback received."),
            media_type="application/xml",
        )

    phone_number = From.removeprefix("whatsapp:").strip()
    print(f"[Twilio Incoming] {phone_number} says: {Body}")
    background_tasks.add_task(_process_and_send_twilio_reply, phone_number, Body)
    print("[Twilio Ack] Responding immediately; processing reply in background.")
    return Response(
        content=_safe_twiml_message(
            "Bhai, got your message. Processing with AI now, sending full reply shortly."
        ),
        media_type="application/xml",
    )

