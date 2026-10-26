"""Judge-facing demo routes — aggregate growth metrics and batch replay."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.services.data_loader import load_demo_users
from backend.services.demo_metrics import MAX_EVENTS, compute_growth_metrics
from backend.services.demo_replay import MAX_SESSIONS, SCENARIO_WEIGHTS, run_replay

router = APIRouter(prefix="/api", tags=["demo"])


class ReplayRequest(BaseModel):
    sessions: int = Field(default=25, ge=1, le=MAX_SESSIONS)
    seed: int | None = Field(default=7, ge=0, le=1_000_000)


@router.get("/demo/customers")
def api_demo_customers():
    """Demo customer book used by the checkout user switcher."""
    data = load_demo_users()
    orders = data.get("orders", [])
    customers = []
    for user in data.get("users", []):
        user_id = user.get("user_id")
        completed = [
            order
            for order in orders
            if order.get("user_id") == user_id and order.get("status") == "completed"
        ]
        latest = max(completed, key=lambda order: order.get("created_at", "")) if completed else None
        customers.append(
            {
                "user_id": user_id,
                "name": user.get("name"),
                "email": user.get("email"),
                "phone": user.get("phone"),
                "city": user.get("city"),
                "signup_date": user.get("signup_date"),
                "preferred_budget_inr": user.get("preferred_budget_inr"),
                "preferences": user.get("preferences") or {},
                "notes": user.get("notes"),
                "completed_orders": len(completed),
                "usual_order_id": latest.get("order_id") if latest else None,
                "usual_total_inr": latest.get("total_inr") if latest else None,
            }
        )
    return {
        "demo_user_id": get_settings().demo_user_id,
        "customers": customers,
    }


@router.get("/metrics/growth")
def api_growth_metrics(
    limit: int = Query(default=MAX_EVENTS, ge=1, le=MAX_EVENTS),
):
    return compute_growth_metrics(limit=limit)


@router.get("/demo/scenarios")
def api_demo_scenarios():
    return {
        "max_sessions": MAX_SESSIONS,
        "scenarios": [
            {"id": sid, "weight": weight, "label": label}
            for sid, weight, label in SCENARIO_WEIGHTS
        ],
    }


@router.post("/demo/replay")
def api_demo_replay(body: ReplayRequest | None = None):
    payload = body or ReplayRequest()
    return run_replay(sessions=payload.sessions, seed=payload.seed)
