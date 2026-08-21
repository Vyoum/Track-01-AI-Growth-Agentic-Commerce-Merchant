"""Razorpay payment tool."""

from __future__ import annotations

from backend.models import ConfirmationRequest, VerifyPaymentRequest
from backend.services import checkout


def create_test_order(
    proposal_id: str,
    expected_total_inr: int,
    idempotency_key: str | None = None,
):
    return checkout.confirm_proposal(
        proposal_id,
        ConfirmationRequest(
            expected_total_inr=expected_total_inr,
            idempotency_key=idempotency_key,
        ),
    )


def verify_test_payment(
    *,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    payment_id: str | None = None,
):
    return checkout.verify_payment(
        VerifyPaymentRequest(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            payment_id=payment_id,
        )
    )
