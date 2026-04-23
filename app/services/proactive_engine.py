from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.clients.supabase_client import supabase
from app.config import CHECKIN_INTERVAL_MINUTES, CHECKIN_MESSAGE
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
                await send_whatsapp_message(phone_number, CHECKIN_MESSAGE)
            except Exception as exc:  # noqa: BLE001
                print(f"[Scheduler] Failed check-in for {phone_number}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Scheduler] Proactive check-in job failed: {exc}")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_proactive_checkin_job,
        trigger="interval",
        minutes=CHECKIN_INTERVAL_MINUTES,
        id="apna_coach_proactive_checkin",
        replace_existing=True,
    )
    return scheduler

