from fastapi import FastAPI

from app.routes.webhooks import router as webhooks_router

app = FastAPI(
    title="Apna Coach API",
    version="0.1.0",
    description="Core API for Apna Coach conversational fitness backend.",
)
app.include_router(webhooks_router)

