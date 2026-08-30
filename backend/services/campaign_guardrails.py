"""Campaign guardrails — same trust class as cart guardrails."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.models import GrowthOffer
from backend.services.catalog import get_product
from backend.services.data_loader import load_campaigns, load_policy


@dataclass
class CampaignGuardrailResult:
    ok: bool
    notes: list[str] = field(default_factory=list)


def apply_campaign_guardrails(
    *,
    campaign: dict,
    offer: GrowthOffer,
    target_segment: str,
    discount_pct: float = 0.0,
) -> CampaignGuardrailResult:
    cfg = load_campaigns()
    guard = cfg.get("campaign_guardrails", {})
    known_segments = set(cfg.get("known_segments", []))
    notes: list[str] = []

    if target_segment not in known_segments:
        return CampaignGuardrailResult(
            False, [f"Unknown segment {target_segment!r} — not in known_segments"]
        )
    notes.append(f"Segment {target_segment} is a known filter")

    max_discount = float(
        campaign.get("max_discount_pct", guard.get("max_discount_pct", 0))
    )
    if discount_pct > max_discount:
        return CampaignGuardrailResult(
            False,
            [f"Discount {discount_pct}% exceeds campaign max {max_discount}%"],
        )
    notes.append(f"Discount {discount_pct}% ≤ max {max_discount}%")

    product = get_product(offer.product_id)
    if product is None:
        return CampaignGuardrailResult(False, [f"Unknown product {offer.product_id}"])

    policy_denied = set(load_policy().get("bounds", {}).get("denied_categories", []))
    cfg_denied = set(guard.get("denied_categories", []))
    denied = policy_denied | cfg_denied
    if product.category in denied:
        return CampaignGuardrailResult(
            False, [f"Category {product.category} is denied"]
        )
    notes.append(f"Category {product.category} allowed")

    allowed = set(campaign.get("allowed_categories") or [])
    if allowed and product.category not in allowed:
        return CampaignGuardrailResult(
            False,
            [f"Category {product.category} not in campaign allowed_categories"],
        )
    if allowed:
        notes.append("Category within campaign allow-list")

    if product.stock < 1:
        return CampaignGuardrailResult(False, ["Offer product out of stock"])
    notes.append("Offer product in stock")

    return CampaignGuardrailResult(True, notes)
