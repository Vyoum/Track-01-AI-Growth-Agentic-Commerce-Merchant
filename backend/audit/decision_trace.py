"""Build one coherent decision-trace story from checkout + growth outcomes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.models import LineItem, Proposal
from backend.services.growth import CandidateEvaluation, GrowthDecisionResult


def _format_items(items: list[LineItem]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": i.product_id,
            "name": i.name,
            "line_total_inr": i.line_total_inr,
        }
        for i in items
    ]


def build_proposal_decision_trace(
    *,
    proposal: Proposal,
    growth_result: GrowthDecisionResult | None,
    history_outcome: dict[str, Any] | None = None,
    guardrail_passed: bool = True,
    user_request_summary: str | None = None,
) -> dict[str, Any]:
    """Structured + human-readable trace for one proposal build."""
    steps: list[dict[str, Any]] = []
    step_no = 0

    if user_request_summary:
        step_no += 1
        steps.append(
            {
                "step": step_no,
                "key": "user_request",
                "title": "User request",
                "detail": user_request_summary,
            }
        )

    # 1. History / source
    step_no += 1
    if history_outcome:
        if history_outcome.get("order_found"):
            detail = (
                f"Previous order found ({history_outcome.get('order_id') or 'latest'}) "
                "→ used completed order history"
            )
        elif proposal.proposal_source == "bestsellers":
            detail = (
                "No previous order found → used bestseller fallback. "
                f"{proposal.source_reason}"
            )
        else:
            detail = f"Cart source: {proposal.proposal_source}. {proposal.source_reason}"
        steps.append(
            {
                "step": step_no,
                "key": "history_checked",
                "title": "Order history",
                "detail": detail,
                "data": history_outcome,
            }
        )
    else:
        steps.append(
            {
                "step": step_no,
                "key": "history_checked",
                "title": "Cart source",
                "detail": f"{proposal.proposal_source}: {proposal.source_reason}",
            }
        )

    if proposal.proposal_source == "bestsellers":
        step_no += 1
        steps.append(
            {
                "step": step_no,
                "key": "bestseller_fallback_used",
                "title": "Bestseller fallback",
                "detail": proposal.source_reason,
            }
        )

    # 2. Base cart
    step_no += 1
    base_items = _format_items(proposal.items)
    steps.append(
        {
            "step": step_no,
            "key": "base_cart_selected",
            "title": "Base cart selected",
            "detail": ", ".join(f"{i['name']} ₹{i['line_total_inr']}" for i in base_items),
            "data": {"items": base_items, "total_inr": proposal.total_inr},
        }
    )

    baseline = proposal.baseline_total_inr or proposal.total_inr
    budget = proposal.stated_budget_inr
    remaining = budget - baseline if budget is not None else None

    # 3. Remaining budget
    if budget is not None:
        step_no += 1
        steps.append(
            {
                "step": step_no,
                "key": "remaining_budget",
                "title": "Remaining budget",
                "detail": f"₹{budget} − ₹{baseline} = ₹{remaining}",
                "data": {
                    "stated_budget_inr": budget,
                    "baseline_inr": baseline,
                    "remaining_inr": remaining,
                },
            }
        )

    # 4–7. Growth decision
    if growth_result is not None:
        step_no += 1
        if growth_result.has_relationship_data:
            rel_detail = "Product relationship data available on cart items"
        else:
            rel_detail = (
                "No product relationship data available "
                "→ used popular-budget-fit fallback"
                if growth_result.growth_method == "popular_budget_fit"
                else "No product relationship data on cart items"
            )
        steps.append(
            {
                "step": step_no,
                "key": "relationship_data",
                "title": "Relationship data",
                "detail": rel_detail,
                "data": {"has_relationship_data": growth_result.has_relationship_data},
            }
        )

        if growth_result.growth_method == "popular_budget_fit":
            step_no += 1
            steps.append(
                {
                    "step": step_no,
                    "key": "growth_fallback",
                    "title": "Growth fallback",
                    "detail": "popular-budget-fit (no complements)",
                }
            )

        if growth_result.candidate_evaluations:
            step_no += 1
            eval_lines = []
            for ev in growth_result.candidate_evaluations:
                mark = "eligible" if ev.status == "eligible" else "rejected"
                eval_lines.append(
                    f"{ev.name} ₹{ev.price_inr} → {mark} ({ev.reason})"
                )
            steps.append(
                {
                    "step": step_no,
                    "key": "candidate_products_evaluated",
                    "title": "Candidates evaluated",
                    "detail": "; ".join(eval_lines[:12])
                    + (" …" if len(eval_lines) > 12 else ""),
                    "data": [asdict(ev) for ev in growth_result.candidate_evaluations],
                }
            )

        if growth_result.offer:
            step_no += 1
            offer = growth_result.offer
            steps.append(
                {
                    "step": step_no,
                    "key": "growth_reason_calculated",
                    "title": "Add-on selected",
                    "detail": (
                        f"{offer.name} ₹{offer.price_inr} via {offer.source}. "
                        + "; ".join(growth_result.selection_rationale)
                    ),
                    "data": {
                        "product_id": offer.product_id,
                        "source": offer.source,
                        "reason": offer.reason,
                        "rationale": growth_result.selection_rationale,
                    },
                }
            )

            step_no += 1
            steps.append(
                {
                    "step": step_no,
                    "key": "projected_total",
                    "title": "Projected total",
                    "detail": f"₹{baseline} + ₹{offer.price_inr} = ₹{offer.projected_total_inr}",
                    "data": {
                        "baseline_inr": baseline,
                        "addon_inr": offer.price_inr,
                        "projected_total_inr": offer.projected_total_inr,
                    },
                }
            )
        elif growth_result.skipped_reasons:
            step_no += 1
            steps.append(
                {
                    "step": step_no,
                    "key": "growth_offer_none",
                    "title": "No add-on offered",
                    "detail": "; ".join(growth_result.skipped_reasons[:5]),
                    "data": {"skipped_reasons": growth_result.skipped_reasons},
                }
            )

    # 8. Budget guardrail
    step_no += 1
    budget_ok = guardrail_passed and (
        budget is None or (growth_result.offer.projected_total_inr if growth_result and growth_result.offer else baseline) <= budget
    )
    steps.append(
        {
            "step": step_no,
            "key": "budget_check_passed" if budget_ok else "budget_check_failed",
            "title": "Budget guardrail",
            "detail": "PASS" if budget_ok else "FAIL",
            "data": {"passed": budget_ok, "stated_budget_inr": budget},
        }
    )

    # Stock implicitly passed if proposal was created
    step_no += 1
    steps.append(
        {
            "step": step_no,
            "key": "stock_check_passed",
            "title": "Stock guardrail",
            "detail": "PASS (in-stock items in final cart)",
            "data": {"passed": True},
        }
    )

    narrative = _format_narrative(
        user_request=user_request_summary,
        steps=steps,
        proposal=proposal,
        growth_result=growth_result,
    )

    return {
        "title": "DECISION TRACE",
        "proposal_id": proposal.id,
        "user_request": user_request_summary,
        "steps": steps,
        "narrative": narrative,
    }


def _format_narrative(
    *,
    user_request: str | None,
    steps: list[dict[str, Any]],
    proposal: Proposal,
    growth_result: GrowthDecisionResult | None,
) -> str:
    lines = ["DECISION TRACE", ""]
    if user_request:
        lines.extend([f'User request: "{user_request}"', ""])

    for step in steps:
        lines.append(f"{step['step']}. {step['title']}")
        lines.append(f"   {step['detail']}")
        lines.append("")

    if growth_result and growth_result.offer and proposal.status.value == "awaiting_addon_decision":
        lines.append(
            "Next: user must explicitly accept or skip the add-on before payment."
        )

    return "\n".join(lines).strip()
