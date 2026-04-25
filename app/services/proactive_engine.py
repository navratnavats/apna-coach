from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.clients.supabase_client import supabase
from app.config import (
    CHECKIN_INTERVAL_MINUTES,
    CHECKIN_MESSAGE,
    DIETITIAN_REVIEW_ENABLED,
    DIETITIAN_REVIEW_HOUR,
    DIETITIAN_REVIEW_MINUTE,
    MORNING_NUDGE_ENABLED,
    MORNING_NUDGE_HOUR,
    MORNING_NUDGE_MINUTE,
    SCHEDULER_TIMEZONE,
)
from app.services.agent_trace import log_agent_event
from app.services.dietitian_review import generate_dietitian_review
from app.services.eod_compression import compress_today_for_archive
from app.services.historical_archive import upsert_historical_day
from app.services.morning_programmer import generate_morning_workout_nudge
from app.services.persona import resolve_user_address
from app.services.response_humanizer import humanize_response
from app.services.twilio_messaging import send_whatsapp_message


async def run_proactive_checkin_job() -> None:
    """
    Find onboarded users and send proactive WhatsApp check-in.
    """
    try:
        response = (
            supabase.table("users")
            .select("phone_number,living_profile")
            .contains("living_profile", {"onboarding_complete": True})
            .execute()
        )
        rows = response.data or []
        print(f"[Scheduler] Found {len(rows)} onboarded users for check-in.")

        for row in rows:
            phone_number = row.get("phone_number")
            living_profile = row.get("living_profile") or {}
            if not phone_number:
                continue
            try:
                trace_id = f"scheduler-checkin-{phone_number}"
                log_agent_event(
                    agent="front_desk",
                    stage="scheduler_checkin_send",
                    trace_id=trace_id,
                )
                address = resolve_user_address(living_profile)
                if "{address}" in CHECKIN_MESSAGE:
                    static_text = CHECKIN_MESSAGE.format(address=address)
                elif CHECKIN_MESSAGE.lower().startswith("bhai,"):
                    static_text = f"{address},{CHECKIN_MESSAGE[5:]}"
                else:
                    static_text = f"{address}, {CHECKIN_MESSAGE}"
                
                # Humanize proactive check-in message
                # Include conversation history for context-aware proactive nudges
                conversation_history_str = ""
                session_context = living_profile.get("session_context") or {}
                if isinstance(session_context, dict):
                    history = session_context.get("conversation_history") or []
                    if isinstance(history, list) and history:
                        history_lines = []
                        for turn_pair in history[-2:]:  # Last 2 turns for proactive context
                            if isinstance(turn_pair, dict):
                                user_turn = turn_pair.get("user") or {}
                                assistant_turn = turn_pair.get("assistant") or {}
                                user_msg = str(user_turn.get("message") or "").strip()
                                assistant_msg = str(assistant_turn.get("message") or "").strip()
                                if user_msg and assistant_msg:
                                    history_lines.append(f"User: {user_msg[:100]}")
                                    history_lines.append(f"Coach: {assistant_msg[:100]}")
                        if history_lines:
                            conversation_history_str = "Recent context:\n" + "\n".join(history_lines)
                
                message = await humanize_response(
                    intent_text=static_text,
                    living_profile=living_profile,
                    last_user_message="",  # No user context for proactive messages
                    trace_id=trace_id,
                    conversation_history=conversation_history_str,
                )
                await send_whatsapp_message(phone_number, message)
            except Exception as exc:  # noqa: BLE001
                print(f"[Scheduler] Failed check-in for {phone_number}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Scheduler] Proactive check-in job failed: {exc}")


async def run_dietitian_review_job() -> None:
    """
    Nightly dietitian review for onboarded users based on today's nutrition log.
    """
    try:
        response = (
            supabase.table("users")
            .select("phone_number,living_profile")
            .contains("living_profile", {"onboarding_complete": True})
            .execute()
        )
        rows = response.data or []
        print(f"[Scheduler] Found {len(rows)} onboarded users for dietitian review.")

        for row in rows:
            phone_number = row.get("phone_number")
            living_profile = row.get("living_profile") or {}
            if not phone_number:
                continue
            try:
                trace_id = f"scheduler-dietitian-{phone_number}"
                review_text = await generate_dietitian_review(
                    living_profile, SCHEDULER_TIMEZONE, trace_id=trace_id
                )
                await send_whatsapp_message(phone_number, review_text)
                log_agent_event(
                    agent="front_desk",
                    stage="scheduler_dietitian_sent",
                    trace_id=trace_id,
                    details={"chars": len(review_text)},
                )

                compressed_profile, archive_payload = compress_today_for_archive(
                    living_profile,
                    SCHEDULER_TIMEZONE,
                    trace_id=trace_id,
                )
                if archive_payload is not None:
                    upsert_historical_day(
                        phone_number=phone_number,
                        archive_date=str(archive_payload.get("date") or ""),
                        summary_line=str(archive_payload.get("summary_line") or ""),
                        metrics=archive_payload.get("metrics") or {},
                        nutrition_entries=archive_payload.get("nutrition_entries") or [],
                        activity_entries=archive_payload.get("activity_entries") or [],
                    )
                    (
                        supabase.table("users")
                        .update({"living_profile": compressed_profile})
                        .eq("phone_number", phone_number)
                        .execute()
                    )
                    log_agent_event(
                        agent="front_desk",
                        stage="scheduler_eod_saved",
                        trace_id=trace_id,
                        details={
                            "date": archive_payload.get("date"),
                            "archive_upserted": True,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[Scheduler] Failed dietitian review for {phone_number}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Scheduler] Dietitian review job failed: {exc}")


async def run_morning_workout_nudge_job() -> None:
    """
    Morning proactive workout nudge for onboarded users.
    """
    try:
        response = (
            supabase.table("users")
            .select("phone_number,living_profile")
            .contains("living_profile", {"onboarding_complete": True})
            .execute()
        )
        rows = response.data or []
        print(f"[Scheduler] Found {len(rows)} onboarded users for morning nudge.")

        for row in rows:
            phone_number = row.get("phone_number")
            living_profile = row.get("living_profile") or {}
            if not phone_number:
                continue
            try:
                trace_id = f"scheduler-morning-{phone_number}"
                message = await generate_morning_workout_nudge(
                    living_profile, trace_id=trace_id
                )
                await send_whatsapp_message(phone_number, message)
                log_agent_event(
                    agent="front_desk",
                    stage="scheduler_morning_sent",
                    trace_id=trace_id,
                    details={"chars": len(message)},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[Scheduler] Failed morning nudge for {phone_number}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Scheduler] Morning workout nudge job failed: {exc}")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    scheduler.add_job(
        run_proactive_checkin_job,
        trigger="interval",
        minutes=CHECKIN_INTERVAL_MINUTES,
        id="apna_coach_proactive_checkin",
        replace_existing=True,
    )
    if DIETITIAN_REVIEW_ENABLED:
        scheduler.add_job(
            run_dietitian_review_job,
            trigger="cron",
            hour=DIETITIAN_REVIEW_HOUR,
            minute=DIETITIAN_REVIEW_MINUTE,
            id="apna_coach_dietitian_review",
            replace_existing=True,
        )
    if MORNING_NUDGE_ENABLED:
        scheduler.add_job(
            run_morning_workout_nudge_job,
            trigger="cron",
            hour=MORNING_NUDGE_HOUR,
            minute=MORNING_NUDGE_MINUTE,
            id="apna_coach_morning_workout_nudge",
            replace_existing=True,
        )
    return scheduler

