"""Deterministic checkout orchestration with guardrails + audit."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.agent.guardrails import (
    apply_cart_guardrails,
    assert_payment_confirmation_gate,
    payment_confirm_prompt,
)
from backend.audit.logger import log_event
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
from backend.services.cart import build_line_item
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
        log_event(
            "growth_offer_shown",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={
                "offer": result.offer.model_dump(),
                "baseline_total_inr": proposal.total_inr,
                "candidates_considered": result.metrics.candidates_considered,
            },
        )
    else:
        proposal.growth_offer = None
        proposal.status = ProposalStatus.AWAITING_CONFIRMATION
        log_event(
            "growth_offer_none",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={"skipped_reasons": result.skipped_reasons},
        )
    store.save_proposal(proposal)
    return proposal


def create_proposal(req: CreateProposalRequest) -> Proposal:
    policy = load_policy()
    ttl = int(policy.get("bounds", {}).get("proposal_ttl_seconds", 600))

    product_ids = list(req.product_ids)
    quantities = dict(req.quantities)
    reasons: dict[str, str] = {}
    protect: set[str] = set()

    if req.use_usual:
        usual = get_usual_order(req.user_id)
        if not usual.items:
            raise HTTPException(status_code=404, detail="No usual order found for user")
        for item in usual.items:
            if item.product_id not in product_ids:
                product_ids.append(item.product_id)
            quantities.setdefault(item.product_id, item.qty)
            reasons[item.product_id] = item.reason
            protect.add(item.product_id)

    guarded = apply_cart_guardrails(
        user_id=req.user_id,
        product_ids=product_ids,
        quantities=quantities,
        reasons=reasons,
        stated_budget_inr=req.stated_budget_inr,
        allow_substitute=req.allow_substitute,
        allow_trim=req.allow_trim,
        protect_product_ids=protect,
    )

    log_event(
        "guardrail_evaluated",
        user_id=req.user_id,
        session_id=req.session_id,
        payload={
            "product_ids": product_ids,
            "stated_budget_inr": req.stated_budget_inr,
            "allow_substitute": req.allow_substitute,
            "allow_trim": req.allow_trim,
            "actions": [
                {
                    "code": a.code,
                    "message": a.message,
                    "product_id": a.product_id,
                    "replaced_with": a.replaced_with,
                }
                for a in guarded.actions
            ],
            "blocking_issues": [i.model_dump() for i in guarded.blocking_issues],
            "cart_total_inr": guarded.cart.total_inr,
        },
    )

    if not guarded.ok:
        log_event(
            "proposal_rejected",
            user_id=req.user_id,
            session_id=req.session_id,
            payload={"issues": [i.model_dump() for i in guarded.blocking_issues]},
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Proposal failed guardrails",
                "issues": [i.model_dump() for i in guarded.blocking_issues],
                "actions": [
                    {"code": a.code, "message": a.message, "product_id": a.product_id}
                    for a in guarded.actions
                ],
            },
        )

    now = _now()
    proposal = Proposal(
        id=f"prop_{uuid.uuid4().hex[:12]}",
        user_id=req.user_id,
        session_id=req.session_id,
        status=ProposalStatus.AWAITING_CONFIRMATION,
        items=guarded.cart.items,
        total_inr=guarded.cart.total_inr,
        stated_budget_inr=req.stated_budget_inr,
        reasons=guarded.reasons or [i.reason for i in guarded.cart.items],
        issues=guarded.blocking_issues,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        baseline_total_inr=guarded.cart.total_inr,
    )
    store.save_proposal(proposal)
    log_event(
        "proposal_created",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={
            "total_inr": proposal.total_inr,
            "items": [i.model_dump() for i in proposal.items],
            "reasons": proposal.reasons,
            "expires_at": proposal.expires_at.isoformat(),
        },
    )

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
        log_event(
            "proposal_expired",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={},
        )
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

        metrics = GrowthMetrics(
            baseline_order_value=proposal.baseline_total_inr or proposal.total_inr
        )

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
        log_event(
            "addon_skipped",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={
                "product_id": offer.product_id,
                "total_inr": proposal.total_inr,
                "reason": "user declined add-on",
                "payment_confirm_prompt": payment_confirm_prompt(proposal),
            },
        )
        return proposal

    line, issue = build_line_item(
        offer.product_id,
        qty=1,
        reason=f"growth add-on: {offer.reason}",
    )
    if issue or line is None:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Add-on no longer eligible",
                "issue": issue.model_dump() if issue else None,
            },
        )

    new_items = list(proposal.items) + [line]
    new_cart = Cart(user_id=proposal.user_id, items=new_items)
    issues = validate_cart(new_cart, stated_budget_inr=proposal.stated_budget_inr)
    if issues:
        log_event(
            "addon_rejected_by_guardrail",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={"issues": [i.model_dump() for i in issues]},
        )
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
    log_event(
        "addon_accepted",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={
            "product_id": offer.product_id,
            "baseline_total_inr": proposal.baseline_total_inr,
            "new_total_inr": proposal.total_inr,
            "uplift_inr": offer.uplift_amount_inr,
            "note": "Add-on acceptance is NOT payment confirmation",
            "payment_confirm_prompt": payment_confirm_prompt(proposal),
        },
    )
    return proposal


def _growth_summary(proposal: Proposal) -> dict:
    return {
        "baseline_total_inr": proposal.baseline_total_inr,
        "paid_total_inr": proposal.total_inr,
        "projected_uplift_inr": (
            proposal.growth_metrics.uplift_amount if proposal.growth_metrics else 0
        ),
        "realized_paid_uplift": None,
        "note": "realized_paid_uplift stays null until Razorpay payment succeeds",
    }


def confirm_proposal(
    proposal_id: str,
    body: ConfirmationRequest,
) -> dict:
    proposal = get_proposal(proposal_id)

    if body.idempotency_key:
        existing = store.get_payment_by_idempotency(body.idempotency_key)
        if existing:
            prior = store.get_proposal(existing.proposal_id) or proposal
            log_event(
                "payment_idempotent_replay",
                user_id=proposal.user_id,
                session_id=proposal.session_id,
                proposal_id=proposal.id,
                payload={"idempotency_key": body.idempotency_key, "payment_id": existing.id},
            )
            return {
                "proposal": prior,
                "payment": existing,
                "idempotent_replay": True,
                "growth_summary": _growth_summary(prior),
            }

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
            "growth_summary": _growth_summary(proposal),
        }

    # Hard gate: exact proposal + total (+ optional user bind)
    assert_payment_confirmation_gate(
        proposal,
        expected_total_inr=body.expected_total_inr,
        user_id=body.user_id,
    )

    log_event(
        "payment_confirmation_received",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={
            "expected_total_inr": body.expected_total_inr,
            "proposal_total_inr": proposal.total_inr,
            "idempotency_key": body.idempotency_key,
        },
    )

    proposal = store.mark_proposal_status(proposal, ProposalStatus.CONFIRMED)
    proposal = store.mark_proposal_status(proposal, ProposalStatus.PAYMENT_PENDING)

    if proposal.growth_metrics and proposal.growth_metrics.recommendation_accepted:
        proposal.growth_metrics.accepted_order_value = proposal.total_inr
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
    log_event(
        "payment_order_created",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={
            "payment_id": payment.id,
            "amount_inr": payment.amount_inr,
            "razorpay_order_id": payment.razorpay_order_id,
            "mock": True,
        },
    )
    return {
        "proposal": proposal,
        "payment": payment,
        "idempotent_replay": False,
        "growth_summary": _growth_summary(proposal),
        "next_step": "Wire Razorpay Checkout in a later pointer using payment.razorpay_order_id",
    }


def cancel_proposal(proposal_id: str) -> Proposal:
    proposal = get_proposal(proposal_id)
    if proposal.status == ProposalStatus.PAID:
        raise HTTPException(status_code=409, detail="Paid proposals cannot be cancelled")
    proposal = store.mark_proposal_status(proposal, ProposalStatus.CANCELLED)
    log_event(
        "proposal_cancelled",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={},
    )
    return proposal
