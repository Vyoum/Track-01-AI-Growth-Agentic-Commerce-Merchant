"""Bounded, gated, explainable guardrails for checkout money actions.

All pricing/stock/category checks live here as application code — not in the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.models import Cart, LineItem, Proposal, ProposalStatus, ValidationIssue
from backend.services.catalog import get_product
from backend.services.data_loader import load_policy


@dataclass
class GuardrailAction:
    code: str
    message: str
    product_id: str | None = None
    replaced_with: str | None = None


@dataclass
class GuardrailResult:
    cart: Cart
    actions: list[GuardrailAction] = field(default_factory=list)
    blocking_issues: list[ValidationIssue] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking_issues and bool(self.cart.items)


def _policy_bounds() -> dict:
    return load_policy().get("bounds", {})


def _denied_categories() -> set[str]:
    return set(_policy_bounds().get("denied_categories", []))


def explain_line_reasons(items: list[LineItem]) -> list[str]:
    return [f"{i.name}: {i.reason}" for i in items]


def build_guarded_line(
    product_id: str,
    qty: int = 1,
    reason: str = "requested",
    allow_substitute: bool = True,
) -> tuple[LineItem | None, list[GuardrailAction], ValidationIssue | None]:
    """Resolve one SKU with stock substitution and category denial."""
    actions: list[GuardrailAction] = []
    product = get_product(product_id)
    if product is None:
        return None, actions, ValidationIssue(
            code="unknown_product",
            message=f"Unknown product: {product_id}",
            product_id=product_id,
        )

    resolved_id = product_id
    resolved = product
    line_reason = reason

    if resolved.stock < qty:
        if allow_substitute and resolved.substitute_with:
            alt = get_product(resolved.substitute_with)
            if alt and alt.stock >= qty and alt.category not in _denied_categories():
                actions.append(
                    GuardrailAction(
                        code="substituted",
                        message=(
                            f"{resolved.name} is out of stock; substituted "
                            f"{alt.name} (₹{alt.price_inr})"
                        ),
                        product_id=product_id,
                        replaced_with=alt.id,
                    )
                )
                resolved_id = alt.id
                resolved = alt
                line_reason = (
                    f"substituted for out-of-stock {product.name}"
                )
            else:
                return None, actions, ValidationIssue(
                    code="out_of_stock",
                    message=(
                        f"{product.name} is out of stock and no eligible substitute"
                    ),
                    product_id=product_id,
                )
        else:
            return None, actions, ValidationIssue(
                code="out_of_stock",
                message=f"{product.name} is out of stock (need {qty}, have {product.stock})",
                product_id=product_id,
            )

    if resolved.category in _denied_categories():
        actions.append(
            GuardrailAction(
                code="denied_category",
                message=f"Skipped {resolved.name}: category '{resolved.category}' not allowed",
                product_id=resolved.id,
            )
        )
        return None, actions, ValidationIssue(
            code="denied_category",
            message=f"Category '{resolved.category}' is not allowed for agent checkout",
            product_id=resolved.id,
        )

    if qty < 1:
        return None, actions, ValidationIssue(
            code="invalid_qty",
            message="Quantity must be at least 1",
            product_id=resolved_id,
        )

    line = LineItem(
        product_id=resolved.id,
        name=resolved.name,
        qty=qty,
        unit_price_inr=resolved.price_inr,
        line_total_inr=resolved.price_inr * qty,
        reason=line_reason,
    )
    actions.append(
        GuardrailAction(
            code="accepted",
            message=f"Accepted {line.name} x{line.qty} @ ₹{line.unit_price_inr} ({line.reason})",
            product_id=line.product_id,
        )
    )
    return line, actions, None


def trim_to_budget(
    cart: Cart,
    stated_budget_inr: int,
    protect_product_ids: set[str] | None = None,
) -> tuple[Cart, list[GuardrailAction]]:
    """Drop lowest-value unprotected lines until under budget (explainable)."""
    protect = protect_product_ids or set()
    actions: list[GuardrailAction] = []
    items = list(cart.items)

    def total() -> int:
        return sum(i.line_total_inr for i in items)

    while items and total() > stated_budget_inr:
        trim_candidates = [i for i in items if i.product_id not in protect]
        if not trim_candidates:
            break
        # Lowest priority = cheapest line first (deterministic).
        victim = min(trim_candidates, key=lambda i: (i.line_total_inr, i.name))
        items.remove(victim)
        actions.append(
            GuardrailAction(
                code="trimmed",
                message=(
                    f"Removed {victim.name} (₹{victim.line_total_inr}) to fit "
                    f"budget ₹{stated_budget_inr}"
                ),
                product_id=victim.product_id,
            )
        )

    return Cart(user_id=cart.user_id, items=items), actions


def apply_cart_guardrails(
    user_id: str,
    product_ids: list[str],
    quantities: dict[str, int] | None = None,
    reasons: dict[str, str] | None = None,
    stated_budget_inr: int | None = None,
    allow_substitute: bool = True,
    allow_trim: bool = False,
    protect_product_ids: set[str] | None = None,
) -> GuardrailResult:
    """Build a cart with substitutions/trims and collect explainable actions."""
    quantities = quantities or {}
    reasons = reasons or {}
    actions: list[GuardrailAction] = []
    issues: list[ValidationIssue] = []
    items: list[LineItem] = []
    seen: set[str] = set()

    for product_id in product_ids:
        if product_id in seen:
            continue
        seen.add(product_id)
        qty = int(quantities.get(product_id, 1))
        line, line_actions, issue = build_guarded_line(
            product_id,
            qty=qty,
            reason=reasons.get(product_id, "requested"),
            allow_substitute=allow_substitute,
        )
        actions.extend(line_actions)
        if issue:
            # Substituted denial/oos that couldn't resolve stays blocking unless trimmed later.
            if issue.code in {"unknown_product", "denied_category", "out_of_stock", "invalid_qty"}:
                # denied/oos without substitute: record but continue building others
                if issue.code == "denied_category":
                    continue
                issues.append(issue)
            continue
        if line:
            # Avoid duplicate SKUs after substitution
            if any(i.product_id == line.product_id for i in items):
                actions.append(
                    GuardrailAction(
                        code="skipped_duplicate",
                        message=f"Skipped duplicate {line.name} after substitution",
                        product_id=line.product_id,
                    )
                )
                continue
            items.append(line)

    cart = Cart(user_id=user_id, items=items)
    bounds = _policy_bounds()

    if stated_budget_inr is not None and cart.total_inr > stated_budget_inr:
        if allow_trim:
            cart, trim_actions = trim_to_budget(
                cart, stated_budget_inr, protect_product_ids=protect_product_ids
            )
            actions.extend(trim_actions)
        if cart.total_inr > stated_budget_inr:
            issues.append(
                ValidationIssue(
                    code="budget_exceeded",
                    message=(
                        f"Total ₹{cart.total_inr} exceeds stated budget ₹{stated_budget_inr}"
                    ),
                )
            )

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

    if not cart.items:
        issues.append(
            ValidationIssue(code="empty_cart", message="Cart has no valid items")
        )

    reasons_out = [a.message for a in actions if a.code in {
        "accepted", "substituted", "trimmed", "denied_category", "skipped_duplicate"
    }]
    return GuardrailResult(
        cart=cart,
        actions=actions,
        blocking_issues=issues,
        reasons=reasons_out,
    )


def assert_addon_gate(proposal: Proposal) -> None:
    """Add-on must be resolved before payment confirmation."""
    from fastapi import HTTPException

    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "addon_gate",
                "message": "Resolve the optional add-on offer (accept/skip) before payment confirmation",
            },
        )


def assert_payment_confirmation_gate(
    proposal: Proposal,
    expected_total_inr: int,
    user_id: str | None = None,
) -> None:
    """Payment only after explicit confirm of exact proposal_id + total (+ user)."""
    from fastapi import HTTPException

    now = datetime.now(timezone.utc)
    if proposal.status == ProposalStatus.EXPIRED or now > proposal.expires_at:
        raise HTTPException(
            status_code=410,
            detail={"code": "expired", "message": "Proposal expired — create a new one"},
        )

    assert_addon_gate(proposal)

    if proposal.status != ProposalStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_status",
                "message": f"Proposal cannot be confirmed (status={proposal.status})",
            },
        )

    if user_id and user_id != proposal.user_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "user_mismatch",
                "message": "Confirmation user does not match proposal owner",
            },
        )

    if expected_total_inr != proposal.total_inr:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "total_mismatch",
                "message": "Confirmation total mismatch — refresh proposal and confirm again",
                "expected_server_total_inr": proposal.total_inr,
                "client_expected_total_inr": expected_total_inr,
                "proposal_id": proposal.id,
            },
        )


def payment_confirm_prompt(proposal: Proposal) -> str:
    policy = load_policy()
    template = policy.get("confirmation_wording", {}).get(
        "payment_confirm_template",
        "Ready to pay ₹{total} for: {line_summary}. Confirm payment?",
    )
    return template.format(total=proposal.total_inr, line_summary=proposal.line_summary)


# Back-compat alias used by older stub imports
def validate_proposal(cart: Cart, stated_budget_inr: int | None = None) -> list[ValidationIssue]:
    result = apply_cart_guardrails(
        user_id=cart.user_id,
        product_ids=[i.product_id for i in cart.items],
        quantities={i.product_id: i.qty for i in cart.items},
        reasons={i.product_id: i.reason for i in cart.items},
        stated_budget_inr=stated_budget_inr,
        allow_substitute=False,
        allow_trim=False,
    )
    return result.blocking_issues
