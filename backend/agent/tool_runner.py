"""Execute agent tools — all money paths stay in checkout services."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from backend.models import CreateProposalRequest
from backend.services import catalog, checkout, history


def run_tool(name: str, arguments: dict[str, Any], *, session_id: str | None) -> dict[str, Any]:
    try:
        if name == "search_products":
            products = catalog.search_products(
                query=arguments.get("query", ""),
                category=arguments.get("category"),
            )
            return {
                "count": len(products),
                "products": [p.model_dump() for p in products[:10]],
            }

        if name == "get_usual_order":
            user_id = arguments["user_id"]
            usual = history.get_usual_order(user_id)
            return usual.model_dump()

        if name == "create_proposal_from_usual":
            req = CreateProposalRequest(
                user_id=arguments["user_id"],
                use_usual=True,
                stated_budget_inr=int(arguments["stated_budget_inr"]),
                with_growth=arguments.get("with_growth", True),
                session_id=session_id,
            )
            proposal = checkout.create_proposal(req)
            return _proposal_tool_result(proposal)

        if name == "create_proposal_from_products":
            req = CreateProposalRequest(
                user_id=arguments["user_id"],
                product_ids=list(arguments["product_ids"]),
                stated_budget_inr=int(arguments["stated_budget_inr"]),
                with_growth=arguments.get("with_growth", True),
                session_id=session_id,
            )
            proposal = checkout.create_proposal(req)
            return _proposal_tool_result(proposal)

        if name == "get_proposal_status":
            proposal = checkout.get_proposal(arguments["proposal_id"])
            return _proposal_tool_result(proposal)

        return {"error": f"Unknown tool: {name}"}
    except HTTPException as exc:
        detail = exc.detail
        return {"error": detail if isinstance(detail, str) else json.dumps(detail)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _proposal_tool_result(proposal) -> dict[str, Any]:
    out = {
        "proposal_id": proposal.id,
        "status": proposal.status.value,
        "total_inr": proposal.total_inr,
        "baseline_total_inr": proposal.baseline_total_inr,
        "stated_budget_inr": proposal.stated_budget_inr,
        "items": [i.model_dump() for i in proposal.items],
        "line_summary": proposal.line_summary,
        "proposal_source": proposal.proposal_source,
        "source_reason": proposal.source_reason,
    }
    if proposal.campaign_decision:
        cd = proposal.campaign_decision
        out["campaign"] = {
            "campaign_id": cd.campaign_id,
            "opportunity": cd.opportunity,
            "target_segment": cd.target_segment,
            "merchant_approval_status": cd.merchant_approval_status,
            "copy_key": cd.copy_key,
        }
    if proposal.status.value == "awaiting_merchant_approval":
        out["next_step"] = (
            "A growth template is waiting on the merchant desk. Tell the user to "
            "open Merchant and enable the template. Do NOT ask accept/skip. "
            "They may still confirm the baseline cart after the store enables it "
            "on a new proposal."
        )
    elif (
        proposal.campaign_decision
        and proposal.campaign_decision.merchant_approval_status == "paused"
    ):
        out["next_step"] = (
            "The matching add-on template is paused. Tell the user: optional add-on "
            "is waiting for the store to enable it on Merchant. Do NOT present the "
            "add-on SKU. They can still confirm payment for the baseline cart."
        )
    elif proposal.growth_offer and proposal.status.value == "awaiting_addon_decision":
        out["growth_offer"] = {
            "product_id": proposal.growth_offer.product_id,
            "name": proposal.growth_offer.name,
            "price_inr": proposal.growth_offer.price_inr,
            "projected_total_inr": proposal.growth_offer.projected_total_inr,
            "reason": proposal.growth_offer.reason,
            "offer_text": proposal.growth_offer.offer_text,
        }
        out["next_step"] = (
            "Ask user to accept or skip the add-on. Do NOT confirm payment yet."
        )
    elif proposal.status.value == "awaiting_confirmation":
        out["next_step"] = (
            "Tell user the total and ask them to say 'confirm payment' when ready. "
            "Do NOT claim payment is complete."
        )
    return out
