from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.clients.supabase_client import supabase
from app.config import TWILIO_WHATSAPP_FROM, WHATSAPP_VERIFY_TOKEN
from app.services.agent_trace import log_agent_event
from app.services.bio_math_agent import (
    calculate_activity_burn,
    calculate_net_deficit,
    compute_daily_targets_if_ready,
    normalize_activity_for_burn,
)
from app.services.coach_reply import generate_coach_reply
from app.services.memory_clerk import (
    ai_memory_clerk,
    ai_nutrition_from_image,
    ai_transcribe_voice_note,
    deep_merge_profile,
    load_default_living_profile,
    next_onboarding_prompt,
)
from app.services.intent_router import classify_router_intent
from app.services.profile_schema_guard import sanitize_memory_updates
from app.services.twilio_messaging import send_whatsapp_message

router = APIRouter()
RECENT_EVENT_TTL_SECONDS = 45
_recent_twilio_events: dict[str, float] = {}
_phone_locks: dict[str, asyncio.Lock] = {}
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


def _is_same_utc_day(iso_ts: str, now_utc: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed_utc = parsed.astimezone(timezone.utc)
        return parsed_utc.date() == now_utc.date()
    except Exception:  # noqa: BLE001
        return False


def _recalculate_current_day_calories(logs: dict[str, Any]) -> int:
    nutrition_log = logs.get("nutrition_log") or []
    if not isinstance(nutrition_log, list):
        return 0

    now_utc = datetime.now(timezone.utc)
    total = 0.0
    for entry in nutrition_log:
        if not isinstance(entry, dict):
            continue

        logged_at = str(entry.get("logged_at") or "").strip()
        if logged_at and not _is_same_utc_day(logged_at, now_utc):
            continue

        try:
            total += float(entry.get("estimated_calories", 0) or 0)
        except (TypeError, ValueError):
            continue

    return int(round(total))


async def _process_and_send_twilio_reply(
    phone_number: str,
    body: str,
    media_url: Optional[str] = None,
    media_content_type: Optional[str] = None,
    trace_id: str | None = None,
) -> None:
    request_started_at = time.perf_counter()
    reply_message = "Bhai, give me a second, optimizing your plan..."
    phone_lock = _phone_locks.setdefault(phone_number, asyncio.Lock())

    async def _run_pipeline() -> None:
        nonlocal reply_message
        log_agent_event(
            agent="front_desk",
            stage="pipeline_start",
            trace_id=trace_id,
            details={
                "phone_number": phone_number,
                "has_media": bool(media_url),
                "media_type": media_content_type or "",
            },
        )
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

        effective_user_text = body
        voice_note_logged_this_turn = False
        source_hint = "text"

        if media_url and (media_content_type or "").lower().startswith("audio/"):
            audio_result = await ai_transcribe_voice_note(
                media_url, media_content_type or "audio/ogg", trace_id=trace_id
            )
            transcript = str(audio_result.get("transcript") or "").strip()
            if transcript:
                voice_note_logged_this_turn = True
                source_hint = "voice"
                effective_user_text = (
                    transcript
                    if not effective_user_text
                    else f"{effective_user_text}\n\nVoice note transcript: {transcript}"
                )
                print("[Twilio Flow] Voice note transcribed and added to user context.")

        router_result = await classify_router_intent(
            effective_user_text,
            trace_id=trace_id,
        )
        routed_intent = str(router_result.get("primary_intent") or "general_chat")
        routed_confidence = str(router_result.get("confidence") or "low")
        log_agent_event(
            agent="router",
            stage="applied",
            trace_id=trace_id,
            details={"intent": routed_intent, "confidence": routed_confidence},
        )

        memory_started_at = time.perf_counter()
        extracted_updates: dict[str, Any] = {}
        if routed_intent != "burn_query":
            extracted_updates = await ai_memory_clerk(
                effective_user_text,
                base_profile,
                source_hint=source_hint,
                trace_id=trace_id,
            )
            extracted_updates = sanitize_memory_updates(
                extracted_updates,
                trace_id=trace_id,
            )
        else:
            log_agent_event(
                agent="memory_clerk",
                stage="skipped",
                status="burn_query_route",
                trace_id=trace_id,
            )
        memory_elapsed_ms = int((time.perf_counter() - memory_started_at) * 1000)
        print(f"[Twilio Timing] Memory clerk completed in {memory_elapsed_ms} ms")
        print(
            f"[Twilio Flow] Extracted updates: {json.dumps(extracted_updates, ensure_ascii=False)}"
        )
        extracted_logs = extracted_updates.get("logs")
        extracted_nutrition_entries: list[dict[str, Any]] = []
        extracted_workout_summaries: list[dict[str, Any]] = []
        extracted_volume_trends: list[dict[str, Any]] = []
        extracted_activity_entries: list[dict[str, Any]] = []
        extracted_activity_adjustment: dict[str, Any] | None = None
        extracted_workout_complete: Optional[bool] = None
        extracted_water_delta_liters: Optional[float] = None
        if isinstance(extracted_logs, dict):
            raw_nutrition = extracted_logs.get("nutrition_log")
            if isinstance(raw_nutrition, list):
                for entry in raw_nutrition:
                    if isinstance(entry, dict):
                        extracted_nutrition_entries.append(entry)
            elif isinstance(raw_nutrition, dict):
                extracted_nutrition_entries.append(raw_nutrition)

            raw_workout_summaries = extracted_logs.get("last_3_workout_summaries")
            if isinstance(raw_workout_summaries, list):
                for entry in raw_workout_summaries:
                    if isinstance(entry, dict):
                        extracted_workout_summaries.append(entry)
            elif isinstance(raw_workout_summaries, dict):
                extracted_workout_summaries.append(raw_workout_summaries)

            raw_volume_trends = extracted_logs.get("volume_trends")
            if isinstance(raw_volume_trends, list):
                for entry in raw_volume_trends:
                    if isinstance(entry, dict):
                        extracted_volume_trends.append(entry)
            elif isinstance(raw_volume_trends, dict):
                extracted_volume_trends.append(raw_volume_trends)

            raw_activity_log = extracted_logs.get("activity_log")
            if isinstance(raw_activity_log, list):
                for entry in raw_activity_log:
                    if isinstance(entry, dict):
                        extracted_activity_entries.append(entry)
            elif isinstance(raw_activity_log, dict):
                extracted_activity_entries.append(raw_activity_log)
            raw_activity_adjustment = extracted_logs.get("activity_adjustment")
            if isinstance(raw_activity_adjustment, dict):
                extracted_activity_adjustment = raw_activity_adjustment

            raw_current_day = extracted_logs.get("current_day")
            if isinstance(raw_current_day, dict):
                if "workout_complete" in raw_current_day:
                    extracted_workout_complete = bool(raw_current_day.get("workout_complete"))
                if "water_liters_delta" in raw_current_day:
                    try:
                        extracted_water_delta_liters = float(
                            raw_current_day.get("water_liters_delta") or 0
                        )
                    except (TypeError, ValueError):
                        extracted_water_delta_liters = None

            # Prevent array overwrite in deep merge; we'll append manually.
            extracted_logs = dict(extracted_logs)
            extracted_logs.pop("nutrition_log", None)
            extracted_logs.pop("last_3_workout_summaries", None)
            extracted_logs.pop("volume_trends", None)
            extracted_logs.pop("activity_log", None)
            extracted_logs.pop("activity_adjustment", None)
            if isinstance(extracted_logs.get("current_day"), dict):
                extracted_logs["current_day"] = dict(extracted_logs["current_day"])
                extracted_logs["current_day"].pop("workout_complete", None)
                extracted_logs["current_day"].pop("water_liters_delta", None)
                if not extracted_logs["current_day"]:
                    extracted_logs.pop("current_day", None)
            extracted_updates["logs"] = extracted_logs

        merged_profile = deep_merge_profile(base_profile, extracted_updates)
        nutrition_logged_this_turn = False
        workout_logged_this_turn = False
        workout_highlight = ""
        activity_burn_logged_this_turn = False
        activity_assumptions: list[str] = []
        activity_burn_added = 0
        has_image_media = bool(
            media_url and (media_content_type or "").lower().startswith("image/")
        )

        # If image is present, trust vision model for food logging and skip text/voice food placeholders.
        if has_image_media and extracted_nutrition_entries:
            print(
                "[Twilio Flow] Skipping text/voice nutrition extraction because image is present."
            )
            extracted_nutrition_entries = []

        if extracted_nutrition_entries:
            logs = merged_profile.get("logs") or {}
            nutrition_log = logs.get("nutrition_log") or []
            if not isinstance(nutrition_log, list):
                nutrition_log = []
            for entry in extracted_nutrition_entries:
                entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
                entry.setdefault("source", source_hint)
                if body:
                    entry.setdefault("user_message", body)
                nutrition_log.append(entry)
            logs["nutrition_log"] = nutrition_log
            merged_profile["logs"] = logs
            nutrition_logged_this_turn = True
            print(
                f"[Twilio Flow] Nutrition entries appended from {source_hint}: "
                f"{len(extracted_nutrition_entries)}"
            )

        if has_image_media:
            vision_updates = await ai_nutrition_from_image(
                media_url, merged_profile, trace_id=trace_id
            )
            food_log_entry = vision_updates.get("food_log_entry")
            if isinstance(food_log_entry, dict):
                logs = merged_profile.get("logs") or {}
                nutrition_log = logs.get("nutrition_log") or []
                if not isinstance(nutrition_log, list):
                    nutrition_log = []
                nutrition_log.append(food_log_entry)
                food_log_entry.setdefault(
                    "logged_at", datetime.now(timezone.utc).isoformat()
                )
                if body:
                    food_log_entry.setdefault("user_message", body)
                logs["nutrition_log"] = nutrition_log
                merged_profile["logs"] = logs
                nutrition_logged_this_turn = True
                print("[Twilio Flow] Nutrition entry appended from image.")

        if (
            extracted_workout_complete is True
            or extracted_workout_summaries
            or extracted_volume_trends
        ):
            logs = merged_profile.get("logs") or {}
            current_day = logs.get("current_day") or {}
            if not isinstance(current_day, dict):
                current_day = {}
            if extracted_workout_complete is True:
                current_day["workout_complete"] = True
                workout_logged_this_turn = True

            summaries = logs.get("last_3_workout_summaries") or []
            if not isinstance(summaries, list):
                summaries = []
            for summary in extracted_workout_summaries:
                summary = dict(summary)
                summary.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
                summary.setdefault("source", source_hint)
                summaries.append(summary)
                workout_logged_this_turn = True
            if len(summaries) > 3:
                summaries = summaries[-3:]

            volume_trends = logs.get("volume_trends") or []
            if not isinstance(volume_trends, list):
                volume_trends = []
            for trend in extracted_volume_trends:
                trend = dict(trend)
                trend.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
                trend.setdefault("source", source_hint)
                volume_trends.append(trend)
                workout_logged_this_turn = True

            logs["current_day"] = current_day
            logs["last_3_workout_summaries"] = summaries
            logs["volume_trends"] = volume_trends
            merged_profile["logs"] = logs

            # Coach-friendly short highlight from latest parsed workout.
            latest_summary = summaries[-1] if summaries else {}
            if isinstance(latest_summary, dict):
                summary_text = str(latest_summary.get("summary") or "").strip()
                top_weight = latest_summary.get("top_weight_kg")
                if summary_text and top_weight not in (None, "", 0, 0.0):
                    workout_highlight = f"{summary_text} at {top_weight}kg"
                elif summary_text:
                    workout_highlight = summary_text

        if extracted_activity_entries:
            logs = merged_profile.get("logs") or {}
            activity_log = logs.get("activity_log") or []
            if not isinstance(activity_log, list):
                activity_log = []
            current_day = logs.get("current_day") or {}
            if not isinstance(current_day, dict):
                current_day = {}
            physiology = merged_profile.get("physiology") or {}
            biometrics = physiology.get("biometrics") or {}
            weight_kg = float(biometrics.get("weight") or 0)

            for raw_activity in extracted_activity_entries:
                normalized = normalize_activity_for_burn(raw_activity)
                burn_cals = 0
                if weight_kg > 0:
                    burn_cals = calculate_activity_burn(
                        met_score=float(normalized.get("met_score_used") or 0),
                        duration_mins=float(normalized.get("effective_duration_mins") or 0),
                        weight_kg=weight_kg,
                    )
                entry = dict(normalized)
                entry["burn_cals"] = burn_cals
                entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
                entry.setdefault("source", source_hint)
                if body:
                    entry.setdefault("user_message", body)
                activity_log.append(entry)
                activity_burn_added += burn_cals
                assumption_note = str(entry.get("assumption_note") or "").strip()
                if assumption_note:
                    activity_assumptions.append(assumption_note)

            try:
                existing_active = int(float(current_day.get("active_cals_burnt") or 0))
            except (TypeError, ValueError):
                existing_active = 0
            current_day["active_cals_burnt"] = existing_active + activity_burn_added
            logs["activity_log"] = activity_log
            logs["current_day"] = current_day
            merged_profile["logs"] = logs
            activity_burn_logged_this_turn = activity_burn_added > 0
            log_agent_event(
                agent="bio_math",
                stage="activity_burn_updated",
                trace_id=trace_id,
                details={"entries": len(extracted_activity_entries), "added_cals": activity_burn_added},
            )

        if extracted_activity_adjustment:
            logs = merged_profile.get("logs") or {}
            activity_log = logs.get("activity_log") or []
            current_day = logs.get("current_day") or {}
            if isinstance(activity_log, list) and activity_log and isinstance(current_day, dict):
                mode = str(extracted_activity_adjustment.get("mode") or "").strip().lower()
                if mode == "recalculate_last":
                    last_idx = len(activity_log) - 1
                    last_entry = activity_log[last_idx]
                    if isinstance(last_entry, dict):
                        rest_style = extracted_activity_adjustment.get("rest_style")
                        if rest_style:
                            last_entry["rest_style"] = rest_style
                        normalized = normalize_activity_for_burn(last_entry)
                        for key, value in normalized.items():
                            last_entry[key] = value
                        physiology = merged_profile.get("physiology") or {}
                        biometrics = physiology.get("biometrics") or {}
                        weight_kg = float(biometrics.get("weight") or 0)
                        old_burn = int(float(last_entry.get("burn_cals") or 0))
                        new_burn = (
                            calculate_activity_burn(
                                met_score=float(normalized.get("met_score_used") or 0),
                                duration_mins=float(normalized.get("effective_duration_mins") or 0),
                                weight_kg=weight_kg,
                            )
                            if weight_kg > 0
                            else old_burn
                        )
                        delta = new_burn - old_burn
                        last_entry["burn_cals"] = new_burn
                        activity_log[last_idx] = last_entry
                        current_day["active_cals_burnt"] = int(
                            float(current_day.get("active_cals_burnt") or 0) + delta
                        )
                        logs["activity_log"] = activity_log
                        logs["current_day"] = current_day
                        merged_profile["logs"] = logs
                        assumption_note = str(last_entry.get("assumption_note") or "").strip()
                        if assumption_note:
                            activity_assumptions.append(assumption_note)
                        activity_burn_logged_this_turn = True
                        activity_burn_added += delta
                        log_agent_event(
                            agent="bio_math",
                            stage="activity_burn_recalculated",
                            trace_id=trace_id,
                            details={"delta_cals": delta, "new_burn": new_burn},
                        )

        if extracted_water_delta_liters and extracted_water_delta_liters > 0:
            logs = merged_profile.get("logs") or {}
            current_day = logs.get("current_day") or {}
            if not isinstance(current_day, dict):
                current_day = {}
            try:
                existing_water = float(current_day.get("water", 0) or 0)
            except (TypeError, ValueError):
                existing_water = 0.0
            current_day["water"] = round(existing_water + extracted_water_delta_liters, 3)
            logs["current_day"] = current_day
            merged_profile["logs"] = logs

        # Keep current_day calories in sync with today's nutrition log entries.
        logs = merged_profile.get("logs") or {}
        current_day = logs.get("current_day") or {}
        if not isinstance(current_day, dict):
            current_day = {}
        current_day["cals"] = _recalculate_current_day_calories(logs)
        logs["current_day"] = current_day
        merged_profile["logs"] = logs

        # Agent 4 trigger: deterministic daily targets once math inputs are ready.
        daily_targets, missing_reason = compute_daily_targets_if_ready(
            merged_profile, trace_id=trace_id
        )
        if daily_targets is not None:
            physiology = merged_profile.get("physiology") or {}
            biometrics = physiology.get("biometrics") or {}
            biometrics["daily_targets"] = daily_targets
            physiology["biometrics"] = biometrics
            merged_profile["physiology"] = physiology

            logs = merged_profile.get("logs") or {}
            current_day = logs.get("current_day") or {}
            if not isinstance(current_day, dict):
                current_day = {}
            current_day["calorie_budget"] = int(daily_targets.get("cals", 0) or 0)
            logs["current_day"] = current_day
            merged_profile["logs"] = logs
        else:
            log_agent_event(
                agent="bio_math",
                stage="targets_not_computed",
                status=missing_reason or "unknown",
                trace_id=trace_id,
            )

        # Keep net deficit synced: (TDEE + active burn) - food eaten.
        logs = merged_profile.get("logs") or {}
        current_day = logs.get("current_day") or {}
        if not isinstance(current_day, dict):
            current_day = {}
        physiology = merged_profile.get("physiology") or {}
        biometrics = physiology.get("biometrics") or {}
        daily_targets = biometrics.get("daily_targets") or {}
        tdee_cals = float(daily_targets.get("tdee_cals") or 0)
        active_cals_burnt = float(current_day.get("active_cals_burnt") or 0)
        food_cals = float(current_day.get("cals") or 0)
        current_day["net_deficit"] = calculate_net_deficit(
            tdee_cals=tdee_cals,
            active_cals_burnt=active_cals_burnt,
            food_cals=food_cals,
        )
        logs["current_day"] = current_day
        merged_profile["logs"] = logs

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
        log_agent_event(
            agent="memory_clerk",
            stage="profile_saved",
            trace_id=trace_id,
        )

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

        # Missing-data gatekeeper for deterministic macro math.
        # Memory Clerk runs before this, so if user just shared height/age, gate auto-unlocks.
        daily_targets_fresh, missing_reason_fresh = compute_daily_targets_if_ready(
            fresh_profile, trace_id=trace_id
        )
        if daily_targets_fresh is None and missing_reason_fresh in {
            "missing_height_or_age",
            "missing_gender",
        }:
            log_agent_event(
                agent="intake_agent",
                stage="math_gate_prompt",
                status=missing_reason_fresh,
                trace_id=trace_id,
            )
            reply_message = (
                "Bhai, I need to calculate your exact macros for the December goal. "
                "What is your height, age, and gender?"
            )
        else:
            coach_started_at = time.perf_counter()
            coach_reply = await generate_coach_reply(
                effective_user_text,
                fresh_profile,
                session_context={
                    "nutrition_logged_this_turn": nutrition_logged_this_turn,
                    "voice_note_logged_this_turn": voice_note_logged_this_turn,
                    "workout_logged_this_turn": workout_logged_this_turn,
                    "workout_highlight": workout_highlight,
                    "activity_burn_logged_this_turn": activity_burn_logged_this_turn,
                    "activity_burn_added": activity_burn_added,
                    "activity_assumptions": activity_assumptions[:3],
                    "routed_intent": routed_intent,
                    "router_confidence": routed_confidence,
                    "burn_facts": {
                        "active_cals_burnt": int(
                            float(
                                ((fresh_profile.get("logs") or {}).get("current_day") or {}).get(
                                    "active_cals_burnt", 0
                                )
                                or 0
                            )
                        ),
                        "intake_cals": int(
                            float(
                                ((fresh_profile.get("logs") or {}).get("current_day") or {}).get(
                                    "cals", 0
                                )
                                or 0
                            )
                        ),
                        "net_deficit": int(
                            float(
                                ((fresh_profile.get("logs") or {}).get("current_day") or {}).get(
                                    "net_deficit", 0
                                )
                                or 0
                            )
                        ),
                    },
                },
                trace_id=trace_id,
            )
            coach_elapsed_ms = int((time.perf_counter() - coach_started_at) * 1000)
            print(f"[Twilio Timing] Coach reply completed in {coach_elapsed_ms} ms")
            if coach_reply.strip():
                reply_message = coach_reply.strip()
        log_agent_event(
            agent="front_desk",
            stage="pipeline_complete",
            trace_id=trace_id,
            details={"reply_chars": len(reply_message)},
        )

    try:
        async with phone_lock:
            await _run_pipeline()
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio Error] Brain pipeline failed: {exc}")
        log_agent_event(
            agent="front_desk",
            stage="pipeline_error",
            status="failed",
            trace_id=trace_id,
            details={"error": str(exc)},
        )
        reply_message = (
            "Bhai, AI service abhi thoda busy hai. Tension mat le, 20-30 seconds me "
            "dobara ping kar."
        )
    finally:
        total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
        print(f"[Twilio Timing] Total async processing time: {total_elapsed_ms} ms")

    try:
        await send_whatsapp_message(phone_number, reply_message)
        log_agent_event(
            agent="front_desk",
            stage="message_sent",
            trace_id=trace_id,
            details={"reply_chars": len(reply_message)},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio Outbound Error] Failed to send final message: {exc}")
        log_agent_event(
            agent="front_desk",
            stage="message_send_error",
            status="failed",
            trace_id=trace_id,
            details={"error": str(exc)},
        )


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
    MediaContentType0: Optional[str] = Form(None),
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
    trace_id = (MessageSid or "").strip() or f"{phone_number}-{int(time.time() * 1000)}"
    log_agent_event(
        agent="front_desk",
        stage="incoming_event",
        trace_id=trace_id,
        details={"has_text": bool(incoming_text), "num_media": NumMedia or "0"},
    )
    print(f"[Twilio Incoming] {phone_number} says: {incoming_text}")
    media_count = 0
    try:
        media_count = int(NumMedia or "0")
    except (TypeError, ValueError):
        media_count = 0
    media_url = MediaUrl0 if media_count > 0 and MediaUrl0 else None
    media_content_type = MediaContentType0 if media_count > 0 and MediaContentType0 else None
    if media_url:
        print(
            f"[Twilio Incoming] Media detected. MediaUrl0 present for {phone_number}. "
            f"content_type={media_content_type or 'unknown'}"
        )

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
    event_key = (
        f"{phone_number}|{incoming_text}|{media_url or ''}|"
        f"{media_content_type or ''}|{MessageSid or ''}"
    )
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
        _process_and_send_twilio_reply,
        phone_number,
        incoming_text,
        media_url,
        media_content_type,
        trace_id,
    )
    print("[Twilio Ack] Responding immediately; processing reply in background.")
    return Response(
        content=_safe_twiml_message(
            "Bhai, got your message. Processing with AI now, sending full reply shortly."
        ),
        media_type="application/xml",
    )

