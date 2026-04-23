from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.clients.supabase_client import supabase
from app.config import (
    SCHEDULER_TIMEZONE,
    TRIAL_DAILY_TURN_LIMIT,
    TRIAL_DAILY_TURN_WARNING_THRESHOLD,
    TWILIO_WHATSAPP_FROM,
    WHATSAPP_VERIFY_TOKEN,
)
from app.services.agent_trace import log_agent_event
from app.services.bio_math_agent import (
    calculate_activity_burn,
    compute_current_day_metrics,
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
)
from app.services.messages import (
    ack_audio,
    ack_default,
    ack_image,
    ack_long_text,
    image_rejection,
    onboarding_missing_biometrics,
    pipeline_busy_retry,
    policy_out_of_scope,
    trial_limit_wall,
    trial_limit_warning,
)
from app.services.intake_agent import (
    build_graduation_message,
    build_intake_prompt,
    get_missing_onboarding_fields,
)
from app.services.intent_router import classify_router_intent
from app.services.critic_agent import run_critic_agent
from app.services.plan_agent import generate_structured_plan
from app.services.plan_orchestrator import (
    PLAN_INTENTS,
    apply_plan_confirmation_if_any,
    build_plan_compact_for_prompt,
    classify_plan_intent_fallback,
    ensure_plan_state,
    fetch_latest_plan_version,
    infer_plan_type_and_horizon,
    persist_plan_version,
    upsert_pending_change_request,
)
from app.services.policy_agent import classify_query_policy
from app.services.psychology_agent import (
    analyze_psychology_signals,
    apply_psychology_update,
)
from app.services.historical_archive import fetch_historical_day, resolve_target_date
from app.services.profile_schema_guard import sanitize_memory_updates
from app.services.twilio_messaging import send_whatsapp_message
from app.services.usage_quota import consume_daily_turn_quota

router = APIRouter()
RECENT_EVENT_TTL_SECONDS = 45
_recent_twilio_events: dict[str, float] = {}
_phone_locks: dict[str, asyncio.Lock] = {}
OUTBOUND_STATUS_VALUES = {"sent", "delivered", "read", "failed", "undelivered"}
BATCH_WINDOW_SECONDS = 10.0
_pending_batches: dict[str, dict[str, Any]] = {}
_batch_flush_tasks: dict[str, asyncio.Task] = {}
_phone_queues: dict[str, deque[dict[str, Any]]] = {}
_phone_worker_tasks: dict[str, asyncio.Task] = {}


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


def _try_record_message_sid(message_sid: str, phone_number: str) -> bool:
    sid = (message_sid or "").strip()
    if not sid:
        return True
    try:
        (
            supabase.table("processed_webhook_events")
            .insert(
                {
                    "message_sid": sid,
                    "phone_number": phone_number,
                    "payload_type": "twilio_inbound",
                }
            )
            .execute()
        )
        return True
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "duplicate key" in text or "unique constraint" in text:
            return False
        raise


def _contextual_ack_message(
    *, has_audio: bool, has_image: bool, user_text: str
) -> str:
    if has_audio:
        return ack_audio()
    if has_image:
        return ack_image()
    if len((user_text or "").strip()) > 120:
        return ack_long_text()
    return ack_default()


async def _flush_batch_after_window(phone_number: str) -> None:
    await asyncio.sleep(BATCH_WINDOW_SECONDS)
    batch = _pending_batches.pop(phone_number, None)
    _batch_flush_tasks.pop(phone_number, None)
    if not batch:
        return
    queue = _phone_queues.setdefault(phone_number, deque())
    queue.append(batch)
    if phone_number not in _phone_worker_tasks or _phone_worker_tasks[phone_number].done():
        _phone_worker_tasks[phone_number] = asyncio.create_task(_run_phone_queue_worker(phone_number))


def _enqueue_into_batch(
    *,
    phone_number: str,
    incoming_text: str,
    media_items: list[dict[str, str]],
    trace_id: str,
) -> None:
    batch = _pending_batches.get(phone_number)
    if not batch:
        batch = {
            "phone_number": phone_number,
            "texts": [],
            "media_items": [],
            "trace_ids": [],
            "created_at": time.time(),
        }
        _pending_batches[phone_number] = batch

    if incoming_text.strip():
        batch["texts"].append(incoming_text.strip())
    if media_items:
        batch["media_items"].extend(media_items)
    batch["trace_ids"].append(trace_id)
    batch["last_event_at"] = time.time()

    existing_task = _batch_flush_tasks.get(phone_number)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    _batch_flush_tasks[phone_number] = asyncio.create_task(_flush_batch_after_window(phone_number))


async def _run_phone_queue_worker(phone_number: str) -> None:
    queue = _phone_queues.setdefault(phone_number, deque())
    while queue:
        batch = queue.popleft()
        texts = [t for t in (batch.get("texts") or []) if isinstance(t, str) and t.strip()]
        combined_text = "\n".join(texts).strip()
        media_items = batch.get("media_items") or []
        trace_ids = batch.get("trace_ids") or []
        trace_id = trace_ids[0] if trace_ids else f"{phone_number}-{int(time.time() * 1000)}"
        await _process_and_send_twilio_reply(
            phone_number,
            combined_text,
            media_items=media_items,
            trace_id=trace_id,
        )


async def _process_and_send_twilio_reply(
    phone_number: str,
    body: str,
    media_items: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> None:
    request_started_at = time.perf_counter()
    reply_message = "Give me a second, optimizing your plan..."
    quota_warning_suffix = ""
    phone_lock = _phone_locks.setdefault(phone_number, asyncio.Lock())
    media_items = media_items or []

    async def _run_pipeline() -> None:
        nonlocal reply_message, quota_warning_suffix
        log_agent_event(
            agent="front_desk",
            stage="pipeline_start",
            trace_id=trace_id,
            details={
                "phone_number": phone_number,
                "has_media": bool(media_items),
                "media_count": len(media_items),
            },
        )
        quota_result = consume_daily_turn_quota(
            phone_number=phone_number,
            timezone_name=SCHEDULER_TIMEZONE,
            daily_limit=TRIAL_DAILY_TURN_LIMIT,
            warning_threshold=TRIAL_DAILY_TURN_WARNING_THRESHOLD,
        )
        log_agent_event(
            agent="quota_gate",
            stage="consumed_turn",
            trace_id=trace_id,
            details={
                "used_turns": int(quota_result.get("used_turns") or 0),
                "limit": int(quota_result.get("daily_limit") or TRIAL_DAILY_TURN_LIMIT),
                "blocked": bool(quota_result.get("blocked")),
            },
        )
        if bool(quota_result.get("blocked")):
            reply_message = trial_limit_wall(daily_limit=TRIAL_DAILY_TURN_LIMIT)
            return
        if bool(quota_result.get("warn")):
            quota_warning_suffix = trial_limit_warning(
                used_turns=int(quota_result.get("used_turns") or 0),
                daily_limit=int(quota_result.get("daily_limit") or TRIAL_DAILY_TURN_LIMIT),
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
        onboarding_was_complete = False
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
        onboarding_was_complete = bool(base_profile.get("onboarding_complete") is True)

        effective_user_text = body
        voice_note_logged_this_turn = False
        source_hint = "text"

        audio_media = [
            item
            for item in media_items
            if str((item or {}).get("content_type") or "").lower().startswith("audio/")
        ]
        image_media = [
            item
            for item in media_items
            if str((item or {}).get("content_type") or "").lower().startswith("image/")
        ]
        transcripts: list[str] = []
        for item in audio_media:
            media_url = str(item.get("url") or "").strip()
            media_content_type = str(item.get("content_type") or "audio/ogg").strip()
            if not media_url:
                continue
            audio_result = await ai_transcribe_voice_note(
                media_url, media_content_type, trace_id=trace_id
            )
            transcript = str(audio_result.get("transcript") or "").strip()
            if transcript:
                transcripts.append(transcript)
        if transcripts:
            voice_note_logged_this_turn = True
            source_hint = "voice"
            merged_transcript = "\n".join(transcripts).strip()
            effective_user_text = (
                merged_transcript
                if not effective_user_text
                else f"{effective_user_text}\n\nVoice note transcript: {merged_transcript}"
            )
            print("[Twilio Flow] Voice note transcribed and added to user context.")

        policy_result = await classify_query_policy(
            user_message=effective_user_text,
            has_media=bool(media_items),
        )
        policy_decision = str(policy_result.get("decision") or "allow")
        policy_reason = str(policy_result.get("reason") or "normal")
        policy_forced_mode = str(policy_result.get("forced_mode") or "push")
        log_agent_event(
            agent="policy_gate",
            stage="applied",
            trace_id=trace_id,
            details={
                "decision": policy_decision,
                "reason": policy_reason,
                "confidence": str(policy_result.get("confidence") or "low"),
            },
        )
        if policy_decision == "deny":
            reply_message = str(policy_result.get("safe_response_hint") or "").strip() or (
                policy_out_of_scope()
            )
            return

        router_result = await classify_router_intent(
            effective_user_text,
            trace_id=trace_id,
        )
        routed_intent = str(router_result.get("primary_intent") or "general_chat")
        routed_confidence = str(router_result.get("confidence") or "low")
        plan_fallback_intent = classify_plan_intent_fallback(effective_user_text)
        if plan_fallback_intent:
            routed_intent = plan_fallback_intent
            routed_confidence = "fallback"
        log_agent_event(
            agent="router",
            stage="applied",
            trace_id=trace_id,
            details={"intent": routed_intent, "confidence": routed_confidence},
        )

        merged_profile = ensure_plan_state(base_profile)
        merged_profile, plan_confirmation = apply_plan_confirmation_if_any(
            merged_profile,
            effective_user_text,
        )
        if plan_confirmation is not None:
            if plan_confirmation.get("decision") == "approved":
                routed_intent = "plan_edit_request"
            else:
                routed_intent = "general_chat"

        historical_result: dict[str, Any] | None = None
        if routed_intent == "historical_query":
            target_date = resolve_target_date(effective_user_text, "Asia/Kolkata")
            if target_date:
                historical_result = fetch_historical_day(phone_number, target_date)
                log_agent_event(
                    agent="router",
                    stage="historical_fetch",
                    trace_id=trace_id,
                    details={
                        "target_date": target_date,
                        "found": bool(historical_result),
                    },
                )
            else:
                log_agent_event(
                    agent="router",
                    stage="historical_fetch",
                    status="missing_date",
                    trace_id=trace_id,
                )

        memory_started_at = time.perf_counter()
        extracted_updates: dict[str, Any] = {}
        if routed_intent not in {"burn_query", *PLAN_INTENTS}:
            extracted_updates = await ai_memory_clerk(
                effective_user_text,
                merged_profile,
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

        merged_profile = deep_merge_profile(merged_profile, extracted_updates)
        nutrition_logged_this_turn = False
        workout_logged_this_turn = False
        workout_highlight = ""
        activity_burn_logged_this_turn = False
        activity_assumptions: list[str] = []
        activity_burn_added = 0
        has_image_media = bool(image_media)

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
            rejected_non_fitness_images = 0
            for item in image_media:
                image_url = str(item.get("url") or "").strip()
                if not image_url:
                    continue
                vision_updates = await ai_nutrition_from_image(
                    image_url, merged_profile, trace_id=trace_id
                )
                vision_status = str(vision_updates.get("status") or "ok").strip().lower()
                vision_category = str(vision_updates.get("category") or "").strip().lower()
                if vision_status == "reject":
                    rejected_non_fitness_images += 1
                    log_agent_event(
                        agent="nutritionist",
                        stage="vision_rejected",
                        trace_id=trace_id,
                        details={"category": vision_category or "other"},
                    )
                    continue
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
            if rejected_non_fitness_images > 0:
                has_non_media_text = bool((body or "").strip()) or bool(transcripts)
                if (not has_non_media_text) and (rejected_non_fitness_images == len(image_media)):
                    reply_message = (
                        image_rejection()
                    )
                    return
            if nutrition_logged_this_turn:
                print("[Twilio Flow] Nutrition entries appended from image(s).")

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

        else:
            log_agent_event(
                agent="bio_math",
                stage="targets_not_computed",
                status=missing_reason or "unknown",
                trace_id=trace_id,
            )

        # Keep daily dashboard metrics synced from one deterministic source.
        logs = merged_profile.get("logs") or {}
        current_day = logs.get("current_day") or {}
        if not isinstance(current_day, dict):
            current_day = {}
        metrics = compute_current_day_metrics(merged_profile)
        current_day["cals"] = int(metrics.get("intake_cals") or 0)
        current_day["active_cals_burnt"] = int(metrics.get("active_cals_burnt") or 0)
        current_day["net_deficit"] = int(metrics.get("net_deficit_cals") or 0)
        current_day["calorie_budget"] = int(metrics.get("calorie_budget_cals") or 0)
        current_day["metrics"] = metrics
        logs["current_day"] = current_day
        merged_profile["logs"] = logs

        # Psychology Agent: nuanced signal extraction + deterministic bounded update.
        psych_analysis = await analyze_psychology_signals(
            user_message=effective_user_text,
            living_profile=merged_profile,
        )
        merged_profile = apply_psychology_update(merged_profile, psych_analysis)

        missing_after_merge = get_missing_onboarding_fields(merged_profile)
        if not missing_after_merge:
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
        if isinstance(fresh_profile, dict):
            # Keep transient response fields out of living_profile state.
            fresh_profile.pop("coach_response", None)

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

        # Intake Agent graduation gate + deterministic macro gate.
        daily_targets_fresh, missing_reason_fresh = compute_daily_targets_if_ready(
            fresh_profile, trace_id=trace_id
        )
        missing_onboarding_now = get_missing_onboarding_fields(fresh_profile)
        onboarding_is_complete = len(missing_onboarding_now) == 0

        if not onboarding_is_complete:
            log_agent_event(
                agent="intake_agent",
                stage="prompt_missing_fields",
                trace_id=trace_id,
                details={"missing_fields": missing_onboarding_now},
            )
            reply_message = build_intake_prompt(fresh_profile, missing_onboarding_now)
        elif (
            not onboarding_was_complete
            and onboarding_is_complete
            and daily_targets_fresh is not None
        ):
            fresh_profile["onboarding_complete"] = True
            (
                supabase.table("users")
                .update({"living_profile": fresh_profile})
                .eq("phone_number", phone_number)
                .execute()
            )
            reply_message = build_graduation_message(fresh_profile)
            log_agent_event(
                agent="intake_agent",
                stage="graduated",
                trace_id=trace_id,
                details={
                    "daily_target_cals": (daily_targets_fresh or {}).get("cals", 0),
                    "daily_target_protein": (daily_targets_fresh or {}).get("protein_g", 0),
                },
            )
        elif daily_targets_fresh is None and missing_reason_fresh in {
            "missing_height_or_age",
            "missing_gender",
        }:
            reply_message = (
                onboarding_missing_biometrics()
            )
        elif routed_intent == "plan_change_signal":
            fresh_profile = upsert_pending_change_request(
                fresh_profile, effective_user_text
            )
            (
                supabase.table("users")
                .update({"living_profile": fresh_profile})
                .eq("phone_number", phone_number)
                .execute()
            )
            reply_message = (
                "Noted. Plan adjust kar du based on this update? Reply with Yes/No."
            )
        else:
            latest_plan = None
            if routed_intent in PLAN_INTENTS:
                latest_plan = fetch_latest_plan_version(phone_number)
                if latest_plan:
                    plan_payload = latest_plan.get("plan_payload") or {}
                    plans = (fresh_profile.get("plans") or {})
                    active = (plans.get("active") or {})
                    active["plan_id"] = latest_plan.get("plan_id") or active.get("plan_id")
                    active["version"] = int(float(latest_plan.get("version") or 0))
                    active["status"] = latest_plan.get("status") or "active"
                    if isinstance(plan_payload, dict):
                        active["type"] = str(
                            plan_payload.get("type")
                            or ((plan_payload.get("meta") or {}).get("type") or "hybrid")
                        )
                        active["horizon"] = str(
                            plan_payload.get("horizon")
                            or ((plan_payload.get("meta") or {}).get("horizon") or "weekly")
                        )
                        active["current_block"] = plan_payload.get("current_block") or {}
                        active["week_blocks"] = plan_payload.get("week_blocks") or []
                        active["day_actions"] = plan_payload.get("day_actions") or []
                        active["constraints"] = plan_payload.get("constraints") or {}
                    plans["active"] = active
                    fresh_profile["plans"] = plans
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
                    "burn_facts": dict(
                        (
                            ((fresh_profile.get("logs") or {}).get("current_day") or {}).get(
                                "metrics"
                            )
                            or {}
                        )
                    ),
                    "historical_query_result": historical_result,
                    "plan_context": build_plan_compact_for_prompt(fresh_profile),
                    "response_mode": (
                        policy_forced_mode
                        if policy_decision == "allow_constrained"
                        else (
                            (
                                (psych_analysis or {}).get("response_mode")
                                if isinstance(psych_analysis, dict)
                                else "push"
                            )
                            or "push"
                        )
                    ),
                    "policy_decision": policy_decision,
                    "policy_reason": policy_reason,
                },
                trace_id=trace_id,
            )
            coach_elapsed_ms = int((time.perf_counter() - coach_started_at) * 1000)
            print(f"[Twilio Timing] Coach reply completed in {coach_elapsed_ms} ms")
            if coach_reply.strip():
                reply_message = coach_reply.strip()
                if routed_intent in {"plan_create_request", "plan_edit_request"}:
                    reason = (
                        "create"
                        if routed_intent == "plan_create_request"
                        else "edit"
                    )
                    plan_context = build_plan_compact_for_prompt(fresh_profile)
                    structured = await generate_structured_plan(
                        user_message=effective_user_text,
                        living_profile=fresh_profile,
                        plan_context=plan_context,
                    )
                    inferred_type, inferred_horizon = infer_plan_type_and_horizon(
                        effective_user_text
                    )
                    plan_type = str(structured.get("type") or inferred_type)
                    horizon = str(structured.get("horizon") or inferred_horizon)
                    week_blocks = structured.get("week_blocks") or []
                    day_actions = structured.get("day_actions") or []
                    response_text = str(structured.get("response_text") or "").strip()
                    if response_text:
                        coached = await run_critic_agent(
                            response_text,
                            source="plan_agent",
                            living_profile=fresh_profile,
                            trace_id=trace_id,
                        )
                        if coached.strip():
                            reply_message = coached.strip()
                    fresh_profile = persist_plan_version(
                        phone_number=phone_number,
                        living_profile=fresh_profile,
                        plan_text=reply_message,
                        change_reason=reason,
                        horizon_weeks=12,
                        plan_type=plan_type,
                        horizon=horizon,
                        week_blocks=week_blocks,
                        day_actions=day_actions,
                    )
                    (
                        supabase.table("users")
                        .update({"living_profile": fresh_profile})
                        .eq("phone_number", phone_number)
                        .execute()
                    )
        log_agent_event(
            agent="front_desk",
            stage="pipeline_complete",
            trace_id=trace_id,
            details={"reply_chars": len(reply_message)},
        )

    done_event = asyncio.Event()

    async def _send_delay_pings() -> None:
        checkpoints = [
            (30, "Aapka context review kar raha hoon. Kripya thoda sa aur time dijiye."),
            (60, "Heavy analysis chal raha hai. Almost there."),
            (90, "Bas final checks bache hain. Answer bhej raha hoon."),
            (
                150,
                "Still processing. Final response ready hote hi seedha bhej dunga.",
            ),
        ]
        started = time.monotonic()
        for seconds, message in checkpoints:
            remaining = seconds - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            if done_event.is_set():
                return
            try:
                await send_whatsapp_message(phone_number, message)
            except Exception as exc:  # noqa: BLE001
                print(f"[Twilio Delay Ping Error] {exc}")

    ping_task = asyncio.create_task(_send_delay_pings())

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
            pipeline_busy_retry()
        )
    finally:
        done_event.set()
        if not ping_task.done():
            ping_task.cancel()
        total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
        print(f"[Twilio Timing] Total async processing time: {total_elapsed_ms} ms")

    if quota_warning_suffix and "trial limit" not in reply_message.lower():
        reply_message = f"{reply_message}{quota_warning_suffix}"

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
    request: Request,
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
    form_data = await request.form()
    media_items: list[dict[str, str]] = []
    for i in range(max(media_count, 0)):
        url = str(form_data.get(f"MediaUrl{i}") or "").strip()
        ctype = str(form_data.get(f"MediaContentType{i}") or "").strip()
        if url:
            media_items.append({"url": url, "content_type": ctype})
    if media_items:
        print(f"[Twilio Incoming] Media detected for {phone_number}. count={len(media_items)}")

    # Guard 1: status callbacks are not user chat messages.
    # IMPORTANT: inbound user events may carry SmsStatus=received, so do not drop
    # them when actual message content (text/media) is present.
    callback_status = (MessageStatus or SmsStatus or "").strip().lower()
    has_user_content = bool(incoming_text) or len(media_items) > 0
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
    if not incoming_text and len(media_items) == 0:
        print(f"[Twilio Event] type=empty_filtered from={phone_number}")
        return Response(
            content=_safe_twiml_message("No message content received."),
            media_type="application/xml",
        )

    # Guard 3: ignore sandbox-origin system echoes/events.
    sandbox_source = TWILIO_WHATSAPP_FROM.removeprefix("whatsapp:").strip()
    if sandbox_source and phone_number == sandbox_source and not incoming_text and len(media_items) == 0:
        print(f"[Twilio Event] type=sandbox_filtered from={phone_number}")
        return Response(
            content=_safe_twiml_message("Sandbox event ignored."),
            media_type="application/xml",
        )

    if not _try_record_message_sid(MessageSid or "", phone_number):
        print(f"[Twilio Event] type=duplicate_message_sid from={phone_number}")
        return Response(
            content=_safe_twiml_message("Duplicate event skipped."),
            media_type="application/xml",
        )

    # Guard 4: dedupe near-identical repeated events/retries.
    now_ts = time.time()
    _cleanup_recent_events(now_ts)
    event_media_sig = ",".join(
        [f"{(m.get('url') or '')}|{(m.get('content_type') or '')}" for m in media_items]
    )
    event_key = (
        f"{phone_number}|{incoming_text}|{event_media_sig}|{MessageSid or ''}"
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
        f"has_text={bool(incoming_text)} media_count={len(media_items)}"
    )

    _enqueue_into_batch(
        phone_number=phone_number,
        incoming_text=incoming_text,
        media_items=media_items,
        trace_id=trace_id,
    )
    print(
        f"[Twilio Batch] Event added for {phone_number}. "
        f"buffer_window_seconds={BATCH_WINDOW_SECONDS}"
    )

    has_audio = any(
        str((m or {}).get("content_type") or "").lower().startswith("audio/")
        for m in media_items
    )
    has_image = any(
        str((m or {}).get("content_type") or "").lower().startswith("image/")
        for m in media_items
    )
    ack_text = _contextual_ack_message(
        has_audio=has_audio,
        has_image=has_image,
        user_text=incoming_text,
    )
    return Response(
        content=_safe_twiml_message(ack_text),
        media_type="application/xml",
    )

