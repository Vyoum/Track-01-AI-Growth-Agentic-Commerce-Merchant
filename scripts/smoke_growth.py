#!/usr/bin/env python3
"""Smoke-test growth path: usual → optional add-on → accept/skip → confirm."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.razorpay_client import keys_are_usable
from backend.models import AddonDecisionRequest, ConfirmationRequest, CreateProposalRequest
from backend.services import checkout


def _demo_user_id() -> str:
    return get_settings().demo_user_id


def main() -> None:
    init_db()
    user_id = _demo_user_id()
    budget_inr = 800
    print(f"using DEMO_USER_ID={user_id!r}")

    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            use_usual=True,
            stated_budget_inr=budget_inr,
            with_growth=True,
        )
    )
    baseline = proposal.total_inr
    if proposal.growth_offer is None:
        raise SystemExit(
            f"No growth offer for user {user_id!r} (baseline ₹{baseline}). "
            "Ensure stated budget leaves headroom or catalog has complement data."
        )

    offer = proposal.growth_offer
    projected_total = offer.projected_total_inr
    uplift = offer.uplift_amount_inr

    assert proposal.status.value == "awaiting_addon_decision"
    assert offer.product_id
    assert projected_total == baseline + uplift
    assert projected_total <= budget_inr
    assert proposal.growth_metrics and proposal.growth_metrics.recommendation_shown
    print("offer ok:", offer.offer_text)

    # Cannot confirm before addon decision
    try:
        checkout.confirm_proposal(
            proposal.id,
            ConfirmationRequest(expected_total_inr=baseline, user_id=user_id),
        )
        raise SystemExit("expected confirm blocked")
    except Exception as exc:
        print("gate ok:", getattr(exc, "detail", exc))

    updated = checkout.decide_addon(
        proposal.id,
        AddonDecisionRequest(decision="accept", product_id=offer.product_id),
    )
    assert updated.total_inr == projected_total
    assert updated.status.value == "awaiting_confirmation"
    assert updated.growth_metrics.recommendation_accepted is True
    print("accept ok: total", updated.total_inr)

    if keys_are_usable():
        print(
            "confirm skipped: real Razorpay test keys configured — "
            "run scripts/smoke_razorpay.py for payment + verify"
        )
    else:
        result = checkout.confirm_proposal(
            updated.id,
            ConfirmationRequest(
                expected_total_inr=projected_total,
                user_id=user_id,
                idempotency_key=f"growth-smoke-{uuid.uuid4().hex[:8]}",
            ),
        )
        assert result["payment"].amount_inr == projected_total
        assert result["growth_summary"]["projected_uplift_inr"] == uplift
        assert result["growth_summary"]["realized_paid_uplift"] is None
        print(f"confirm ok: uplift projected ₹{uplift}, realized still null")

    # Budget too tight for any add-on (reuse same base cart shape as hero path)
    tight_budget = baseline
    base_product_ids = [item.product_id for item in proposal.items]
    tight = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            product_ids=base_product_ids if base_product_ids else None,
            use_usual=not base_product_ids,
            stated_budget_inr=tight_budget,
            with_growth=True,
        )
    )
    assert tight.growth_offer is None
    assert tight.status.value == "awaiting_confirmation"
    print(f"tight budget ok: no add-on at exact baseline ₹{tight_budget}")

    # Skip path
    skip_prop = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            product_ids=base_product_ids if base_product_ids else None,
            use_usual=not base_product_ids,
            stated_budget_inr=budget_inr,
            with_growth=True,
        )
    )
    assert skip_prop.growth_offer is not None
    skipped = checkout.decide_addon(skip_prop.id, AddonDecisionRequest(decision="skip"))
    assert skipped.total_inr == skip_prop.total_inr
    assert skipped.growth_metrics.recommendation_declined is True
    print(f"skip ok: stayed at ₹{skipped.total_inr}")

    print(
        json.dumps(
            {
                "status": "pointer4_growth_smoke_passed",
                "demo_user_id": user_id,
                "baseline_inr": baseline,
                "projected_total_inr": projected_total,
                "uplift_inr": uplift,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
