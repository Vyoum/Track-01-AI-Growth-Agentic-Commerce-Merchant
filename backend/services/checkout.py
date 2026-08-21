"""Deterministic checkout orchestration (no LLM)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.models import (
    ConfirmationRequest,
    CreateProposalRequest,
    PaymentRecord,
    PaymentStatus,
    Proposal,
    ProposalStatus,
)
from backend.services.cart import build_cart
from backend.services.data_loader import load_policy
from backend.services.history import get_usual_order
from backend.services import store
from backend.services.validation import validate_cart


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_proposal(req: CreateProposalRequest) -> Proposal:
    policy = load_policy()
    ttl = int(policy.get("bounds", {}).get("proposal_ttl_seconds", 600))

    product_ids = list(req.product_ids)
    quantities = dict(req.quantities)
    reasons: dict[str, str] = {}

    if req.use_usual:
        usual = get_usual_order(req.user_id)
        if not usual.items:
            raise HTTPException(status_code=404, detail="No usual order found for user")
        for item in usual.items:
            if item.product_id not in product_ids:
                product_ids.append(item.product_id)
            quantities.setdefault(item.product_id, item.qty)
            reasons[item.product_id] = item.reason

    cart, build_issues = build_cart(
        user_id=req.user_id,
        product_ids=product_ids,
        quantities=quantities,
        reasons=reasons,
    )
    issues = build_issues + validate_cart(cart, stated_budget_inr=req.stated_budget_inr)

    blocking = [i for i in issues if i.code != "budget_exceeded"]
    # Budget overflow is blocking for creating a payable proposal.
    if any(i.code == "budget_exceeded" for i in issues):
        blocking = issues

    if blocking or not cart.items:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Proposal failed validation",
                "issues": [i.model_dump() for i in issues],
            },
        )

    now = _now()
    proposal = Proposal(
        id=f"prop_{uuid.uuid4().hex[:12]}",
        user_id=req.user_id,
        session_id=req.session_id,
        status=ProposalStatus.AWAITING_CONFIRMATION,
        items=cart.items,
        total_inr=cart.total_inr,
        stated_budget_inr=req.stated_budget_inr,
        reasons=[i.reason for i in cart.items],
        issues=issues,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    store.save_proposal(proposal)
    return proposal


def get_proposal(proposal_id: str) -> Proposal:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if (
        proposal.status == ProposalStatus.AWAITING_CONFIRMATION
        and _now() > proposal.expires_at
    ):
        proposal = store.mark_proposal_status(proposal, ProposalStatus.EXPIRED)
    return proposal


def confirm_proposal(
    proposal_id: str,
    body: ConfirmationRequest,
) -> dict:
    proposal = get_proposal(proposal_id)

    if body.idempotency_key:
        existing = store.get_payment_by_idempotency(body.idempotency_key)
        if existing:
            return {
                "proposal": store.get_proposal(existing.proposal_id),
                "payment": existing,
                "idempotent_replay": True,
            }

    if proposal.status == ProposalStatus.EXPIRED:
        raise HTTPException(status_code=410, detail="Proposal expired")

    if proposal.status in {
        ProposalStatus.CANCELLED,
        ProposalStatus.FAILED,
    }:
        raise HTTPException(
            status_code=409,
            detail=f"Proposal cannot be confirmed (status={proposal.status})",
        )

    if proposal.status in {
        ProposalStatus.CONFIRMED,
        ProposalStatus.PAYMENT_PENDING,
        ProposalStatus.PAID,
    }:
        payment = store.get_payment_by_proposal(proposal_id)
        return {
            "proposal": proposal,
            "payment": payment,
            "idempotent_replay": True,
        }

    if proposal.status != ProposalStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail=f"Unexpected proposal status: {proposal.status}",
        )

    if body.expected_total_inr != proposal.total_inr:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Confirmation total mismatch — refresh proposal and confirm again",
                "expected_server_total_inr": proposal.total_inr,
                "client_expected_total_inr": body.expected_total_inr,
            },
        )

    # Gate: confirmation bound to exact proposal id + total.
    proposal = store.mark_proposal_status(proposal, ProposalStatus.CONFIRMED)
    proposal = store.mark_proposal_status(proposal, ProposalStatus.PAYMENT_PENDING)

    now = _now()
    payment = PaymentRecord(
        id=f"pay_{uuid.uuid4().hex[:12]}",
        proposal_id=proposal.id,
        status=PaymentStatus.CREATED,
        amount_inr=proposal.total_inr,
        razorpay_order_id=f"order_mock_{uuid.uuid4().hex[:14]}",
        mock=True,
        payload={
            "note": "Mock Razorpay order for pointer 3. Real Razorpay wired later.",
            "idempotency_key": body.idempotency_key,
            "currency": "INR",
            "amount_paise": proposal.total_inr * 100,
        },
        created_at=now,
        updated_at=now,
    )
    store.save_payment(payment)
    return {
        "proposal": proposal,
        "payment": payment,
        "idempotent_replay": False,
        "next_step": "Wire Razorpay Checkout in a later pointer using payment.razorpay_order_id",
    }


def cancel_proposal(proposal_id: str) -> Proposal:
    proposal = get_proposal(proposal_id)
    if proposal.status in {
        ProposalStatus.PAID,
        ProposalStatus.PAYMENT_PENDING,
        ProposalStatus.CONFIRMED,
    }:
        # Allow cancel only before money motion; pending mock can still cancel.
        if proposal.status == ProposalStatus.PAID:
            raise HTTPException(status_code=409, detail="Paid proposals cannot be cancelled")
    return store.mark_proposal_status(proposal, ProposalStatus.CANCELLED)
