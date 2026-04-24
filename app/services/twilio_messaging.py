from __future__ import annotations

import asyncio
import base64
import urllib.error
import urllib.parse
import urllib.request

from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM

TWILIO_SAFE_SEGMENT_CHARS = 1400
TWILIO_RETRY_SEGMENT_CHARS = 900


class TwilioOutboundError(RuntimeError):
    def __init__(self, status_code: int, reason: str, error_body: str) -> None:
        super().__init__(f"Twilio outbound failed: {status_code} {reason}")
        self.status_code = int(status_code)
        self.reason = reason
        self.error_body = error_body


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

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8", errors="ignore")
            print(
                f"[Twilio Outbound] Sent message status={response.status} "
                f"response_chars={len(response_body)}"
            )
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        print(
            f"[Twilio Outbound Error] status={exc.code} reason={exc.reason} "
            f"body={error_body}"
        )
        raise TwilioOutboundError(exc.code, str(exc.reason), error_body) from exc


def _segment_message(body: str, *, max_chars: int) -> list[str]:
    text = str(body or "").strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    current = ""
    for para in paragraphs:
        para_with_gap = para if not current else f"\n\n{para}"
        if len(current) + len(para_with_gap) <= max_chars:
            current += para_with_gap
            continue
        if current.strip():
            segments.append(current.strip())
            current = ""
        if len(para) <= max_chars:
            current = para
            continue

        # Hard-wrap oversized paragraph by words.
        words = para.split()
        block = ""
        for word in words:
            probe = f"{block} {word}".strip()
            if len(probe) <= max_chars:
                block = probe
            else:
                if block:
                    segments.append(block)
                block = word[:max_chars]
        if block:
            current = block

    if current.strip():
        segments.append(current.strip())
    return segments or [text[:max_chars]]


async def _send_segmented(
    to_phone_number: str,
    body: str,
    *,
    max_chars: int,
) -> None:
    segments = _segment_message(body, max_chars=max_chars)
    total = len(segments)
    print(
        f"[Twilio Outbound] segmented_send total_segments={total} "
        f"total_chars={len(body or '')} max_chars={max_chars}"
    )
    for index, segment in enumerate(segments, start=1):
        prefix = f"({index}/{total}) " if total > 1 else ""
        await asyncio.to_thread(
            _send_whatsapp_message_sync,
            to_phone_number,
            f"{prefix}{segment}".strip(),
        )
        if total > 1 and index < total:
            await asyncio.sleep(0.15)


async def send_whatsapp_message(to_phone_number: str, body: str) -> None:
    try:
        await _send_segmented(
            to_phone_number,
            body,
            max_chars=TWILIO_SAFE_SEGMENT_CHARS,
        )
    except TwilioOutboundError as exc:
        if "21617" in str(exc.error_body or ""):
            print(
                "[Twilio Outbound] retrying with stricter segmentation "
                f"after_error={exc.status_code}"
            )
            await _send_segmented(
                to_phone_number,
                body,
                max_chars=TWILIO_RETRY_SEGMENT_CHARS,
            )
            return
        raise

