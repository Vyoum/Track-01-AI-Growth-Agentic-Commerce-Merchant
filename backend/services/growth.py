"""Growth decision engine — complementary add-ons with uplift metrics."""

from __future__ import annotations

from dataclasses import dataclass

from backend.models import (
    Cart,
    GrowthCandidate,
    GrowthMetrics,
    GrowthOffer,
    LineItem,
    Product,
)
from backend.services import catalog
from backend.services.catalog import get_product
from backend.services.data_loader import load_demo_users, load_policy


@dataclass
class GrowthDecisionResult:
    offer: GrowthOffer | None
    candidates: list[GrowthCandidate]
    metrics: GrowthMetrics
    skipped_reasons: list[str]


def _co_purchase_boost(user_id: str | None, base_ids: set[str], candidate_id: str) -> bool:
    if not user_id:
        return False
    data = load_demo_users()
    for order in data.get("orders", []):
        if order.get("user_id") != user_id:
            continue
        ids = {i["product_id"] for i in order.get("items", [])}
        if candidate_id in ids and (ids & base_ids):
            return True
    return False


def _offer_text(
    baseline_items: list[LineItem],
    baseline_total: int,
    candidate: GrowthCandidate,
    budget: int | None,
    *,
    proposal_source: str = "completed_order_history",
    source_reason: str | None = None,
) -> str:
    policy = load_policy()
    wording = policy.get("confirmation_wording", {})
    if proposal_source == "bestsellers":
        template = wording.get(
            "bestseller_addon_offer_template",
            (
                "{source_reason}: {baseline_summary} for ₹{baseline_total}. "
                "I also found {addon_name} for ₹{addon_price}, which is {addon_reason}. "
                "Total would be ₹{projected_total}, still under your ₹{budget} budget. "
                "Want me to add it?"
            ),
        )
    else:
        template = wording.get(
            "addon_offer_template",
            (
                "I found your usual order for ₹{baseline_total} ({baseline_summary}). "
                "I also found {addon_name} for ₹{addon_price}, which is {addon_reason}. "
                "Total would be ₹{projected_total}, still under your ₹{budget} budget. "
                "Want me to add it?"
            ),
        )
    baseline_summary = " + ".join(i.name for i in baseline_items) or "your cart"
    return template.format(
        source_reason=source_reason or "here are our most popular picks",
        baseline_total=baseline_total,
        baseline_summary=baseline_summary,
        addon_name=candidate.name,
        addon_price=candidate.price_inr,
        addon_reason=candidate.reason,
        projected_total=candidate.projected_total_inr,
        budget=budget if budget is not None else "your",
    )


def _popularity_sort_key(
    product: Product,
    configured_position: dict[str, int],
) -> tuple:
    return (
        0 if product.is_bestseller else 1,
        configured_position.get(product.id, 1_000_000),
        product.bestseller_rank if product.bestseller_rank is not None else 1_000_000,
        -product.sales_count,
        -product.review_count,
        -product.rating,
        product.id,
    )


def _is_popular_product(product: Product, configured_position: dict[str, int]) -> bool:
    return (
        product.is_bestseller
        or product.bestseller_rank is not None
        or product.sales_count > 0
        or product.review_count > 0
        or product.id in configured_position
    )


def _build_candidate(
    *,
    product: Product,
    baseline: int,
    remaining: int | None,
    priority: int,
    reason: str,
    source: str,
) -> GrowthCandidate:
    projected = baseline + product.price_inr
    uplift = product.price_inr
    uplift_pct = round((uplift / baseline) * 100, 2) if baseline else 0.0
    return GrowthCandidate(
        product_id=product.id,
        name=product.name,
        price_inr=product.price_inr,
        reason=reason,
        priority=priority,
        source=source,
        remaining_budget_after_inr=(
            remaining - product.price_inr if remaining is not None else 0
        ),
        projected_total_inr=projected,
        uplift_amount_inr=uplift,
        uplift_percent=uplift_pct,
    )


def _popular_budget_fit_candidates(
    *,
    cart: Cart,
    baseline: int,
    remaining: int,
    in_cart: set[str],
    rejected: set[str],
    denied: set[str],
    max_items: int,
    skipped_reasons: list[str],
) -> list[GrowthCandidate]:
    """When no complement data exists, suggest one popular item within budget headroom."""
    policy = load_policy()
    fallback_cfg = policy.get("growth", {}).get("popular_budget_fallback", {})
    if not fallback_cfg.get("enabled", True):
        skipped_reasons.append("popular budget fallback disabled")
        return []

    min_remaining = int(fallback_cfg.get("min_remaining_inr", 1))
    if remaining < min_remaining:
        skipped_reasons.append(
            f"remaining budget ₹{remaining} below fallback minimum ₹{min_remaining}"
        )
        return []

    reason_template = str(
        fallback_cfg.get(
            "reason_template",
            "a popular add-on that fits your remaining ₹{remaining} budget",
        )
    )
    bestseller_cfg = policy.get("bestsellers", {})
    configured_rank = list(bestseller_cfg.get("fallback_ranked_product_ids", []))
    configured_position = {
        product_id: position for position, product_id in enumerate(configured_rank)
    }

    eligible: list[Product] = []
    for product in catalog.list_products():
        if product.id in in_cart:
            continue
        if product.id in rejected:
            skipped_reasons.append(f"{product.id}: previously declined this session")
            continue
        if product.stock < 1:
            continue
        if product.category in denied:
            continue
        if product.price_inr > remaining:
            continue
        if cart.item_count + 1 > max_items:
            break
        eligible.append(product)

    if not eligible:
        skipped_reasons.append("no in-stock add-on fits remaining budget")
        return []

    popular = [p for p in eligible if _is_popular_product(p, configured_position)]
    pool = popular if popular else eligible
    pool.sort(
        key=lambda product: (
            -product.price_inr,
            *_popularity_sort_key(product, configured_position),
        )
    )

    best = pool[0]
    reason = reason_template.format(remaining=remaining)
    return [
        _build_candidate(
            product=best,
            baseline=baseline,
            remaining=remaining,
            priority=50,
            reason=reason,
            source="popular_budget_fit",
        )
    ]


def _offer_from_candidates(
    *,
    cart: Cart,
    baseline: int,
    stated_budget_inr: int | None,
    candidates: list[GrowthCandidate],
    max_shown: int,
    metrics: GrowthMetrics,
    proposal_source: str = "completed_order_history",
    source_reason: str | None = None,
) -> GrowthOffer:
    candidates.sort(
        key=lambda c: (
            c.priority,
            -c.uplift_amount_inr,
            c.remaining_budget_after_inr,
        )
    )
    best = candidates[:max_shown][0]
    offer = GrowthOffer(
        product_id=best.product_id,
        name=best.name,
        price_inr=best.price_inr,
        reason=best.reason,
        source=best.source,
        offer_text=_offer_text(
            cart.items,
            baseline,
            best,
            stated_budget_inr,
            proposal_source=proposal_source,
            source_reason=source_reason,
        ),
        projected_total_inr=best.projected_total_inr,
        uplift_amount_inr=best.uplift_amount_inr,
        uplift_percent=best.uplift_percent,
    )
    metrics.recommendation_shown = True
    metrics.projected_order_value = offer.projected_total_inr
    metrics.uplift_amount = offer.uplift_amount_inr
    metrics.uplift_percent = offer.uplift_percent
    return offer


def recommend_addon(
    cart: Cart,
    stated_budget_inr: int | None = None,
    rejected_addon_ids: list[str] | None = None,
    user_id: str | None = None,
    *,
    proposal_source: str = "completed_order_history",
    source_reason: str | None = None,
) -> GrowthDecisionResult:
    """Pick at most one optional complementary add-on. Never mutates the cart."""
    policy = load_policy()
    bounds = policy.get("bounds", {})
    growth_cfg = policy.get("growth", {})
    max_shown = int(growth_cfg.get("max_recommendations_shown", 1))
    max_items = int(bounds.get("max_items_per_proposal", 5))
    denied = set(bounds.get("denied_categories", []))
    rejected = set(rejected_addon_ids or [])

    baseline = cart.total_inr
    metrics = GrowthMetrics(
        baseline_order_value=baseline,
        recommendation_shown=False,
        candidates_considered=0,
    )
    skipped_reasons: list[str] = []

    if stated_budget_inr is not None and baseline > stated_budget_inr:
        skipped_reasons.append("baseline already exceeds stated budget")
        return GrowthDecisionResult(None, [], metrics, skipped_reasons)

    if cart.item_count >= max_items:
        skipped_reasons.append("cart already at max item count")
        return GrowthDecisionResult(None, [], metrics, skipped_reasons)

    in_cart = {i.product_id for i in cart.items}
    remaining = (
        stated_budget_inr - baseline if stated_budget_inr is not None else None
    )

    # Collect complement refs from every base item.
    raw_refs: dict[str, tuple[int, str]] = {}
    for item in cart.items:
        product = get_product(item.product_id)
        if not product:
            continue
        for ref in product.complements:
            existing = raw_refs.get(ref.product_id)
            if existing is None or ref.priority < existing[0]:
                raw_refs[ref.product_id] = (ref.priority, ref.reason)

    candidates: list[GrowthCandidate] = []
    for product_id, (priority, reason) in raw_refs.items():
        if product_id in in_cart:
            skipped_reasons.append(f"{product_id}: already in cart")
            continue
        if product_id in rejected:
            skipped_reasons.append(f"{product_id}: previously declined this session")
            continue

        product = get_product(product_id)
        if product is None:
            skipped_reasons.append(f"{product_id}: unknown product")
            continue
        if product.stock < 1:
            skipped_reasons.append(f"{product_id}: out of stock")
            continue
        if product.category in denied:
            skipped_reasons.append(f"{product_id}: denied category")
            continue
        if remaining is not None and product.price_inr > remaining:
            skipped_reasons.append(
                f"{product_id}: ₹{product.price_inr} exceeds remaining budget ₹{remaining}"
            )
            continue
        if cart.item_count + 1 > max_items:
            skipped_reasons.append(f"{product_id}: would exceed max items")
            continue

        source = "catalog_complements"
        if _co_purchase_boost(user_id or cart.user_id, in_cart, product_id):
            source = "catalog_complements+co_purchase_history"
            priority = max(1, priority - 1)

        candidates.append(
            _build_candidate(
                product=product,
                baseline=baseline,
                remaining=remaining,
                priority=priority,
                reason=reason or "commonly bought together",
                source=source,
            )
        )

    if candidates:
        metrics.candidates_considered = len(candidates)
        offer = _offer_from_candidates(
            cart=cart,
            baseline=baseline,
            stated_budget_inr=stated_budget_inr,
            candidates=candidates,
            max_shown=max_shown,
            metrics=metrics,
            proposal_source=proposal_source,
            source_reason=source_reason,
        )
        return GrowthDecisionResult(offer, candidates, metrics, skipped_reasons)

    # Relationship data existed but nothing passed eligibility — do not fallback.
    if raw_refs:
        metrics.candidates_considered = 0
        return GrowthDecisionResult(None, [], metrics, skipped_reasons)

    # No complement/relationship data — popular add-on within remaining budget headroom.
    if remaining is not None and remaining > 0:
        fallback_candidates = _popular_budget_fit_candidates(
            cart=cart,
            baseline=baseline,
            remaining=remaining,
            in_cart=in_cart,
            rejected=rejected,
            denied=denied,
            max_items=max_items,
            skipped_reasons=skipped_reasons,
        )
        metrics.candidates_considered = len(fallback_candidates)
        if fallback_candidates:
            offer = _offer_from_candidates(
                cart=cart,
                baseline=baseline,
                stated_budget_inr=stated_budget_inr,
                candidates=fallback_candidates,
                max_shown=max_shown,
                metrics=metrics,
                proposal_source=proposal_source,
                source_reason=source_reason,
            )
            return GrowthDecisionResult(
                offer, fallback_candidates, metrics, skipped_reasons
            )

    return GrowthDecisionResult(None, [], metrics, skipped_reasons)
