#!/usr/bin/env python3
"""Smoke-test agent gates (offline) + optional Groq live call."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.orchestrator import run_agent_turn
from backend.config import get_settings
from backend.db import init_db
from backend.models import CreateProposalRequest
from backend.services import checkout


def test_gate_flow() -> None:
    init_db()
    session_id = f"agent_smoke_{uuid.uuid4().hex[:8]}"

    # Step 1: create proposal via checkout (simulates tool result)
    p = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            with_growth=True,
            session_id=session_id,
        )
    )
    assert p.status.value == "awaiting_addon_decision"

    # Seed session with proposal via first chat (fallback if no groq on usual - skip)
    from backend.agent.session import get_or_create, save

    state = get_or_create(session_id, "demo_user_01")
    state["proposal_id"] = p.id
    save(state)

    r1 = run_agent_turn(
        message="yes, add it",
        user_id="demo_user_01",
        session_id=session_id,
    )
    assert r1["handled_by"] == "addon_gate"
    assert r1["proposal"]["total_inr"] == 798
    print("gate addon ok:", r1["reply"][:80])

    r2 = run_agent_turn(
        message="confirm payment",
        user_id="demo_user_01",
        session_id=session_id,
    )
    assert r2["handled_by"] == "payment_gate"
    assert r2["payment"]["amount_inr"] == 798
    print("gate payment ok: order", r2["payment"]["razorpay_order_id"])


def test_groq_optional() -> None:
    settings = get_settings()
    if not settings.effective_llm_api_key:
        print("groq live test skipped (no GROQ_API_KEY)")
        return

    session_id = f"groq_smoke_{uuid.uuid4().hex[:8]}"
    r = run_agent_turn(
        message="Order my usual, under ₹800",
        user_id="demo_user_01",
        session_id=session_id,
    )
    assert r.get("reply")
    assert r.get("proposal") is not None
    assert r["proposal"]["total_inr"] == 699
    assert r["proposal"]["status"] == "awaiting_addon_decision"
    print("groq ok:", r["reply"][:120])


def main() -> None:
    test_gate_flow()
    test_groq_optional()
    print(json.dumps({"status": "pointer8_agent_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
