from __future__ import annotations

import asyncio
import base64
import urllib.parse
import urllib.request

from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM


def _format_whatsapp_address(phone_number: str) -> str:
    stripped = phone_number.strip()
    if stripped.startswith("whatsapp:"):
        return stripped
    return f"whatsapp:{stripped}"


def _send_whatsapp_message_sync(to_phone_number: str, body: str) -> None:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        raise RuntimeError(
            "Missing Twilio outbound config. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_FROM."
        )

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    )
    payload = urllib.parse.urlencode(
        {
            "From": _format_whatsapp_address(TWILIO_WHATSAPP_FROM),
            "To": _format_whatsapp_address(to_phone_number),
            "Body": body,
        }
    ).encode("utf-8")

    request = urllib.request.Request(url=url, data=payload, method="POST")
    credentials = f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")
    auth_header = base64.b64encode(credentials).decode("ascii")
    request.add_header("Authorization", f"Basic {auth_header}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(request, timeout=20) as response:
        response_body = response.read().decode("utf-8", errors="ignore")
        print(
            f"[Twilio Outbound] Sent message status={response.status} "
            f"response_chars={len(response_body)}"
        )


async def send_whatsapp_message(to_phone_number: str, body: str) -> None:
    await asyncio.to_thread(_send_whatsapp_message_sync, to_phone_number, body)

