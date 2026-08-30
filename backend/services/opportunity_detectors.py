"""Deterministic opportunity detectors for the campaign orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.models import Cart
from backend.services.catalog import get_product
from backend.services.growth import GrowthDecisionResult


@dataclass
class OpportunitySignal:
    id: str
    label: str
    priority: int
    data: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


def resolve_segment(*, has_order_history: bool, stated_budget_inr: int | None) -> str:
    """Known segments only — never open-ended LLM labels."""
    if not has_order_history:
        return "new_user_no_history"
    # Stated budget alone does not override returning history; budget_shopper
    # is reserved for campaigns that explicitly target budget-led carts.
    if stated_budget_inr is not None and not has_order_history:
        return "budget_shopper"
    return "returning_with_history"


def detect_opportunities(
    *,
    cart: Cart,
    stated_budget_inr: int | None,
    proposal_source: str,
    growth: GrowthDecisionResult,
    has_order_history: bool,
) -> list[OpportunitySignal]:
    """Return ranked opportunity signals. Does not invent offers."""
    signals: list[OpportunitySignal] = []
    baseline = cart.total_inr
    remaining = (
        stated_budget_inr - baseline if stated_budget_inr is not None else None
    )

    if remaining is not None and remaining > 0:
        signals.append(
            OpportunitySignal(
                id="budget_headroom",
                label="Budget headroom",
                priority=10,
                data={
                    "baseline_inr": baseline,
                    "stated_budget_inr": stated_budget_inr,
                    "remaining_inr": remaining,
                },
                rationale=f"₹{remaining} remaining under stated budget ₹{stated_budget_inr}",
            )
        )

    if growth.has_relationship_data and growth.growth_method == "catalog_complements":
        signals.append(
            OpportunitySignal(
                id="complement_attach",
                label="Complement attach",
                priority=5,
                data={"growth_method": growth.growth_method},
                rationale="Cart products have complement relationship data",
            )
        )

    if not has_order_history or proposal_source == "bestsellers":
        signals.append(
            OpportunitySignal(
                id="new_user_bestseller",
                label="New user / bestseller path",
                priority=15,
                data={"proposal_source": proposal_source},
                rationale="No completed order history (or bestseller fallback cart)",
            )
        )

    if growth.offer and growth.has_relationship_data:
        offer_product = get_product(growth.offer.product_id)
        cart_categories = {
            get_product(i.product_id).category
            for i in cart.items
            if get_product(i.product_id)
        }
        if (
            offer_product
            and offer_product.category
            and offer_product.category not in cart_categories
        ):
            signals.append(
                OpportunitySignal(
                    id="category_cross_sell",
                    label="Category cross-sell",
                    priority=20,
                    data={
                        "offer_category": offer_product.category,
                        "cart_categories": sorted(cart_categories),
                    },
                    rationale=(
                        f"Add-on category {offer_product.category} "
                        f"differs from cart categories"
                    ),
                )
            )

    signals.sort(key=lambda s: s.priority)
    return signals
