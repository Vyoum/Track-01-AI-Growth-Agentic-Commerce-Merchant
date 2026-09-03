"""Judge-facing demo routes — aggregate growth metrics and batch replay."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.services.demo_metrics import MAX_EVENTS, compute_growth_metrics
from backend.services.demo_replay import MAX_SESSIONS, SCENARIO_WEIGHTS, run_replay

router = APIRouter(prefix="/api", tags=["demo"])


class ReplayRequest(BaseModel):
    sessions: int = Field(default=25, ge=1, le=MAX_SESSIONS)
    seed: int | None = Field(default=7, ge=0, le=1_000_000)


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
