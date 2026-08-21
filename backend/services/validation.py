"""Core proposal validation (bounds). Full gated guardrails expand later."""

from __future__ import annotations

from backend.models import Cart, ValidationIssue
from backend.services.data_loader import load_policy


def validate_cart(
    cart: Cart,
    stated_budget_inr: int | None = None,
) -> list[ValidationIssue]:
    policy = load_policy()
    bounds = policy.get("bounds", {})
    issues: list[ValidationIssue] = []

    max_items = int(bounds.get("max_items_per_proposal", 5))
    if cart.item_count > max_items:
        issues.append(
            ValidationIssue(
                code="max_items",
                message=f"Cart has {cart.item_count} items; max allowed is {max_items}",
            )
        )

    hard_max = int(bounds.get("hard_max_order_value_inr", 5000))
    if cart.total_inr > hard_max:
        issues.append(
            ValidationIssue(
                code="hard_max_exceeded",
                message=f"Total ₹{cart.total_inr} exceeds hard ceiling ₹{hard_max}",
            )
        )

    if stated_budget_inr is not None and cart.total_inr > stated_budget_inr:
        issues.append(
            ValidationIssue(
                code="budget_exceeded",
                message=(
                    f"Total ₹{cart.total_inr} exceeds stated budget ₹{stated_budget_inr}"
                ),
            )
        )

    if not cart.items:
        issues.append(
            ValidationIssue(
                code="empty_cart",
                message="Cart has no valid items",
            )
        )

    return issues
