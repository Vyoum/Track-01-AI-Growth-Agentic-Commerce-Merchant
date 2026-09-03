"""Checkout Agent API — merchant adapter + Razorpay test checkout."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend.api.routes_agent import router as agent_router
from backend.api.routes_checkout import router as checkout_router
from backend.api.routes_demo import router as demo_router
from backend.api.routes_merchant_mock import router as merchant_mock_router
from backend.api.routes_well_known import router as well_known_router
from backend.config import get_settings
from backend.db import init_db
from backend.integrations.razorpay_client import keys_are_usable
from backend.services import store_source

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
    version="0.4.0",
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
app.include_router(demo_router)
app.include_router(agent_router)
app.include_router(merchant_mock_router)
app.include_router(well_known_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    razorpay_ready = keys_are_usable(settings)
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "razorpay_mode": settings.razorpay_mode,
        "use_mock_catalog": settings.use_mock_catalog,
        "catalog_source": store_source.source_label(),
        "store_provider": settings.resolved_store_provider,
        "store_api_configured": bool(settings.store_api_base_url.strip()),
        "supabase_configured": bool(
            settings.effective_supabase_url.strip()
            and settings.effective_supabase_key.strip()
        ),
        "demo_user_id": settings.demo_user_id,
        "db_path": getattr(app.state, "db_path", None),
        "started_at": getattr(app.state, "started_at", None),
        "razorpay_key_configured": bool(settings.razorpay_key_id),
        "razorpay_key_is_test": (
            settings.razorpay_key_id.startswith("rzp_test_")
            if settings.razorpay_key_id
            else None
        ),
        "razorpay_test_ready": razorpay_ready,
        "checkout_core": True,
    }


@app.get("/api/meta")
def meta() -> dict:
    razorpay_ready = keys_are_usable(settings)
    source = store_source.source_label()
    return {
        "merchant": "Demo Fitness Store",
        "track": "Track 01 — AI Growth & Agentic Commerce",
        "demo_user_id": settings.demo_user_id,
        "currency": "INR",
        "catalog_source": source,
        "features": {
            "chat": True,
            "agent": bool(settings.effective_llm_api_key),
            "growth": True,
            "campaigns": True,
            "merchant_approval": True,
            "checkout_core": True,
            "razorpay": True,
            "razorpay_test_ready": razorpay_ready,
            "merchant_adapter": True,
            "growth_metrics": True,
            "demo_replay": True,
        },
        "message": (
            f"Catalog source: {source}. "
            + (
                "Chat agent ready (Groq). Try: 'Order my usual, under ₹800'."
                if settings.effective_llm_api_key
                else "Add GROQ_API_KEY for full chat; gates + fallback still work."
            )
            + " Growth campaigns require merchant approval before customer offer."
        ),
    }
