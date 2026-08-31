"""Autonomous buyer-agent decision logic — no merchant backend imports."""

from __future__ import annotations

from typing import Any


def score_product(product: dict[str, Any], goal: dict[str, Any]) -> int:
    """Rank catalog rows against an independent buyer goal."""
    want = str(goal.get("want") or "").strip().lower()
    budget = int(goal.get("budget_inr") or 0)
    price = int(product.get("price_inr") or 0)
    stock = int(product.get("stock") or 0)

    if stock < 1 or price > budget:
        return -1

    haystack = " ".join(
        [
            str(product.get("id") or ""),
            str(product.get("name") or ""),
            str(product.get("category") or ""),
            " ".join(str(t) for t in product.get("tags") or []),
        ]
    ).lower()

    score = 0
    if want and want in haystack:
        score += 10
    for token in want.split():
        if len(token) > 2 and token in haystack:
            score += 3
    if price <= budget:
        score += 1
    return score


def pick_product(
    products: list[dict[str, Any]],
    goal: dict[str, Any],
) -> dict[str, Any] | None:
    ranked = sorted(products, key=lambda p: score_product(p, goal), reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    return best if score_product(best, goal) >= 0 else None


def should_accept_addon(
    offer: dict[str, Any] | None,
    goal: dict[str, Any],
    *,
    max_addon_inr: int = 150,
) -> bool:
    """Buyer policy: accept complements under ₹150 that keep total within budget."""
    if not offer:
        return False
    price = int(offer.get("price_inr") or 0)
    projected = int(offer.get("projected_total_inr") or 0)
    budget = int(goal.get("budget_inr") or 0)
    return price <= max_addon_inr and projected <= budget
