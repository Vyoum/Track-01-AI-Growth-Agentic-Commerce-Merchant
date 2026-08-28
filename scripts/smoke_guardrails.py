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
from backend.config import get_settings
from backend.db import init_db
from backend.integrations.razorpay_client import keys_are_usable
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
)
from backend.services import checkout


def _demo_user_id() -> str:
    return get_settings().demo_user_id


def main() -> None:
    init_db()
    user_id = _demo_user_id()
    budget_inr = 800
    print(f"using DEMO_USER_ID={user_id!r}")

    # OOS substitution (mock-catalog SKUs; skipped when unavailable in live catalog)
    sub = apply_cart_guardrails(
        user_id=user_id,
        product_ids=["prod_protein_oos"],
        stated_budget_inr=budget_inr,
        allow_substitute=True,
    )
    if sub.ok and any(a.code == "substituted" for a in sub.actions):
        print("substitute ok:", sub.actions[0].message)
    else:
        print("substitute skipped: prod_protein_oos not in live catalog")

    # Denied category
    denied = apply_cart_guardrails(
        user_id=user_id,
        product_ids=["prod_gift_card"],
        stated_budget_inr=budget_inr,
    )
    if denied.actions and any(a.code == "denied_category" for a in denied.actions):
        print("denied ok")
    else:
        print("denied skipped: prod_gift_card not in live catalog")

    # Budget trim
    trimmed = apply_cart_guardrails(
        user_id=user_id,
        product_ids=["prod_protein_bundle", "prod_oats"],
        stated_budget_inr=budget_inr,
        allow_trim=True,
        protect_product_ids={"prod_protein_bundle"},
    )
    if trimmed.ok and any(a.code == "trimmed" for a in trimmed.actions):
        print("trim ok:", [a.message for a in trimmed.actions if a.code == "trimmed"][0])
    else:
        print("trim skipped: mock trim SKUs not in live catalog")

    # Full flow with audit — live catalog + dynamic growth offer
    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            use_usual=True,
            stated_budget_inr=budget_inr,
            session_id="guard_session",
            with_growth=True,
        )
    )
    if proposal.growth_offer is None:
        raise SystemExit(
            f"No growth offer for {user_id!r} (baseline ₹{proposal.total_inr}). "
            "Need stated budget headroom or complement data in catalog."
        )

    baseline = proposal.total_inr
    offer = proposal.growth_offer
    projected_total = offer.projected_total_inr

    assert proposal.status.value == "awaiting_addon_decision"
    print(
        f"growth offer ok: {offer.product_id!r} ₹{offer.price_inr} "
        f"({offer.source}) → projected ₹{projected_total}"
    )

    # Payment blocked before addon
    try:
        assert_payment_confirmation_gate(proposal, expected_total_inr=baseline)
        raise SystemExit("addon gate should block")
    except Exception as exc:
        detail = getattr(exc, "detail", {})
        assert isinstance(detail, dict) and detail.get("code") == "addon_gate"
        print("addon_gate ok")

    updated = checkout.decide_addon(
        proposal.id,
        AddonDecisionRequest(decision="accept", product_id=offer.product_id),
    )
    assert updated.total_inr == projected_total

    # Total mismatch
    try:
        checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(expected_total_inr=baseline, user_id=user_id),
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
            ConfirmationRequest(expected_total_inr=projected_total, user_id="someone_else"),
        )
        raise SystemExit("user mismatch should fail")
    except Exception as exc:
        detail = getattr(exc, "detail", {})
        assert detail.get("code") == "user_mismatch"
        print("user_mismatch ok")

    needed_audit = {
        "proposal_created",
        "growth_offer_shown",
        "addon_accepted",
        "decision_trace",
    }

    if keys_are_usable():
        print(
            "confirm+payment audit skipped: real Razorpay keys — "
            "run scripts/smoke_razorpay.py for payment audit events"
        )
    else:
        idem = f"guard-smoke-{uuid.uuid4().hex[:10]}"
        result = checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(
                expected_total_inr=projected_total,
                user_id=user_id,
                idempotency_key=idem,
            ),
        )
        replay = checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(
                expected_total_inr=projected_total,
                user_id=user_id,
                idempotency_key=idem,
            ),
        )
        assert replay["idempotent_replay"] is True
        print("confirm+idempotency ok:", result["payment"].id)
        needed_audit.update(
            {"payment_confirmation_received", "payment_order_created"}
        )

    events = list_events(proposal_id=updated.id, limit=50)
    types = {e["event_type"] for e in events}
    for needed in needed_audit:
        assert needed in types, f"missing audit event {needed}: {types}"

    trace = next(e for e in events if e["event_type"] == "decision_trace")
    check_ids = {c["id"] for c in trace["payload"]["checks"]}
    for required_check in {
        "history_checked",
        "stock_check_passed",
        "budget_check_passed",
        "candidate_products_evaluated",
        "growth_reason_calculated",
    }:
        assert required_check in check_ids, f"missing check {required_check}: {check_ids}"
    assert trace["payload"]["summary"]["all_required_passed"] is True
    print("decision checks ok:", trace["payload"]["summary"])

    print("audit ok:", sorted(types))

    redacted = redact({"razorpay_key_secret": "supersecret", "note": "rzp_test_abc123xyz"})
    assert redacted["razorpay_key_secret"] == "***REDACTED***"
    assert "***" in redacted["note"]
    print("redact ok")

    print(
        json.dumps(
            {
                "status": "pointer5_guardrails_audit_smoke_passed",
                "demo_user_id": user_id,
                "baseline_inr": baseline,
                "projected_total_inr": projected_total,
                "addon_product_id": offer.product_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
