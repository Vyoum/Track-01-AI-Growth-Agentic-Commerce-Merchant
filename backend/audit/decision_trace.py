"""Build one coherent decision-trace story from checkout + growth + guardrails."""

from __future__ import annotations

from typing import Any

from backend.agent.guardrails import GuardrailResult
from backend.audit.checks import (
    DecisionCheck,
    build_growth_checks,
    build_guardrail_checks,
    build_history_checks,
    format_checks_narrative,
    summarize_checks,
)
from backend.models import Proposal
from backend.services.data_loader import load_policy
from backend.services.growth import GrowthDecisionResult


def build_proposal_decision_trace(
    *,
    proposal: Proposal,
    growth_result: GrowthDecisionResult | None,
    guarded: GuardrailResult | None = None,
    history_outcome: dict[str, Any] | None = None,
    user_request_summary: str | None = None,
) -> dict[str, Any]:
    """Structured pass/fail checks + judge-readable narrative for one proposal."""
    checks: list[DecisionCheck] = []

    checks.extend(
        build_history_checks(history_outcome=history_outcome, proposal=proposal)
    )

    if guarded is not None:
        bounds = load_policy().get("bounds", {})
        checks.extend(
            build_guardrail_checks(
                guarded=guarded,
                stated_budget_inr=proposal.stated_budget_inr,
                hard_max_inr=int(bounds.get("hard_max_order_value_inr", 5000)),
                max_items=int(bounds.get("max_items_per_proposal", 5)),
            )
        )

    checks.extend(
        build_growth_checks(growth_result=growth_result, proposal=proposal)
    )

    summary = summarize_checks(checks)
    checks_narrative = format_checks_narrative(checks, summary)

    header_lines = ["DECISION TRACE"]
    if user_request_summary:
        header_lines.append(f'User request: "{user_request_summary}"')
    header_lines.append("")
    narrative = "\n".join(header_lines) + "\n" + checks_narrative

    return {
        "title": "DECISION TRACE",
        "proposal_id": proposal.id,
        "user_request": user_request_summary,
        "checks": [c.to_dict() for c in checks],
        "summary": summary,
        "narrative": narrative,
        "all_required_passed": summary["all_required_passed"],
    }


def build_gate_trace(
    checks: list[DecisionCheck],
    *,
    gate_name: str,
    proposal_id: str,
) -> dict[str, Any]:
    summary = summarize_checks(checks)
    narrative = format_checks_narrative(checks, summary)
    header = f"GATE TRACE — {gate_name}\nProposal: {proposal_id}\n\n"
    return {
        "title": f"GATE TRACE ({gate_name})",
        "proposal_id": proposal_id,
        "gate": gate_name,
        "checks": [c.to_dict() for c in checks],
        "summary": summary,
        "narrative": header + narrative,
        "all_required_passed": summary["all_required_passed"],
    }
