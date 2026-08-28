"""Deterministic bestseller proposal seed for users without completed orders."""

from __future__ import annotations

from backend.models import LineItem, UsualOrderResponse
from backend.services import catalog
from backend.services.data_loader import load_policy


BESTSELLER_REASON = (
    "you don't have any past completed orders yet — here are our most popular picks"
)
USUAL_UNAVAILABLE_REASON = (
    "your usual item is currently unavailable — here's what's popular instead"
)


def get_bestseller_seed(
    user_id: str,
    *,
    stated_budget_inr: int | None,
    reason: str = BESTSELLER_REASON,
    exclude_product_ids: set[str] | None = None,
) -> UsualOrderResponse:
    """Choose explicitly ranked bestsellers without representing them as history."""
    policy = load_policy()
    config = policy.get("bestsellers", {})
    max_items = max(1, int(config.get("max_items", 2)))
    hard_max = int(policy.get("bounds", {}).get("hard_max_order_value_inr", 5000))
    budget = stated_budget_inr if stated_budget_inr is not None else hard_max
    excluded = exclude_product_ids or set()
    configured_rank = list(config.get("fallback_ranked_product_ids", []))
    configured_position = {
        product_id: position for position, product_id in enumerate(configured_rank)
    }

    products = [
        product
        for product in catalog.list_products()
        if product.stock > 0 and product.id not in excluded
    ]
    marked = [
        product
        for product in products
        if product.is_bestseller
        or product.bestseller_rank is not None
        or product.sales_count > 0
        or product.id in configured_position
    ]

    ranked = sorted(
        marked,
        key=lambda product: (
            0 if product.is_bestseller else 1,
            configured_position.get(product.id, 1_000_000),
            product.bestseller_rank
            if product.bestseller_rank is not None
            else 1_000_000,
            -product.sales_count,
            -product.review_count,
            -product.rating,
            product.id,
        ),
    )

    selected: list[LineItem] = []
    running_total = 0
    for product in ranked:
        if len(selected) >= max_items:
            break
        if running_total + product.price_inr > budget:
            continue
        selected.append(
            LineItem(
                product_id=product.id,
                name=product.name,
                qty=1,
                unit_price_inr=product.price_inr,
                line_total_inr=product.price_inr,
                reason=reason,
            )
        )
        running_total += product.price_inr

    return UsualOrderResponse(
        user_id=user_id,
        order_id=None,
        items=selected,
        total_inr=running_total,
        source="bestsellers",
    )
