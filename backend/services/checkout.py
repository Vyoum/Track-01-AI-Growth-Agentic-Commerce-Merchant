"""Deterministic checkout orchestration (no LLM)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.models import (
    AddonDecisionRequest,
    Cart,
    ConfirmationRequest,
    CreateProposalRequest,
    PaymentRecord,
    PaymentStatus,
    Proposal,
    ProposalStatus,
)
from backend.services.cart import build_cart, build_line_item
from backend.services.data_loader import load_policy
from backend.services.growth import recommend_addon
from backend.services.history import get_usual_order
from backend.services import store
from backend.services.validation import validate_cart


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _attach_growth(proposal: Proposal) -> Proposal:
    cart = Cart(user_id=proposal.user_id, items=list(proposal.items))
    result = recommend_addon(
        cart=cart,
        stated_budget_inr=proposal.stated_budget_inr,
        rejected_addon_ids=proposal.rejected_addon_ids,
        user_id=proposal.user_id,
    )
    proposal.baseline_total_inr = proposal.total_inr
    proposal.growth_metrics = result.metrics
    if result.offer:
        proposal.growth_offer = result.offer
        proposal.status = ProposalStatus.AWAITING_ADDON_DECISION
    else:
        proposal.growth_offer = None
        proposal.status = ProposalStatus.AWAITING_CONFIRMATION
    store.save_proposal(proposal)
    return proposal


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

    blocking = list(issues)
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
        baseline_total_inr=cart.total_inr,
    )
    store.save_proposal(proposal)

    if req.with_growth:
        proposal = _attach_growth(proposal)
    return proposal


def get_proposal(proposal_id: str) -> Proposal:
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status in {
        ProposalStatus.AWAITING_CONFIRMATION,
        ProposalStatus.AWAITING_ADDON_DECISION,
    } and _now() > proposal.expires_at:
        proposal = store.mark_proposal_status(proposal, ProposalStatus.EXPIRED)
    return proposal


def decide_addon(proposal_id: str, body: AddonDecisionRequest) -> Proposal:
    proposal = get_proposal(proposal_id)
    decision = (body.decision or "").strip().lower()
    if decision not in {"accept", "skip"}:
        raise HTTPException(status_code=400, detail="decision must be 'accept' or 'skip'")

    if proposal.status == ProposalStatus.EXPIRED:
        raise HTTPException(status_code=410, detail="Proposal expired")

    if proposal.status != ProposalStatus.AWAITING_ADDON_DECISION:
        raise HTTPException(
            status_code=409,
            detail=f"No pending add-on decision (status={proposal.status})",
        )

    if not proposal.growth_offer:
        raise HTTPException(status_code=409, detail="No growth offer on proposal")

    offer = proposal.growth_offer
    if body.product_id and body.product_id != offer.product_id:
        raise HTTPException(
            status_code=409,
            detail="product_id does not match the current growth offer",
        )

    metrics = proposal.growth_metrics
    if metrics is None:
        from backend.models import GrowthMetrics

        metrics = GrowthMetrics(baseline_order_value=proposal.baseline_total_inr or proposal.total_inr)

    if decision == "skip":
        proposal.rejected_addon_ids.append(offer.product_id)
        metrics.recommendation_declined = True
        metrics.recommendation_accepted = False
        metrics.accepted_order_value = proposal.total_inr
        metrics.projected_order_value = offer.projected_total_inr
        metrics.uplift_amount = offer.uplift_amount_inr
        metrics.uplift_percent = offer.uplift_percent
        proposal.growth_metrics = metrics
        proposal.growth_offer = None
        proposal.status = ProposalStatus.AWAITING_CONFIRMATION
        store.save_proposal(proposal)
        return proposal

    # accept — update cart with server prices; require fresh payment confirmation after
    line, issue = build_line_item(
        offer.product_id,
        qty=1,
        reason=f"growth add-on: {offer.reason}",
    )
    if issue or line is None:
        raise HTTPException(
            status_code=400,
            detail={"message": "Add-on no longer eligible", "issue": issue.model_dump() if issue else None},
        )

    new_items = list(proposal.items) + [line]
    new_cart = Cart(user_id=proposal.user_id, items=new_items)
    issues = validate_cart(new_cart, stated_budget_inr=proposal.stated_budget_inr)
    if issues:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Add-on failed guardrail validation",
                "issues": [i.model_dump() for i in issues],
            },
        )

    proposal.items = new_items
    proposal.total_inr = new_cart.total_inr
    proposal.reasons = [i.reason for i in new_items]
    proposal.issues = issues
    metrics.recommendation_accepted = True
    metrics.recommendation_declined = False
    metrics.accepted_order_value = proposal.total_inr
    metrics.projected_order_value = offer.projected_total_inr
    metrics.uplift_amount = offer.uplift_amount_inr
    metrics.uplift_percent = offer.uplift_percent
    proposal.growth_metrics = metrics
    proposal.growth_offer = None
    proposal.status = ProposalStatus.AWAITING_CONFIRMATION
    store.save_proposal(proposal)
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

    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION:
        raise HTTPException(
            status_code=409,
            detail="Resolve the optional add-on offer (accept/skip) before payment confirmation",
        )

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

    proposal = store.mark_proposal_status(proposal, ProposalStatus.CONFIRMED)
    proposal = store.mark_proposal_status(proposal, ProposalStatus.PAYMENT_PENDING)

    # Realized uplift is recorded only after a successful paid status (later pointer).
    # For mock confirm we store projected accepted uplift separately from realized.
    if proposal.growth_metrics and proposal.growth_metrics.recommendation_accepted:
        baseline = proposal.baseline_total_inr or proposal.growth_metrics.baseline_order_value
        proposal.growth_metrics.accepted_order_value = proposal.total_inr
        # Not realized until Razorpay PAID — leave realized_paid_uplift null.
        store.save_proposal(proposal)

    now = _now()
    payment = PaymentRecord(
        id=f"pay_{uuid.uuid4().hex[:12]}",
        proposal_id=proposal.id,
        status=PaymentStatus.CREATED,
        amount_inr=proposal.total_inr,
        razorpay_order_id=f"order_mock_{uuid.uuid4().hex[:14]}",
        mock=True,
        payload={
            "note": "Mock Razorpay order. Real Razorpay wired later.",
            "idempotency_key": body.idempotency_key,
            "currency": "INR",
            "amount_paise": proposal.total_inr * 100,
            "baseline_total_inr": proposal.baseline_total_inr,
            "projected_uplift_inr": (
                proposal.growth_metrics.uplift_amount if proposal.growth_metrics else None
            ),
            "realized_paid_uplift": None,
        },
        created_at=now,
        updated_at=now,
    )
    store.save_payment(payment)
    return {
        "proposal": proposal,
        "payment": payment,
        "idempotent_replay": False,
        "growth_summary": {
            "baseline_total_inr": proposal.baseline_total_inr,
            "paid_total_inr": proposal.total_inr,
            "projected_uplift_inr": (
                proposal.growth_metrics.uplift_amount if proposal.growth_metrics else 0
            ),
            "realized_paid_uplift": None,
            "note": "realized_paid_uplift stays null until Razorpay payment succeeds",
        },
        "next_step": "Wire Razorpay Checkout in a later pointer using payment.razorpay_order_id",
    }


def cancel_proposal(proposal_id: str) -> Proposal:
    proposal = get_proposal(proposal_id)
    if proposal.status == ProposalStatus.PAID:
        raise HTTPException(status_code=409, detail="Paid proposals cannot be cancelled")
    return store.mark_proposal_status(proposal, ProposalStatus.CANCELLED)
