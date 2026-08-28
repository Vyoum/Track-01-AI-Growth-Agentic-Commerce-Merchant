"""Structured pass/fail check records for judge-visible decision traces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.agent.guardrails import GuardrailResult
from backend.models import Proposal, ProposalStatus
from backend.services.growth import CandidateEvaluation, GrowthDecisionResult

CheckStatus = Literal["pass", "fail", "warn", "skip", "info"]


@dataclass
class DecisionCheck:
    id: str
    label: str
    status: CheckStatus
    reason: str
    phase: str = "guardrail"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_checks(checks: list[DecisionCheck]) -> dict[str, Any]:
    passed = sum(1 for c in checks if c.status == "pass")
    failed = sum(1 for c in checks if c.status == "fail")
    warned = sum(1 for c in checks if c.status == "warn")
    skipped = sum(1 for c in checks if c.status == "skip")
    required = [c for c in checks if c.status in {"pass", "fail"}]
    return {
        "total": len(checks),
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "skipped": skipped,
        "all_required_passed": all(c.status == "pass" for c in required),
    }


def build_history_checks(
    *,
    history_outcome: dict[str, Any] | None,
    proposal: Proposal,
) -> list[DecisionCheck]:
    checks: list[DecisionCheck] = []
    if history_outcome is None:
        checks.append(
            DecisionCheck(
                id="history_checked",
                label="Order history lookup",
                status="skip",
                reason=f"Not using usual order (source={proposal.proposal_source})",
                phase="history",
            )
        )
        return checks

    if history_outcome.get("order_found"):
        checks.append(
            DecisionCheck(
                id="history_checked",
                label="Order history lookup",
                status="pass",
                reason=(
                    f"Completed order found ({history_outcome.get('order_id') or 'latest'})"
                ),
                phase="history",
                data=history_outcome,
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="history_checked",
                label="Order history lookup",
                status="info",
                reason="No completed order history for this user",
                phase="history",
                data=history_outcome,
            )
        )

    if proposal.proposal_source == "bestsellers":
        checks.append(
            DecisionCheck(
                id="bestseller_fallback_used",
                label="Bestseller fallback",
                status="warn",
                reason=proposal.source_reason,
                phase="history",
                data={"proposal_source": proposal.proposal_source},
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="bestseller_fallback_used",
                label="Bestseller fallback",
                status="skip",
                reason="Not needed — order history supplied the cart",
                phase="history",
            )
        )
    return checks


def build_guardrail_checks(
    *,
    guarded: GuardrailResult,
    stated_budget_inr: int | None,
    hard_max_inr: int = 5000,
    max_items: int = 5,
) -> list[DecisionCheck]:
    checks: list[DecisionCheck] = []
    issue_codes = {i.code for i in guarded.blocking_issues}

    # Per-line stock / category from actions
    substituted = [a for a in guarded.actions if a.code == "substituted"]
    denied = [a for a in guarded.actions if a.code == "denied_category"]
    trimmed = [a for a in guarded.actions if a.code == "trimmed"]

    if "out_of_stock" in issue_codes or "unknown_product" in issue_codes:
        stock_issues = [i for i in guarded.blocking_issues if i.code in {"out_of_stock", "unknown_product"}]
        checks.append(
            DecisionCheck(
                id="stock_check_passed",
                label="Stock availability",
                status="fail",
                reason="; ".join(i.message for i in stock_issues),
                phase="guardrail",
                data={"issues": [i.model_dump() for i in stock_issues]},
            )
        )
    elif substituted:
        checks.append(
            DecisionCheck(
                id="stock_check_passed",
                label="Stock availability",
                status="warn",
                reason="; ".join(a.message for a in substituted),
                phase="guardrail",
                data={"substitutions": [asdict(a) for a in substituted]},
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="stock_check_passed",
                label="Stock availability",
                status="pass",
                reason="All cart lines in stock (server catalog)",
                phase="guardrail",
            )
        )

    if denied or "denied_category" in issue_codes:
        checks.append(
            DecisionCheck(
                id="denied_category_check",
                label="Denied categories",
                status="fail" if "denied_category" in issue_codes else "warn",
                reason="; ".join(a.message for a in denied) or "Denied category in cart",
                phase="guardrail",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="denied_category_check",
                label="Denied categories",
                status="pass",
                reason="No gift-card or alcohol-adjacent items",
                phase="guardrail",
            )
        )

    if stated_budget_inr is not None:
        if "budget_exceeded" in issue_codes:
            checks.append(
                DecisionCheck(
                    id="budget_check_passed",
                    label="Stated budget",
                    status="fail",
                    reason=(
                        f"Cart ₹{guarded.cart.total_inr} exceeds stated budget "
                        f"₹{stated_budget_inr}"
                    ),
                    phase="guardrail",
                    data={
                        "cart_total_inr": guarded.cart.total_inr,
                        "stated_budget_inr": stated_budget_inr,
                    },
                )
            )
        elif trimmed:
            checks.append(
                DecisionCheck(
                    id="budget_check_passed",
                    label="Stated budget",
                    status="warn",
                    reason="; ".join(a.message for a in trimmed),
                    phase="guardrail",
                    data={"cart_total_inr": guarded.cart.total_inr},
                )
            )
        else:
            checks.append(
                DecisionCheck(
                    id="budget_check_passed",
                    label="Stated budget",
                    status="pass",
                    reason=(
                        f"Cart ₹{guarded.cart.total_inr} within stated budget "
                        f"₹{stated_budget_inr}"
                    ),
                    phase="guardrail",
                    data={
                        "cart_total_inr": guarded.cart.total_inr,
                        "stated_budget_inr": stated_budget_inr,
                    },
                )
            )
    else:
        checks.append(
            DecisionCheck(
                id="budget_check_passed",
                label="Stated budget",
                status="skip",
                reason="No budget provided by user",
                phase="guardrail",
            )
        )

    if "hard_max_exceeded" in issue_codes:
        checks.append(
            DecisionCheck(
                id="hard_max_ceiling",
                label="Hard max order value",
                status="fail",
                reason=f"Exceeds ₹{hard_max_inr} ceiling",
                phase="guardrail",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="hard_max_ceiling",
                label="Hard max order value",
                status="pass",
                reason=f"Within ₹{hard_max_inr} server ceiling",
                phase="guardrail",
                data={"cart_total_inr": guarded.cart.total_inr},
            )
        )

    if "max_items" in issue_codes:
        checks.append(
            DecisionCheck(
                id="max_items_check",
                label="Max items per proposal",
                status="fail",
                reason=f"Exceeds {max_items} items",
                phase="guardrail",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="max_items_check",
                label="Max items per proposal",
                status="pass",
                reason=f"{guarded.cart.item_count} item(s) ≤ max {max_items}",
                phase="guardrail",
            )
        )

    if "empty_cart" in issue_codes:
        checks.append(
            DecisionCheck(
                id="cart_non_empty",
                label="Cart has valid items",
                status="fail",
                reason="Cart is empty after guardrails",
                phase="guardrail",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="cart_non_empty",
                label="Cart has valid items",
                status="pass",
                reason=f"{guarded.cart.item_count} valid line(s) in cart",
                phase="guardrail",
                data={"cart_total_inr": guarded.cart.total_inr},
            )
        )

    return checks


def build_payment_gate_checks(
    *,
    proposal: Proposal,
    expected_total_inr: int,
    user_id: str | None = None,
) -> list[DecisionCheck]:
    """Checks run immediately before payment confirmation."""
    checks: list[DecisionCheck] = []

    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION:
        checks.append(
            DecisionCheck(
                id="addon_gate",
                label="Add-on resolved before payment",
                status="fail",
                reason="User must accept or skip the add-on offer first",
                phase="gate",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="addon_gate",
                label="Add-on resolved before payment",
                status="pass",
                reason="No pending add-on decision",
                phase="gate",
            )
        )

    if user_id and user_id != proposal.user_id:
        checks.append(
            DecisionCheck(
                id="user_mismatch",
                label="Confirming user matches proposal owner",
                status="fail",
                reason=f"Expected {proposal.user_id}, got {user_id}",
                phase="gate",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="user_match",
                label="Confirming user matches proposal owner",
                status="pass",
                reason=user_id or "User not supplied (allowed in demo)",
                phase="gate",
            )
        )

    if expected_total_inr != proposal.total_inr:
        checks.append(
            DecisionCheck(
                id="total_mismatch",
                label="Confirmation total matches server proposal",
                status="fail",
                reason=(
                    f"Client sent ₹{expected_total_inr}, server total is ₹{proposal.total_inr}"
                ),
                phase="gate",
                data={
                    "client_expected_total_inr": expected_total_inr,
                    "server_total_inr": proposal.total_inr,
                },
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="total_match",
                label="Confirmation total matches server proposal",
                status="pass",
                reason=f"Both sides agree on ₹{proposal.total_inr}",
                phase="gate",
            )
        )

    checks.append(
        DecisionCheck(
            id="explicit_payment_confirmation",
            label="Explicit payment confirmation required",
            status="pass",
            reason="Payment gate invoked only after user confirmed exact total",
            phase="gate",
        )
    )

    return checks


def build_addon_decision_checks(
    *,
    decision: str,
    product_id: str | None,
    offer_product_id: str | None,
    new_total_inr: int,
    baseline_inr: int,
) -> list[DecisionCheck]:
    checks: list[DecisionCheck] = []
    if decision == "accept":
        if offer_product_id and product_id and product_id != offer_product_id:
            checks.append(
                DecisionCheck(
                    id="addon_product_match",
                    label="Accepted product matches offer",
                    status="fail",
                    reason=f"Offer was {offer_product_id}, client sent {product_id}",
                    phase="gate",
                )
            )
        else:
            checks.append(
                DecisionCheck(
                    id="addon_product_match",
                    label="Accepted product matches offer",
                    status="pass",
                    reason=f"Accepted {product_id or offer_product_id}",
                    phase="gate",
                )
            )
        checks.append(
            DecisionCheck(
                id="addon_accepted",
                label="User accepted optional add-on",
                status="pass",
                reason=f"Total updated ₹{baseline_inr} → ₹{new_total_inr}",
                phase="gate",
                data={"baseline_inr": baseline_inr, "new_total_inr": new_total_inr},
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="addon_skipped",
                label="User skipped optional add-on",
                status="pass",
                reason=f"Staying at baseline ₹{baseline_inr}",
                phase="gate",
            )
        )
    return checks


def build_growth_checks(
    *,
    growth_result: GrowthDecisionResult | None,
    proposal: Proposal,
) -> list[DecisionCheck]:
    if growth_result is None:
        return [
            DecisionCheck(
                id="growth_evaluated",
                label="Growth add-on evaluation",
                status="skip",
                reason="Growth not requested for this proposal",
                phase="growth",
            )
        ]

    checks: list[DecisionCheck] = []
    baseline = proposal.baseline_total_inr or proposal.total_inr
    budget = proposal.stated_budget_inr
    remaining = budget - baseline if budget is not None else None

    checks.append(
        DecisionCheck(
            id="relationship_data",
            label="Product relationship data",
            status="pass" if growth_result.has_relationship_data else "info",
            reason=(
                "Complements found on cart products"
                if growth_result.has_relationship_data
                else "None on cart — eligible for popular-budget fallback"
            ),
            phase="growth",
            data={"has_relationship_data": growth_result.has_relationship_data},
        )
    )

    if growth_result.growth_method == "popular_budget_fit":
        checks.append(
            DecisionCheck(
                id="growth_fallback",
                label="Growth fallback method",
                status="info",
                reason="popular-budget-fit (no complement graph)",
                phase="growth",
            )
        )

    evaluations = growth_result.candidate_evaluations or []
    eligible = [e for e in evaluations if e.status == "eligible"]
    rejected = [e for e in evaluations if e.status == "rejected"]

    checks.append(
        DecisionCheck(
            id="candidate_products_evaluated",
            label="Add-on candidates screened",
            status="pass" if evaluations else "skip",
            reason=(
                f"{len(eligible)} eligible, {len(rejected)} rejected "
                f"of {len(evaluations)} screened"
                if evaluations
                else "No candidates screened"
            ),
            phase="growth",
            data={
                "eligible": [_eval_dict(e) for e in eligible[:20]],
                "rejected_sample": [_eval_dict(e) for e in rejected[:20]],
                "total_screened": len(evaluations),
            },
        )
    )

    for ev in rejected[:8]:
        checks.append(
            DecisionCheck(
                id=f"candidate_{ev.product_id}",
                label=f"Candidate: {ev.name}",
                status="info",
                reason=f"₹{ev.price_inr} — rejected: {ev.reason}",
                phase="growth",
                data=_eval_dict(ev),
            )
        )

    if growth_result.offer:
        offer = growth_result.offer
        checks.append(
            DecisionCheck(
                id="growth_reason_calculated",
                label="Add-on selected",
                status="pass",
                reason=(
                    f"{offer.name} ₹{offer.price_inr} ({offer.source}): "
                    + "; ".join(growth_result.selection_rationale or [])
                ),
                phase="growth",
                data={
                    "product_id": offer.product_id,
                    "projected_total_inr": offer.projected_total_inr,
                    "rationale": growth_result.selection_rationale,
                },
            )
        )
        if budget is not None:
            within = offer.projected_total_inr <= budget
            checks.append(
                DecisionCheck(
                    id="projected_total_within_budget",
                    label="Projected total vs budget",
                    status="pass" if within else "fail",
                    reason=(
                        f"₹{offer.projected_total_inr} ≤ ₹{budget}"
                        if within
                        else f"₹{offer.projected_total_inr} exceeds ₹{budget}"
                    ),
                    phase="growth",
                    data={
                        "baseline_inr": baseline,
                        "addon_inr": offer.price_inr,
                        "projected_total_inr": offer.projected_total_inr,
                        "stated_budget_inr": budget,
                        "remaining_inr": remaining,
                    },
                )
            )
        checks.append(
            DecisionCheck(
                id="addon_gate",
                label="Add-on acceptance gate",
                status="info",
                reason="Awaiting explicit user accept/skip before payment",
                phase="gate",
            )
        )
    else:
        checks.append(
            DecisionCheck(
                id="growth_offer_shown",
                label="Growth add-on offered",
                status="skip",
                reason="; ".join(growth_result.skipped_reasons[:3])
                or "No eligible add-on",
                phase="growth",
                data={"skipped_reasons": growth_result.skipped_reasons},
            )
        )

    return checks


def _eval_dict(ev: CandidateEvaluation) -> dict[str, Any]:
    return {
        "product_id": ev.product_id,
        "name": ev.name,
        "price_inr": ev.price_inr,
        "status": ev.status,
        "reason": ev.reason,
    }


def format_checks_narrative(checks: list[DecisionCheck], summary: dict[str, Any]) -> str:
    lines = [
        "GUARDRAIL & DECISION CHECKS",
        f"Summary: {summary['passed']} passed, {summary['failed']} failed, "
        f"{summary['warned']} warned, {summary['skipped']} skipped",
        "",
    ]
    by_phase: dict[str, list[DecisionCheck]] = {}
    for check in checks:
        by_phase.setdefault(check.phase, []).append(check)

    status_icon = {"pass": "✓ PASS", "fail": "✗ FAIL", "warn": "⚠ WARN", "skip": "— SKIP", "info": "· INFO"}

    for phase, phase_checks in by_phase.items():
        lines.append(f"[{phase.upper()}]")
        for c in phase_checks:
            icon = status_icon.get(c.status, c.status.upper())
            lines.append(f"  {icon}  {c.label}: {c.reason}")
        lines.append("")

    verdict = "ALL REQUIRED CHECKS PASSED" if summary["all_required_passed"] else "CHECKS FAILED"
    lines.append(verdict)
    return "\n".join(lines).strip()
