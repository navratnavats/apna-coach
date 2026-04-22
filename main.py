"""
Apna Coach FastAPI entrypoint.

This module provides:
- App initialization
- Environment loading
- Global Supabase client setup
- Health check endpoint
- WhatsApp webhook verification endpoint
- WhatsApp webhook receive endpoint
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from supabase import Client, create_client

# Load variables from .env into process environment.
# override=True avoids stale shell exports (e.g. empty values) shadowing .env.
load_dotenv(override=True)


def _required_env(name: str) -> str:
    """
    Return an environment variable or fail fast with a clear message.

    Failing at startup (instead of failing mid-request) is production-friendly because
    configuration issues surface immediately during deployment.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# Core environment configuration.
SUPABASE_URL: str = _required_env("SUPABASE_URL")
SUPABASE_KEY: str = _required_env("SUPABASE_KEY")
WHATSAPP_VERIFY_TOKEN: str = _required_env("WHATSAPP_VERIFY_TOKEN")

# Global Supabase client.
# Keeping this at module scope avoids re-creating the client per request.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# FastAPI application instance.
app = FastAPI(
    title="Apna Coach API",
    version="0.1.0",
    description="Core API for Apna Coach conversational fitness backend.",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """
    Lightweight health endpoint for uptime checks.
    """
    return {"status": "bhai_is_alive"}


@app.get("/webhook")
async def verify_whatsapp_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_challenge: Optional[int] = Query(default=None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
) -> int:
    """
    WhatsApp webhook verification handshake.

    Meta sends a verification request containing:
    - hub.mode
    - hub.challenge
    - hub.verify_token

    We must return hub.challenge only when:
    - mode == "subscribe"
    - verify token matches our configured token
    """
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


@app.post("/webhook")
async def receive_whatsapp_webhook(request: Request) -> JSONResponse:
    """
    Receive incoming WhatsApp webhook payloads.

    For now:
    - Parse request body as JSON
    - Print full payload for inspection/debugging
    - Return standard acknowledgement
    """
    payload: Any = await request.json()

    # Pretty print helps while integrating with Meta webhook payload formats.
    print("Incoming WhatsApp webhook payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    return JSONResponse(content={"status": "ok"})
