#!/usr/bin/env python3
"""Smoke-test guardrails + audit: substitute, trim, gates, audit trail."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.guardrails import apply_cart_guardrails, assert_payment_confirmation_gate
from backend.audit.logger import list_events, redact
from backend.db import init_db
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
)
from backend.services import checkout


def main() -> None:
    init_db()

    # OOS substitution
    sub = apply_cart_guardrails(
        user_id="demo_user_01",
        product_ids=["prod_protein_oos"],
        stated_budget_inr=800,
        allow_substitute=True,
    )
    assert sub.ok
    assert sub.cart.items[0].product_id == "prod_protein_bundle"
    assert any(a.code == "substituted" for a in sub.actions)
    print("substitute ok:", sub.actions[0].message)

    # Denied category
    denied = apply_cart_guardrails(
        user_id="demo_user_01",
        product_ids=["prod_gift_card"],
        stated_budget_inr=800,
    )
    assert not denied.ok or not denied.cart.items
    assert any(a.code == "denied_category" for a in denied.actions)
    print("denied ok")

    # Budget trim: protein 699 + oats 249 = 948, budget 800, protect protein
    trimmed = apply_cart_guardrails(
        user_id="demo_user_01",
        product_ids=["prod_protein_bundle", "prod_oats"],
        stated_budget_inr=800,
        allow_trim=True,
        protect_product_ids={"prod_protein_bundle"},
    )
    assert trimmed.ok
    assert trimmed.cart.total_inr == 699
    assert any(a.code == "trimmed" for a in trimmed.actions)
    print("trim ok:", [a.message for a in trimmed.actions if a.code == "trimmed"][0])

    # Full flow with audit
    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            session_id="guard_session",
            with_growth=True,
        )
    )
    assert proposal.status.value == "awaiting_addon_decision"

    # Payment blocked before addon
    try:
        assert_payment_confirmation_gate(proposal, expected_total_inr=699)
        raise SystemExit("addon gate should block")
    except Exception as exc:
        detail = getattr(exc, "detail", {})
        assert isinstance(detail, dict) and detail.get("code") == "addon_gate"
        print("addon_gate ok")

    updated = checkout.decide_addon(
        proposal.id, AddonDecisionRequest(decision="accept", product_id="prod_shaker")
    )
    assert updated.total_inr == 798

    # Total mismatch
    try:
        checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(expected_total_inr=699, user_id="demo_user_01"),
        )
        raise SystemExit("mismatch should fail")
    except Exception as exc:
        detail = getattr(exc, "detail", {})
        assert detail.get("code") == "total_mismatch" or "mismatch" in str(detail).lower()
        print("total_mismatch ok")

    # User mismatch
    try:
        checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(expected_total_inr=798, user_id="someone_else"),
        )
        raise SystemExit("user mismatch should fail")
    except Exception as exc:
        detail = getattr(exc, "detail", {})
        assert detail.get("code") == "user_mismatch"
        print("user_mismatch ok")

    idem = f"guard-smoke-{uuid.uuid4().hex[:10]}"
    result = checkout.confirm_proposal(
        updated.id,
        ConfirmationRequest(
            expected_total_inr=798,
            user_id="demo_user_01",
            idempotency_key=idem,
        ),
    )
    replay = checkout.confirm_proposal(
        updated.id,
        ConfirmationRequest(
            expected_total_inr=798,
            user_id="demo_user_01",
            idempotency_key=idem,
        ),
    )
    assert replay["idempotent_replay"] is True
    print("confirm+idempotency ok:", result["payment"].id)

    events = list_events(proposal_id=updated.id, limit=50)
    types = {e["event_type"] for e in events}
    for needed in {
        "proposal_created",
        "growth_offer_shown",
        "addon_accepted",
        "payment_confirmation_received",
        "payment_order_created",
    }:
        assert needed in types, f"missing audit event {needed}: {types}"
    print("audit ok:", sorted(types))

    redacted = redact({"razorpay_key_secret": "supersecret", "note": "rzp_test_abc123xyz"})
    assert redacted["razorpay_key_secret"] == "***REDACTED***"
    assert "***" in redacted["note"]
    print("redact ok")

    print(json.dumps({"status": "pointer5_guardrails_audit_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
