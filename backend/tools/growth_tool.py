"""Growth / complementary recommendation tool."""

from __future__ import annotations

from backend.models import Cart, LineItem
from backend.services.growth import recommend_addon


def recommend_addon_for_items(
    user_id: str,
    items: list[LineItem],
    stated_budget_inr: int | None = None,
    rejected_addon_ids: list[str] | None = None,
):
    cart = Cart(user_id=user_id, items=items)
    return recommend_addon(
        cart=cart,
        stated_budget_inr=stated_budget_inr,
        rejected_addon_ids=rejected_addon_ids,
        user_id=user_id,
    )
