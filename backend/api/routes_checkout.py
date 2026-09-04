"""HTTP routes for deterministic checkout core."""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    FailPaymentRequest,
    MerchantApprovalRequest,
    CampaignPolicyRequest,
    SearchProductsResponse,
    VerifyPaymentRequest,
)
from backend.audit.logger import list_events
from backend.services import catalog, checkout, history

router = APIRouter(prefix="/api", tags=["checkout"])


@router.get("/products", response_model=SearchProductsResponse)
def api_search_products(
    q: str = Query(default=""),
    category: str | None = None,
):
    products = catalog.search_products(query=q, category=category)
    return SearchProductsResponse(products=products, count=len(products))


@router.get("/products/{product_id}")
def api_get_product(product_id: str):
    product = catalog.get_product(product_id)
    if not product:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/users/{user_id}/usual")
def api_usual_order(user_id: str):
    return history.get_usual_order(user_id)


@router.post("/proposals")
def api_create_proposal(body: CreateProposalRequest):
    return checkout.create_proposal(body)


@router.get("/proposals/{proposal_id}")
def api_get_proposal(proposal_id: str):
    return checkout.get_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/growth/decide")
def api_decide_addon(proposal_id: str, body: AddonDecisionRequest):
    return checkout.decide_addon(proposal_id, body)


@router.post("/proposals/{proposal_id}/addon")
def api_decide_addon_alias(proposal_id: str, body: AddonDecisionRequest):
    """Alias for A2A manifest compatibility — same handler as growth/decide."""
    return checkout.decide_addon(proposal_id, body)


@router.get("/merchant/campaigns/pending")
def api_pending_campaigns(limit: int = Query(default=20, ge=1, le=100)):
    proposals = checkout.list_pending_merchant_approvals(limit=limit)
    return {
        "count": len(proposals),
        "proposals": [p.model_dump(mode="json") for p in proposals],
    }


@router.get("/merchant/campaigns/catalog")
def api_campaign_catalog():
    return checkout.merchant_campaign_catalog()


@router.post("/merchant/campaigns/{campaign_id}/policy")
def api_campaign_policy(campaign_id: str, body: CampaignPolicyRequest):
    return checkout.set_campaign_template_policy(campaign_id, body)


@router.post("/proposals/{proposal_id}/campaign/decide")
def api_decide_campaign(proposal_id: str, body: MerchantApprovalRequest):
    return checkout.decide_merchant_campaign(proposal_id, body)


@router.post("/proposals/{proposal_id}/confirm")
def api_confirm_proposal(proposal_id: str, body: ConfirmationRequest):
    return checkout.confirm_proposal(proposal_id, body)


@router.post("/proposals/{proposal_id}/cancel")
def api_cancel_proposal(proposal_id: str):
    return checkout.cancel_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/payment/fail")
def api_fail_payment(proposal_id: str, body: FailPaymentRequest):
    return checkout.fail_payment(proposal_id, body)


@router.post("/payments/verify")
def api_verify_payment(body: VerifyPaymentRequest):
    return checkout.verify_payment(body)


@router.get("/proposals/{proposal_id}/audit")
def api_proposal_audit(proposal_id: str, limit: int = Query(default=100, ge=1, le=500)):
    events = list_events(proposal_id=proposal_id, limit=limit)
    trace_event = next(
        (e for e in reversed(events) if e["event_type"] == "decision_trace"),
        None,
    )
    gate_events = [e for e in events if e["event_type"] == "gate_trace"]
    return {
        "proposal_id": proposal_id,
        "events": events,
        "decision_trace": trace_event["payload"] if trace_event else None,
        "gate_traces": [e["payload"] for e in gate_events],
        "checks": trace_event["payload"].get("checks") if trace_event else None,
        "checks_summary": trace_event["payload"].get("summary") if trace_event else None,
    }


@router.get("/audit")
def api_audit(
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "events": list_events(session_id=session_id, user_id=user_id, limit=limit),
    }


@router.get("/a2a/summary")
def api_a2a_summary(limit: int = Query(default=100, ge=1, le=500)):
    """Count autonomous buyer-agent purchases from audit trail."""
    events = list_events(limit=limit)
    completed = [
        e for e in events if e.get("event_type") == "a2a_purchase_completed"
    ]
    total_uplift = sum(int(e.get("payload", {}).get("uplift_inr") or 0) for e in completed)
    return {
        "external_agent_purchases": len(completed),
        "total_uplift_inr": total_uplift,
        "recent": completed[-5:],
    }
