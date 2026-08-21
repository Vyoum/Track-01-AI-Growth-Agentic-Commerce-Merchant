"""User order history — resolves 'the usual' from demo data."""

from __future__ import annotations

from backend.models import LineItem, UsualOrderResponse
from backend.services.catalog import get_product
from backend.services.data_loader import load_demo_users


def get_usual_order(user_id: str) -> UsualOrderResponse:
    data = load_demo_users()
    user_orders = [
        o
        for o in data.get("orders", [])
        if o.get("user_id") == user_id and o.get("status") == "completed"
    ]
    if not user_orders:
        return UsualOrderResponse(
            user_id=user_id,
            order_id=None,
            items=[],
            total_inr=0,
            source="none",
        )

    latest = max(user_orders, key=lambda o: o.get("created_at", ""))
    items: list[LineItem] = []
    for raw in latest.get("items", []):
        product = get_product(raw["product_id"])
        # Always prefer live catalog price/name; fall back to historical snapshot.
        unit = product.price_inr if product else int(raw["unit_price_inr"])
        name = product.name if product else raw["name"]
        qty = int(raw.get("qty", 1))
        items.append(
            LineItem(
                product_id=raw["product_id"],
                name=name,
                qty=qty,
                unit_price_inr=unit,
                line_total_inr=unit * qty,
                reason="matched last purchase",
            )
        )

    total = sum(i.line_total_inr for i in items)
    return UsualOrderResponse(
        user_id=user_id,
        order_id=latest.get("order_id"),
        items=items,
        total_inr=total,
        source="most_recent_completed_order",
    )
