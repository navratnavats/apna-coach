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
from app.services.morning_programmer import generate_morning_workout_nudge
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
            if not phone_number:
                continue
            try:
                trace_id = f"scheduler-checkin-{phone_number}"
                log_agent_event(
                    agent="front_desk",
                    stage="scheduler_checkin_send",
                    trace_id=trace_id,
                )
                await send_whatsapp_message(phone_number, CHECKIN_MESSAGE)
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

