#!/usr/bin/env python3
"""Smoke-test agent gates (offline) + optional Groq live call."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.orchestrator import run_agent_turn
from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.models import CreateProposalRequest
from backend.services import checkout


def _mock_env() -> dict[str, str]:
    return {
        "USE_MOCK_CATALOG": "true",
        "DEMO_USER_ID": "demo_user_01",
    }


def test_gate_flow() -> None:
    """Deterministic gate test — always uses frozen mock catalog (₹699 + ₹99)."""
    with patch.dict(os.environ, _mock_env()):
        get_settings.cache_clear()
        reset_store_client()
        init_db()
        session_id = f"agent_smoke_{uuid.uuid4().hex[:8]}"

        p = checkout.create_proposal(
            CreateProposalRequest(
                user_id="demo_user_01",
                use_usual=True,
                stated_budget_inr=800,
                with_growth=True,
                session_id=session_id,
            )
        )
        assert p.status.value == "awaiting_addon_decision", p.status.value
        assert p.proposal_source == "completed_order_history", p.proposal_source

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
        assert r1["proposal"]["total_inr"] == 798, r1["proposal"]["total_inr"]
        print("gate addon ok:", r1["reply"][:80])

        r2 = run_agent_turn(
            message="confirm payment",
            user_id="demo_user_01",
            session_id=session_id,
        )
        assert r2["handled_by"] == "payment_gate"
        assert r2["payment"]["amount_inr"] == 798
        print("gate payment ok: order", r2["payment"]["razorpay_order_id"])

    get_settings.cache_clear()
    reset_store_client()


def test_groq_optional() -> None:
    """Live Groq call using current .env (mock or Supabase)."""
    get_settings.cache_clear()
    reset_store_client()
    settings = get_settings()
    if not settings.effective_llm_api_key:
        print("groq live test skipped (no GROQ_API_KEY)")
        return

    user_id = settings.demo_user_id
    session_id = f"groq_smoke_{uuid.uuid4().hex[:8]}"
    r = run_agent_turn(
        message="Order my usual, under ₹800",
        user_id=user_id,
        session_id=session_id,
    )
    assert r.get("reply"), "expected Groq reply"
    assert r.get("proposal") is not None, "expected proposal from Groq tool loop"

    proposal = r["proposal"]
    source = proposal.get("proposal_source")
    total = proposal.get("total_inr")
    status = proposal.get("status")

    if source == "completed_order_history":
        assert total == 699
        assert status == "awaiting_addon_decision"
    elif source == "bestsellers":
        assert total > 0
        assert status in {"awaiting_confirmation", "awaiting_addon_decision"}
        assert proposal.get("source_reason")
    else:
        raise AssertionError(f"unexpected proposal_source: {source}")

    print("groq ok:", r["reply"][:120])
    print(f"groq proposal: source={source} total={total} status={status}")


def main() -> None:
    test_gate_flow()
    test_groq_optional()
    print(json.dumps({"status": "pointer8_agent_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
