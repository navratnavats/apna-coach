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

