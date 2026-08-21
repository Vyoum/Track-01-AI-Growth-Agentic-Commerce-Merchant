"""Payment tool — mock order creation until Razorpay pointer."""

from __future__ import annotations

from backend.models import ConfirmationRequest
from backend.services import checkout


def create_test_order(proposal_id: str, expected_total_inr: int, idempotency_key: str | None = None):
    return checkout.confirm_proposal(
        proposal_id,
        ConfirmationRequest(
            expected_total_inr=expected_total_inr,
            idempotency_key=idempotency_key,
        ),
    )
