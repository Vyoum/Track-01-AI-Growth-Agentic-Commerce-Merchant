#!/usr/bin/env python3
"""Smoke-test growth path: usual ₹699 → shaker ₹99 → accept → confirm ₹798."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import init_db
from backend.models import AddonDecisionRequest, ConfirmationRequest, CreateProposalRequest
from backend.services import checkout


def main() -> None:
    init_db()

    # Hero path under ₹800
    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            with_growth=True,
        )
    )
    assert proposal.total_inr == 699
    assert proposal.status.value == "awaiting_addon_decision"
    assert proposal.growth_offer is not None
    assert proposal.growth_offer.product_id == "prod_shaker"
    assert proposal.growth_offer.projected_total_inr == 798
    assert proposal.growth_metrics and proposal.growth_metrics.recommendation_shown
    print("offer ok:", proposal.growth_offer.offer_text)

    # Cannot confirm before addon decision
    try:
        checkout.confirm_proposal(
            proposal.id,
            ConfirmationRequest(expected_total_inr=699),
        )
        raise SystemExit("expected confirm blocked")
    except Exception as exc:
        print("gate ok:", getattr(exc, "detail", exc))

    updated = checkout.decide_addon(
        proposal.id,
        AddonDecisionRequest(decision="accept", product_id="prod_shaker"),
    )
    assert updated.total_inr == 798
    assert updated.status.value == "awaiting_confirmation"
    assert updated.growth_metrics.recommendation_accepted is True
    print("accept ok: total", updated.total_inr)

    result = checkout.confirm_proposal(
        updated.id,
        ConfirmationRequest(
            expected_total_inr=798,
            idempotency_key=f"growth-smoke-{uuid.uuid4().hex[:8]}",
        ),
    )
    assert result["payment"].amount_inr == 798
    assert result["growth_summary"]["projected_uplift_inr"] == 99
    assert result["growth_summary"]["realized_paid_uplift"] is None
    print("confirm ok: uplift projected ₹99, realized still null")

    # Budget too tight for shaker — no offer (or cheaper whey sample)
    tight = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=700,
            with_growth=True,
        )
    )
    # Remaining ₹1 — shaker ₹99 ineligible; whey sample ₹79 also ineligible under ₹700 with usual 699
    # remaining = 1, so no add-on
    assert tight.growth_offer is None
    assert tight.status.value == "awaiting_confirmation"
    print("tight budget ok: no add-on under ₹700")

    # Skip path
    skip_prop = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            with_growth=True,
        )
    )
    skipped = checkout.decide_addon(skip_prop.id, AddonDecisionRequest(decision="skip"))
    assert skipped.total_inr == 699
    assert skipped.growth_metrics.recommendation_declined is True
    print("skip ok: stayed at ₹699")

    print(json.dumps({"status": "pointer4_growth_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
