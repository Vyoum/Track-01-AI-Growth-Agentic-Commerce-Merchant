"""Checkout Agent API — deterministic checkout core."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes_checkout import router as checkout_router
from backend.config import get_settings
from backend.db import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db_path = init_db()
    _app.state.db_path = str(db_path)
    _app.state.started_at = datetime.now(timezone.utc).isoformat()
    yield


app = FastAPI(
    title="Conversational Checkout Agent",
    description="Razorpay Buildathon Track 01 — AI Growth & Agentic Commerce (test mode)",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(checkout_router)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "razorpay_mode": settings.razorpay_mode,
        "use_mock_catalog": settings.use_mock_catalog,
        "demo_user_id": settings.demo_user_id,
        "db_path": getattr(app.state, "db_path", None),
        "started_at": getattr(app.state, "started_at", None),
        "razorpay_key_configured": bool(settings.razorpay_key_id),
        "razorpay_key_is_test": (
            settings.razorpay_key_id.startswith("rzp_test_")
            if settings.razorpay_key_id
            else None
        ),
        "checkout_core": True,
    }


@app.get("/api/meta")
def meta() -> dict:
    return {
        "merchant": "Demo Fitness Store",
        "track": "Track 01 — AI Growth & Agentic Commerce",
        "demo_user_id": settings.demo_user_id,
        "currency": "INR",
        "features": {
            "chat": True,
            "agent": False,
            "growth": False,
            "checkout_core": True,
            "razorpay": False,
        },
        "message": "Checkout core ready: products → usual → proposal → confirm (mock payment order).",
    }
