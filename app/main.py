from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import SCHEDULER_ENABLED
from app.routes.webhooks import router as webhooks_router
from app.services.proactive_engine import build_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    if SCHEDULER_ENABLED:
        scheduler = build_scheduler()
        scheduler.start()
        print("[Scheduler] Started proactive engine.")
    else:
        print("[Scheduler] Disabled by config.")

    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
            print("[Scheduler] Stopped proactive engine.")

app = FastAPI(
    title="Apna Coach API",
    version="0.1.0",
    description="Core API for Apna Coach conversational fitness backend.",
    lifespan=lifespan,
)
app.include_router(webhooks_router)

