"""Deterministic campaign orchestrator — picks from campaigns.json only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.models import (
    CampaignDecision,
    CampaignOfferSnapshot,
    Cart,
    GrowthOffer,
)
from backend.services.campaign_guardrails import apply_campaign_guardrails
from backend.services.catalog import get_product
from backend.services.data_loader import load_campaigns
from backend.services.growth import GrowthDecisionResult, recommend_addon
from backend.services.opportunity_detectors import (
    OpportunitySignal,
    detect_opportunities,
    resolve_segment,
)


@dataclass
class CampaignOrchestrationResult:
    growth: GrowthDecisionResult
    decision: CampaignDecision | None
    opportunities: list[OpportunitySignal]
    skipped_reasons: list[str]


def _render_customer_copy(
    *,
    copy_key: str,
    offer: GrowthOffer,
    stated_budget_inr: int | None,
    baseline_inr: int,
) -> str:
    cfg = load_campaigns()
    templates = cfg.get("copy_templates", {})
    template = templates.get(copy_key) or templates.get("complement_addon") or (
        "I also found {addon_name} for ₹{addon_price}. "
        "Total would be ₹{projected_total}. Want me to add it?"
    )
    remaining = (
        stated_budget_inr - baseline_inr if stated_budget_inr is not None else None
    )
    return template.format(
        addon_name=offer.name,
        addon_price=offer.price_inr,
        addon_reason=offer.reason,
        projected_total=offer.projected_total_inr,
        budget=stated_budget_inr if stated_budget_inr is not None else "your",
        remaining=remaining if remaining is not None else "—",
        baseline_total=baseline_inr,
    )


def _match_campaign(
    *,
    opportunities: list[OpportunitySignal],
    growth: GrowthDecisionResult,
    segment: str,
) -> tuple[dict[str, Any] | None, OpportunitySignal | None]:
    """Pick highest-priority (lowest number) enabled campaign matching opportunity + strategy."""
    cfg = load_campaigns()
    campaigns = [c for c in cfg.get("campaigns", []) if c.get("enabled", True)]
    method = growth.growth_method or ""

    opportunity_ids = {o.id for o in opportunities}
    ranked: list[tuple[int, dict[str, Any], OpportunitySignal]] = []

    for camp in campaigns:
        opp_id = camp.get("opportunity")
        strategy = camp.get("strategy")
        if opp_id not in opportunity_ids:
            continue
        if strategy and method and strategy != method:
            # Allow popular_budget_fit campaigns when method is popular_budget_fit
            # and complement campaigns when method is catalog_complements
            continue
        # Prefer segment match; allow if campaign target_segment equals resolved segment
        target = camp.get("target_segment")
        if target and target != segment:
            # Soft miss: still allow but deprioritize
            priority = int(camp.get("priority", 99)) + 50
        else:
            priority = int(camp.get("priority", 99))

        signal = next(o for o in opportunities if o.id == opp_id)
        ranked.append((priority, camp, signal))

    if not ranked:
        return None, None

    ranked.sort(key=lambda x: x[0])
    _, camp, signal = ranked[0]
    return camp, signal


def orchestrate_campaign(
    cart: Cart,
    stated_budget_inr: int | None = None,
    rejected_addon_ids: list[str] | None = None,
    user_id: str | None = None,
    *,
    proposal_source: str = "completed_order_history",
    source_reason: str | None = None,
    has_order_history: bool | None = None,
) -> CampaignOrchestrationResult:
    """
    Run growth ranker → detect opportunities → match campaigns.json → guardrails.
    Never invents SKU/discount/segment.
    """
    growth = recommend_addon(
        cart=cart,
        stated_budget_inr=stated_budget_inr,
        rejected_addon_ids=rejected_addon_ids,
        user_id=user_id,
        proposal_source=proposal_source,
        source_reason=source_reason,
    )
    skipped: list[str] = list(growth.skipped_reasons)

    history_flag = (
        has_order_history
        if has_order_history is not None
        else proposal_source == "completed_order_history"
    )
    opportunities = detect_opportunities(
        cart=cart,
        stated_budget_inr=stated_budget_inr,
        proposal_source=proposal_source,
        growth=growth,
        has_order_history=history_flag,
    )

    if not growth.offer:
        skipped.append("no growth offer — no campaign")
        return CampaignOrchestrationResult(growth, None, opportunities, skipped)

    segment = resolve_segment(
        has_order_history=history_flag,
        stated_budget_inr=stated_budget_inr,
    )
    camp, signal = _match_campaign(
        opportunities=opportunities,
        growth=growth,
        segment=segment,
    )

    if camp is None or signal is None:
        # Fallback: bind to first opportunity + synthetic campaign id from strategy
        if not opportunities:
            skipped.append("no opportunity signals for offer")
            return CampaignOrchestrationResult(growth, None, opportunities, skipped)
        signal = opportunities[0]
        camp = {
            "id": f"adhoc_{growth.growth_method or 'growth'}",
            "name": "Ad-hoc growth campaign",
            "target_segment": segment,
            "copy_key": "complement_addon",
            "copy_variants": ["complement_addon"],
            "max_discount_pct": 0,
            "allowed_categories": [],
        }
        skipped.append("no campaigns.json match — using ad-hoc wrapper (still needs merchant approval)")

    offer = growth.offer
    product = get_product(offer.product_id)
    guard = apply_campaign_guardrails(
        campaign=camp,
        offer=offer,
        target_segment=camp.get("target_segment") or segment,
        discount_pct=0.0,
    )
    if not guard.ok:
        skipped.extend(guard.notes)
        return CampaignOrchestrationResult(growth, None, opportunities, skipped)

    copy_key = str(camp.get("copy_key") or "complement_addon")
    copy_variants = list(camp.get("copy_variants") or [copy_key])
    # Narrow LLM role reserved: pick among pre-approved variants (deterministic first).
    chosen_copy_key = copy_key if copy_key in copy_variants else copy_variants[0]

    customer_copy = _render_customer_copy(
        copy_key=chosen_copy_key,
        offer=offer,
        stated_budget_inr=stated_budget_inr,
        baseline_inr=cart.total_inr,
    )
    # Keep growth offer_text aligned with campaign copy for the agent/UI.
    offer.offer_text = customer_copy

    rationale = list(growth.selection_rationale or [])
    if signal.rationale:
        rationale.insert(0, signal.rationale)
    rationale.extend(guard.notes)

    decision = CampaignDecision(
        opportunity=signal.id,
        campaign_id=str(camp["id"]),
        campaign_name=str(camp.get("name") or camp["id"]),
        target_segment=str(camp.get("target_segment") or segment),
        offer=CampaignOfferSnapshot(
            product_id=offer.product_id,
            name=offer.name,
            price_inr=offer.price_inr,
            reason=offer.reason,
            source=offer.source,
            projected_total_inr=offer.projected_total_inr,
            uplift_amount_inr=offer.uplift_amount_inr,
            uplift_percent=offer.uplift_percent,
            category=product.category if product else None,
        ),
        rationale=rationale,
        copy_key=chosen_copy_key,
        copy_variants=copy_variants,
        customer_copy=customer_copy,
        discount_pct=0.0,
        merchant_approval_status="pending",
        guardrail_passed=True,
        guardrail_notes=guard.notes,
    )
    return CampaignOrchestrationResult(growth, decision, opportunities, skipped)
