"""Map merchant store JSON payloads into internal domain models."""

from __future__ import annotations

from typing import Any

from backend.models import ComplementRef, LineItem, Product, UsualOrderResponse


def map_product(raw: dict[str, Any], *, default_stock: int = 0) -> Product:
    """Normalize common merchant product shapes into Product."""
    pid = str(
        raw.get("id")
        or raw.get("product_id")
        or raw.get("sku")
        or ""
    )
    if not pid:
        raise ValueError("product missing id")

    price = raw.get("price_inr")
    if price is None:
        price = raw.get("price")
    if price is None and "price_paise" in raw:
        price = int(raw["price_paise"]) // 100
    price_inr = int(price)

    complements_raw = raw.get("complements") or raw.get("related_products") or []
    complements: list[ComplementRef] = []
    for i, c in enumerate(complements_raw):
        if isinstance(c, str):
            complements.append(
                ComplementRef(product_id=c, priority=i + 1, reason="merchant related")
            )
        elif isinstance(c, dict):
            complements.append(
                ComplementRef(
                    product_id=str(c.get("product_id") or c.get("id")),
                    priority=int(c.get("priority", i + 1)),
                    reason=str(c.get("reason") or "merchant related"),
                )
            )

    stock = raw.get("stock")
    if stock is None:
        stock = raw.get("inventory")
    if stock is None:
        stock = raw.get("quantity")
    stock = int(stock if stock is not None else default_stock)

    badge = str(raw.get("badge") or "").strip().upper()
    raw_tags = list(raw.get("tags") or [])
    is_bestseller = bool(
        raw.get("is_bestseller")
        or badge == "BESTSELLER"
        or any(str(tag).strip().lower() == "bestseller" for tag in raw_tags)
    )

    return Product(
        id=pid,
        name=str(raw.get("name") or raw.get("title") or pid),
        price_inr=price_inr,
        category=str(
            raw.get("category")
            or raw.get("category_label")
            or raw.get("product_type")
            or raw.get("collection")
            or "uncategorized"
        ),
        stock=stock,
        tags=raw_tags,
        complements=complements,
        substitute_with=raw.get("substitute_with") or raw.get("substitute_product_id"),
        is_bestseller=is_bestseller,
        bestseller_rank=raw.get("bestseller_rank"),
        sales_count=int(raw.get("sales_count") or raw.get("sold_count") or 0),
        rating=float(raw.get("rating") or 0),
        review_count=int(raw.get("review_count") or 0),
    )


def map_usual_order(user_id: str, raw: dict[str, Any]) -> UsualOrderResponse:
    """Normalize merchant order-history payload into UsualOrderResponse."""
    order = raw
    if "order" in raw and isinstance(raw["order"], dict):
        order = raw["order"]
    if "orders" in raw and isinstance(raw["orders"], list) and raw["orders"]:
        # Prefer most recent if a list is returned
        orders = raw["orders"]
        order = max(orders, key=lambda o: o.get("created_at") or o.get("date") or "")

    items_raw = order.get("items") or order.get("line_items") or order.get("order_items") or []
    items: list[LineItem] = []
    for it in items_raw:
        product_id = str(it.get("product_id") or it.get("id") or it.get("sku") or "")
        qty = int(it.get("qty") or it.get("quantity") or 1)
        unit = it.get("unit_price_inr")
        if unit is None:
            unit = it.get("price") or it.get("unit_price") or 0
        unit = int(unit)
        name = str(
            it.get("name")
            or it.get("product_name")
            or it.get("title")
            or product_id
        )
        items.append(
            LineItem(
                product_id=product_id,
                name=name,
                qty=qty,
                unit_price_inr=unit,
                line_total_inr=unit * qty,
                reason="matched last purchase",
            )
        )

    total = order.get("total_inr")
    if total is None:
        total = order.get("total") or sum(i.line_total_inr for i in items)

    return UsualOrderResponse(
        user_id=user_id,
        order_id=order.get("order_id") or order.get("id"),
        items=items,
        total_inr=int(total),
        source=str(order.get("source") or "merchant_api"),
    )
