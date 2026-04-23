from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=True)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SUPABASE_URL: str = required_env("SUPABASE_URL")
WHATSAPP_VERIFY_TOKEN: str = required_env("WHATSAPP_VERIFY_TOKEN")

# Optional to keep local dev resilient.
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL_3_1_FLASH: str = os.getenv(
    "GEMINI_MODEL_3_1_FLASH", "models/gemini-2.0-flash-lite"
)
GEMINI_COACH_MODEL: str = os.getenv("GEMINI_COACH_MODEL", GEMINI_MODEL_3_1_FLASH)

SUPABASE_SERVER_KEY: str = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or required_env("SUPABASE_ANON_KEY")
)

# Outbound Twilio WhatsApp send (used after async processing completes).
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "")

# Proactive engine (scheduler) settings.
SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
CHECKIN_INTERVAL_MINUTES: int = int(os.getenv("CHECKIN_INTERVAL_MINUTES", "360"))
CHECKIN_MESSAGE: str = os.getenv(
    "CHECKIN_MESSAGE",
    "{address}, this is an automated check-in. Have you worked out today?",
)

# Nightly Dietitian review settings.
DIETITIAN_REVIEW_ENABLED: bool = (
    os.getenv("DIETITIAN_REVIEW_ENABLED", "true").lower() == "true"
)
DIETITIAN_REVIEW_HOUR: int = int(os.getenv("DIETITIAN_REVIEW_HOUR", "21"))
DIETITIAN_REVIEW_MINUTE: int = int(os.getenv("DIETITIAN_REVIEW_MINUTE", "30"))
SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "Asia/Kolkata")

# Morning Workout Nudge settings.
MORNING_NUDGE_ENABLED: bool = os.getenv("MORNING_NUDGE_ENABLED", "true").lower() == "true"
MORNING_NUDGE_HOUR: int = int(os.getenv("MORNING_NUDGE_HOUR", "8"))
MORNING_NUDGE_MINUTE: int = int(os.getenv("MORNING_NUDGE_MINUTE", "0"))

# Trial usage quota settings (counted per stitched AI turn).
TRIAL_DAILY_TURN_LIMIT: int = int(os.getenv("TRIAL_DAILY_TURN_LIMIT", "25"))
TRIAL_DAILY_TURN_WARNING_THRESHOLD: int = int(
    os.getenv("TRIAL_DAILY_TURN_WARNING_THRESHOLD", "20")
)

