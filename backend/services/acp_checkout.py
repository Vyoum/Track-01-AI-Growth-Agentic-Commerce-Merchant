"""ACP-shaped checkout sessions — adapter over proposal-confirm-v1.

Not OpenAI Instant Checkout partnership. Same money core: catalog reprice,
gates, mandate CAS, Razorpay test/mock. Delegated complete accepts demo SPT
tokens and settles via existing confirm+verify. Live Checkout.js is returned
as requires_payment_action instead of faking Stripe Shared Payment Tokens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from backend.audit.logger import log_event
from backend.db import connect
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    MerchantApprovalRequest,
    Proposal,
    ProposalStatus,
    VerifyPaymentRequest,
)
from backend.services import checkout, store

ACP_API_VERSION = "2026-04-17"
DEMO_SPT_PREFIXES = ("spt_demo", "spt_mock", "tok_demo")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paise(inr: int) -> int:
    return int(inr) * 100


def _session_status(proposal: Proposal) -> str:
    if proposal.status == ProposalStatus.PAID:
        return "completed"
    if proposal.status in {ProposalStatus.CANCELLED, ProposalStatus.FAILED, ProposalStatus.EXPIRED}:
        return "canceled" if proposal.status == ProposalStatus.CANCELLED else "failed"
    if proposal.status == ProposalStatus.PAYMENT_PENDING:
        return "requires_payment_action"
    if proposal.status == ProposalStatus.AWAITING_CONFIRMATION:
        return "ready_for_complete"
    if proposal.status in {
        ProposalStatus.AWAITING_ADDON_DECISION,
        ProposalStatus.AWAITING_MERCHANT_APPROVAL,
    }:
        return "needs_input"
    return "open"


def _messages(proposal: Proposal) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if proposal.status == ProposalStatus.AWAITING_MERCHANT_APPROVAL:
        messages.append(
            {
                "type": "error",
                "code": "merchant_approval_required",
                "content": "Growth template is paused or pending. POST update with buyer_actions.campaign=approve or enable the template.",
            }
        )
    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION and proposal.growth_offer:
        offer = proposal.growth_offer
        messages.append(
            {
                "type": "info",
                "code": "addon_decision_required",
                "content": offer.offer_text,
                "addon": {
                    "product_id": offer.product_id,
                    "name": offer.name,
                    "price_inr": offer.price_inr,
                    "projected_total_inr": offer.projected_total_inr,
                },
            }
        )
    return messages


def serialize_session(
    *,
    checkout_session_id: str,
    proposal: Proposal,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payment = store.get_payment_by_proposal(proposal.id)
    body: dict[str, Any] = {
        "id": checkout_session_id,
        "object": "checkout_session",
        "api_version": ACP_API_VERSION,
        "status": _session_status(proposal),
        "currency": "inr",
        "proposal_id": proposal.id,
        "buyer": {"id": proposal.user_id, "type": proposal.buyer_type},
        "line_items": [
            {
                "id": item.product_id,
                "name": item.name,
                "quantity": item.qty,
                "unit_amount": _paise(item.unit_price_inr),
                "amount": _paise(item.line_total_inr),
            }
            for item in proposal.items
        ],
        "totals": {
            "total": _paise(proposal.total_inr),
            "total_inr": proposal.total_inr,
        },
        "messages": _messages(proposal),
        "payment": {
            "handler": "razorpay_test_or_mock",
            "accepted_instrument_types": ["spt_demo"],
            "note": (
                "Demo SPT tokens complete via confirm+signature-verify. "
                "This is not Stripe Shared Payment Token / ChatGPT Instant Checkout."
            ),
        },
        "links": {
            "proposal": f"/api/proposals/{proposal.id}",
            "audit": f"/api/proposals/{proposal.id}/audit",
        },
    }
    if payment:
        body["order"] = {
            "id": proposal.id,
            "payment_id": payment.id,
            "razorpay_order_id": payment.razorpay_order_id,
            "mock": payment.mock,
            "payment_status": payment.status.value
            if hasattr(payment.status, "value")
            else str(payment.status),
        }
        if payment.status.value != "paid" and not payment.mock:
            body["payment"]["checkout"] = checkout._checkout_payload(payment, proposal)
    if extra:
        body.update(extra)
    return body


def _put_session(checkout_session_id: str, proposal_id: str, idempotency_key: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO acp_sessions (
                checkout_session_id, proposal_id, idempotency_key, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (checkout_session_id, proposal_id, idempotency_key, _now()),
        )
        conn.commit()


def _get_by_idempotency(idempotency_key: str | None) -> tuple[str, str] | None:
    if not idempotency_key:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT checkout_session_id, proposal_id FROM acp_sessions
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
    if not row:
        return None
    return str(row["checkout_session_id"]), str(row["proposal_id"])


def _get_mapping(checkout_session_id: str) -> tuple[str, str]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT checkout_session_id, proposal_id FROM acp_sessions
            WHERE checkout_session_id = ?
            """,
            (checkout_session_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Unknown checkout session"})
    return str(row["checkout_session_id"]), str(row["proposal_id"])


def _load(checkout_session_id: str) -> tuple[str, Proposal]:
    sid, pid = _get_mapping(checkout_session_id)
    proposal = store.get_proposal(pid)
    if proposal is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Proposal missing"})
    return sid, proposal


def create_session(body: dict[str, Any], *, idempotency_key: str | None) -> dict[str, Any]:
    existing = _get_by_idempotency(idempotency_key or None)
    if existing:
        sid, pid = existing
        proposal = store.get_proposal(pid)
        if proposal:
            return serialize_session(checkout_session_id=sid, proposal=proposal)

    line_items = body.get("line_items") or []
    product_ids: list[str] = []
    quantities: dict[str, int] = {}
    for item in line_items:
        sku = str(item.get("id") or item.get("product_id") or "").strip()
        if not sku:
            continue
        qty = int(item.get("quantity") or 1)
        product_ids.append(sku)
        quantities[sku] = qty
    if not product_ids:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid", "message": "line_items with product id required"},
        )

    buyer = body.get("buyer") or {}
    user_id = str(buyer.get("id") or body.get("user_id") or "external_buyer_01")
    budget = body.get("stated_budget_inr")
    if budget is None:
        budget = 800
    session_id = str(body.get("session_id") or f"acp_{uuid.uuid4().hex[:12]}")

    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            product_ids=product_ids,
            quantities=quantities,
            stated_budget_inr=int(budget),
            with_growth=bool(body.get("with_growth", True)),
            session_id=session_id,
            buyer_type="external_agent",
        )
    )
    checkout_session_id = f"cs_{uuid.uuid4().hex[:16]}"
    _put_session(checkout_session_id, proposal.id, idempotency_key or None)
    log_event(
        "acp_checkout_session_created",
        user_id=user_id,
        session_id=session_id,
        proposal_id=proposal.id,
        payload={"checkout_session_id": checkout_session_id},
    )
    return serialize_session(checkout_session_id=checkout_session_id, proposal=proposal)


def get_session(checkout_session_id: str) -> dict[str, Any]:
    sid, proposal = _load(checkout_session_id)
    return serialize_session(checkout_session_id=sid, proposal=proposal)


def update_session(checkout_session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    sid, proposal = _load(checkout_session_id)
    actions = body.get("buyer_actions") or {}
    campaign = str(actions.get("campaign") or "").strip().lower()
    addon = str(actions.get("addon") or body.get("addon") or "").strip().lower()

    if campaign in {"approve", "reject"} and proposal.status == ProposalStatus.AWAITING_MERCHANT_APPROVAL:
        proposal = checkout.decide_merchant_campaign(
            proposal.id,
            MerchantApprovalRequest(decision=campaign, note="ACP buyer_actions.campaign"),
        )

    if addon in {"accept", "skip"} and proposal.status == ProposalStatus.AWAITING_ADDON_DECISION:
        product_id = None
        if addon == "accept" and proposal.growth_offer:
            product_id = proposal.growth_offer.product_id
        proposal = checkout.decide_addon(
            proposal.id,
            AddonDecisionRequest(decision=addon, product_id=product_id),
        )

    log_event(
        "acp_checkout_session_updated",
        user_id=proposal.user_id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        payload={"checkout_session_id": sid, "buyer_actions": actions},
    )
    return serialize_session(checkout_session_id=sid, proposal=proposal)


def _demo_token_ok(payment_data: dict[str, Any] | None) -> bool:
    if not payment_data:
        return False
    instrument = payment_data.get("instrument") or {}
    token = str(
        instrument.get("token")
        or payment_data.get("token")
        or instrument.get("credential")
        or ""
    )
    type_ = str(instrument.get("type") or payment_data.get("type") or "")
    if type_ in {"spt", "spt_demo", "demo"} and token.startswith(DEMO_SPT_PREFIXES):
        return True
    if token.startswith(DEMO_SPT_PREFIXES):
        return True
    return False


def complete_session(checkout_session_id: str, body: dict[str, Any], *, idempotency_key: str | None) -> dict[str, Any]:
    sid, proposal = _load(checkout_session_id)
    if proposal.status == ProposalStatus.PAID:
        return serialize_session(checkout_session_id=sid, proposal=proposal)

    if proposal.status != ProposalStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_ready_for_complete",
                "message": f"Session status is {proposal.status.value}; resolve messages via POST update first",
                "checkout_session": serialize_session(checkout_session_id=sid, proposal=proposal),
            },
        )

    if not _demo_token_ok(body.get("payment_data")):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid",
                "message": (
                    "Delegated complete expects payment_data.instrument "
                    "{type: spt_demo, token: spt_demo_...}. "
                    "Live Stripe SPT is not wired; Razorpay Checkout.js is not ACP complete."
                ),
            },
        )

    ack = bool(body.get("acknowledge_impulse_warning"))
    try:
        confirmed = checkout.confirm_proposal(
            proposal.id,
            ConfirmationRequest(
                expected_total_inr=proposal.total_inr,
                user_id=proposal.user_id,
                idempotency_key=idempotency_key or f"acp_{sid}",
                acknowledge_impulse_warning=ack,
            ),
            force_mock=False,
            mock_order=True,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        if detail.get("code") == "MANDATE_CAP_WARNING":
            raise HTTPException(
                status_code=409,
                detail={
                    **detail,
                    "checkout_session_id": sid,
                    "message": detail.get("message"),
                    "retry": "POST complete again with acknowledge_impulse_warning=true",
                },
            ) from exc
        raise

    payment = confirmed["payment"]
    extra: dict[str, Any] = {}
    if payment.mock:
        verified = checkout.verify_payment(
            VerifyPaymentRequest(
                payment_id=payment.id,
                razorpay_order_id=payment.razorpay_order_id or "",
                razorpay_payment_id=f"pay_acp_{uuid.uuid4().hex[:8]}",
                razorpay_signature="mock_ok_acp",
            )
        )
        proposal = verified["proposal"]
        log_event(
            "acp_checkout_completed",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={"checkout_session_id": sid, "mock": True},
        )
    else:
        proposal = confirmed["proposal"]
        extra["payment_action"] = {
            "type": "razorpay_checkout_js",
            "checkout": confirmed.get("checkout"),
            "note": "ACP spt_demo uses mock settle; live Checkout.js is the human path.",
        }
        log_event(
            "acp_checkout_requires_payment_action",
            user_id=proposal.user_id,
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            payload={"checkout_session_id": sid},
        )

    return serialize_session(checkout_session_id=sid, proposal=proposal, extra=extra)


def cancel_session(checkout_session_id: str) -> dict[str, Any]:
    sid, proposal = _load(checkout_session_id)
    cancelled = checkout.cancel_proposal(proposal.id)
    log_event(
        "acp_checkout_canceled",
        user_id=cancelled.user_id,
        session_id=cancelled.session_id,
        proposal_id=cancelled.id,
        payload={"checkout_session_id": sid},
    )
    return serialize_session(checkout_session_id=sid, proposal=cancelled)
