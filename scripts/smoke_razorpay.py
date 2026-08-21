#!/usr/bin/env python3
"""Smoke-test Razorpay path (mock fallback when keys are placeholders)."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import init_db
from backend.integrations.razorpay_client import keys_are_usable, get_razorpay_client
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    FailPaymentRequest,
    VerifyPaymentRequest,
)
from backend.services import checkout


def main() -> None:
    init_db()
    client = get_razorpay_client()
    print("razorpay_test_ready:", keys_are_usable(), "client.live_ready:", client.live_ready)

    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id="demo_user_01",
            use_usual=True,
            stated_budget_inr=800,
            with_growth=True,
        )
    )
    proposal = checkout.decide_addon(
        proposal.id,
        AddonDecisionRequest(decision="accept", product_id="prod_shaker"),
    )
    result = checkout.confirm_proposal(
        proposal.id,
        ConfirmationRequest(
            expected_total_inr=798,
            user_id="demo_user_01",
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
        assert verified["growth_summary"]["realized_paid_uplift"] == 99
        print("mock verify ok: realized uplift ₹99")

        # Failure path on a fresh proposal
        p2 = checkout.create_proposal(
            CreateProposalRequest(
                user_id="demo_user_01",
                use_usual=True,
                stated_budget_inr=800,
                with_growth=False,
            )
        )
        c2 = checkout.confirm_proposal(
            p2.id,
            ConfirmationRequest(expected_total_inr=699, user_id="demo_user_01"),
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

    print(json.dumps({"status": "pointer7_razorpay_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
