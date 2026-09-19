"""Public A2A commerce manifest — machine-readable discovery (no auth)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.services.data_loader import load_campaigns, load_policy

router = APIRouter(tags=["well-known"])


@router.get("/.well-known/agent-catalog.json")
def agent_catalog_manifest() -> JSONResponse:
    """Robots.txt-style manifest for autonomous buyer agents."""
    settings = get_settings()
    policy = load_policy()
    campaigns_cfg = load_campaigns()
    bounds = policy.get("bounds", {})
    gates = policy.get("gates", {})
    campaign_guardrails = campaigns_cfg.get("campaign_guardrails", {})
    mandates = policy.get("spend_mandates") or {}

    return JSONResponse(
        {
            "merchant": settings.merchant_name,
            "catalog_url": "/api/products",
            "checkout_protocol": "proposal-confirm-v1",
            "currency": "INR",
            "catalog_source": settings.resolved_store_provider,
            "endpoints": {
                "products": "GET /api/products?q=&category=",
                "product": "GET /api/products/{id}",
                "usual_order": "GET /api/users/{user_id}/usual",
                "create_proposal": "POST /api/proposals",
                "get_proposal": "GET /api/proposals/{id}",
                "merchant_campaign_decide": "POST /api/proposals/{id}/campaign/decide",
                "addon_decide": "POST /api/proposals/{id}/growth/decide",
                "addon_decide_alias": "POST /api/proposals/{id}/addon",
                "confirm": "POST /api/proposals/{id}/confirm",
                "verify_payment": "POST /api/payments/verify",
                "proposal_audit": "GET /api/proposals/{id}/audit",
            },
            "policies": {
                "requires_explicit_confirmation": gates.get(
                    "require_explicit_payment_confirmation", True
                ),
                "requires_addon_decision_before_payment": gates.get(
                    "require_addon_decision_before_payment", True
                ),
                "requires_merchant_campaign_approval": False,
                "requires_enabled_growth_template": campaign_guardrails.get(
                    "require_template_enabled", True
                ),
                "max_order_value_inr": int(bounds.get("hard_max_order_value_inr", 5000)),
                "max_items_per_proposal": int(bounds.get("max_items_per_proposal", 5)),
                "supports_growth_offers": True,
                "razorpay_mode": settings.razorpay_mode,
                "spend_mandates": {
                    "session_cap_inr": int(mandates.get("session_cap_inr", 2000)),
                    "day_cap_inr": int(mandates.get("day_cap_inr", 3000)),
                    "month_cap_inr": int(mandates.get("month_cap_inr", 7000)),
                    "bypass_allowed": True,
                    "acknowledge_field": "acknowledge_impulse_warning",
                    "purpose": "Reduce impulsive repeat shopping; warning is not a hard block.",
                },
            },
            "acp": {
                "discovery": "GET /.well-known/acp.json",
                "checkout_sessions": "POST /checkout_sessions",
                "api_version": "2026-04-17",
                "instant_checkout_partner": False,
            },
            "buyer_agent_hints": {
                "identify_as": "Set buyer_type=external_agent on POST /api/proposals, or use ACP checkout_sessions",
                "session_prefix": "a2a_",
                "preferred_protocol": "acp_adapter",
                "legacy_protocol": "proposal-confirm-v1",
                "no_special_routes": True,
            },
        },
        headers={"Cache-Control": "public, max-age=300"},
    )
