"""Build carts from server catalog prices — never trust client totals."""

from __future__ import annotations

from backend.models import Cart, LineItem, ValidationIssue
from backend.services.catalog import get_product
from backend.services.data_loader import load_policy


def build_line_item(
    product_id: str,
    qty: int = 1,
    reason: str = "requested",
) -> tuple[LineItem | None, ValidationIssue | None]:
    product = get_product(product_id)
    if product is None:
        return None, ValidationIssue(
            code="unknown_product",
            message=f"Unknown product: {product_id}",
            product_id=product_id,
        )
    if qty < 1:
        return None, ValidationIssue(
            code="invalid_qty",
            message="Quantity must be at least 1",
            product_id=product_id,
        )
    if product.stock < qty:
        return None, ValidationIssue(
            code="out_of_stock",
            message=f"{product.name} is out of stock (need {qty}, have {product.stock})",
            product_id=product_id,
        )

    policy = load_policy()
    denied = set(policy.get("bounds", {}).get("denied_categories", []))
    if product.category in denied:
        return None, ValidationIssue(
            code="denied_category",
            message=f"Category '{product.category}' is not allowed for agent checkout",
            product_id=product_id,
        )

    line = LineItem(
        product_id=product.id,
        name=product.name,
        qty=qty,
        unit_price_inr=product.price_inr,
        line_total_inr=product.price_inr * qty,
        reason=reason,
    )
    return line, None


def build_cart(
    user_id: str,
    product_ids: list[str],
    quantities: dict[str, int] | None = None,
    reasons: dict[str, str] | None = None,
) -> tuple[Cart, list[ValidationIssue]]:
    quantities = quantities or {}
    reasons = reasons or {}
    items: list[LineItem] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()

    for product_id in product_ids:
        if product_id in seen:
            continue
        seen.add(product_id)
        qty = int(quantities.get(product_id, 1))
        line, issue = build_line_item(
            product_id,
            qty=qty,
            reason=reasons.get(product_id, "requested"),
        )
        if issue:
            issues.append(issue)
            continue
        assert line is not None
        items.append(line)

    return Cart(user_id=user_id, items=items), issues
