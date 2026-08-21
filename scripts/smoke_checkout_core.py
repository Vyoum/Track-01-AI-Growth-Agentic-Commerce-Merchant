#!/usr/bin/env python3
"""Smoke-test deterministic checkout: usual → proposal → confirm."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import init_db
from backend.models import ConfirmationRequest, CreateProposalRequest
from backend.services import catalog, checkout, history


def main() -> None:
    init_db()

    products = catalog.search_products("protein")
    assert products, "expected protein products"
    print(f"search ok: {len(products)} products")

    usual = history.get_usual_order("demo_user_01")
    assert usual.total_inr == 699, usual
    print(f"usual ok: {usual.order_id} total={usual.total_inr}")

    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            session_id="smoke_session",
            with_growth=False,
        )
    )
    assert proposal.total_inr == 699
    assert proposal.status.value == "awaiting_confirmation"
    print(f"proposal ok: {proposal.id} total={proposal.total_inr}")

    # budget breach
    try:
        checkout.create_proposal(
            CreateProposalRequest(
                user_id="demo_user_01",
                product_ids=["prod_protein_bundle", "prod_creatine"],
                stated_budget_inr=800,
            )
        )
        raise SystemExit("expected budget failure")
    except Exception as exc:  # HTTPException
        detail = getattr(exc, "detail", str(exc))
        print(f"budget guard ok: {detail}")

    result = checkout.confirm_proposal(
        proposal.id,
        ConfirmationRequest(expected_total_inr=699, idempotency_key="smoke-1"),
    )
    payment = result["payment"]
    assert payment is not None
    assert payment.amount_inr == 699
    assert payment.mock is True
    print(f"confirm ok: payment={payment.id} order={payment.razorpay_order_id}")

    replay = checkout.confirm_proposal(
        proposal.id,
        ConfirmationRequest(expected_total_inr=699, idempotency_key="smoke-1"),
    )
    assert replay["idempotent_replay"] is True
    print("idempotency ok")

    print(json.dumps({"status": "pointer3_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
