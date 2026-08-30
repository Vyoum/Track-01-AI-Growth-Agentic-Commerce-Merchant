#!/usr/bin/env python3
"""Smoke-test Razorpay path (mock fallback when keys are placeholders)."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.razorpay_client import keys_are_usable, get_razorpay_client
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    FailPaymentRequest,
    MerchantApprovalRequest,
    VerifyPaymentRequest,
)
from backend.services import checkout


def _demo_user_id() -> str:
    return get_settings().demo_user_id


def main() -> None:
    init_db()
    user_id = _demo_user_id()
    budget_inr = 800
    client = get_razorpay_client()
    print(f"using DEMO_USER_ID={user_id!r}")
    print("razorpay_test_ready:", keys_are_usable(), "client.live_ready:", client.live_ready)

    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            use_usual=True,
            stated_budget_inr=budget_inr,
            with_growth=True,
        )
    )
    if proposal.growth_offer is None:
        raise SystemExit(
            f"No growth offer for {user_id!r} (baseline ₹{proposal.total_inr})"
        )

    offer = proposal.growth_offer
    projected_total = offer.projected_total_inr
    uplift = offer.uplift_amount_inr

    assert proposal.status.value == "awaiting_merchant_approval"
    proposal = checkout.decide_merchant_campaign(
        proposal.id,
        MerchantApprovalRequest(decision="approve", note="razorpay smoke"),
    )

    proposal = checkout.decide_addon(
        proposal.id,
        AddonDecisionRequest(decision="accept", product_id=offer.product_id),
    )
    assert proposal.total_inr == projected_total

    result = checkout.confirm_proposal(
        proposal.id,
        ConfirmationRequest(
            expected_total_inr=projected_total,
            user_id=user_id,
            idempotency_key=f"rzp-smoke-{uuid.uuid4().hex[:8]}",
        ),
    )
    payment = result["payment"]
    checkout_payload = result["checkout"]
    assert checkout_payload["order_id"] == payment.razorpay_order_id
    assert payment.status.value == "pending"
    print("order created:", payment.razorpay_order_id, "mock=", payment.mock)

    if payment.mock:
        verified = checkout.verify_payment(
            VerifyPaymentRequest(
                payment_id=payment.id,
                razorpay_order_id=payment.razorpay_order_id,
                razorpay_payment_id=f"pay_mock_{uuid.uuid4().hex[:8]}",
                razorpay_signature="mock_ok_smoke",
            )
        )
        assert verified["payment"].status.value == "paid"
        assert verified["growth_summary"]["realized_paid_uplift"] == uplift
        print(f"mock verify ok: realized uplift ₹{uplift}")

        # Failure path on a fresh proposal (no growth)
        p2 = checkout.create_proposal(
            CreateProposalRequest(
                user_id=user_id,
                use_usual=True,
                stated_budget_inr=budget_inr,
                with_growth=False,
            )
        )
        baseline = p2.total_inr
        c2 = checkout.confirm_proposal(
            p2.id,
            ConfirmationRequest(expected_total_inr=baseline, user_id=user_id),
        )
        failed = checkout.fail_payment(
            p2.id, FailPaymentRequest(reason="user_cancelled")
        )
        assert failed["payment"].status.value == "failed"
        print("fail path ok:", failed["message"])
    else:
        print(
            "Real test keys detected — complete payment in Checkout UI, "
            "then POST /api/payments/verify with Razorpay response fields."
        )

    print(
        json.dumps(
            {
                "status": "pointer7_razorpay_smoke_passed",
                "demo_user_id": user_id,
                "projected_total_inr": projected_total,
                "addon_product_id": offer.product_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
