"""Replay synthetic customer sessions through the real checkout pipeline.

The customers are synthetic and their accept/decline behaviour is simulated.
Everything they touch is production code: the same guardrails, the same
campaign orchestrator, the same merchant gate, the same payment state machine,
the same audit events. Razorpay orders are forced to mock so a batch never
calls the live test API.
"""

from __future__ import annotations

import random
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from backend.audit.logger import log_event
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    MerchantApprovalRequest,
    Proposal,
    VerifyPaymentRequest,
)
from backend.config import get_settings
from backend.services import catalog, checkout
from backend.services.history import get_usual_order

MAX_SESSIONS = 50

HARD_MAX_INR = 5000

# (scenario id, weight, human label)
SCENARIO_WEIGHTS: list[tuple[str, int, str]] = [
    ("returning_usual", 5, "Returning customer reorders the usual"),
    ("new_user_bestseller", 4, "New customer, no history, bestseller seed"),
    ("agent_buyer", 3, "External buyer agent via public API"),
    ("tight_budget", 2, "Budget leaves no room for an add-on"),
    ("attack_invented_addon", 1, "Attack: accept an add-on never offered"),
    ("attack_total_mismatch", 1, "Attack: confirm a total the server never quoted"),
]

MERCHANT_REJECT_EVERY = 6
ADDON_ACCEPT_PROBABILITY = 0.6


def _plan_scenarios(count: int, rng: random.Random) -> list[str]:
    """Spread scenarios proportionally across the batch, attacks always included."""
    total_weight = sum(weight for _, weight, _ in SCENARIO_WEIGHTS)
    plan: list[str] = []
    for scenario_id, weight, _ in SCENARIO_WEIGHTS:
        share = round(count * weight / total_weight)
        if scenario_id.startswith("attack_") and count >= 4:
            share = max(1, share)
        plan.extend([scenario_id] * share)

    while len(plan) < count:
        plan.append("returning_usual")
    while len(plan) > count:
        droppable = [s for s in plan if not s.startswith("attack_")]
        if not droppable:
            break
        plan.remove(Counter(droppable).most_common(1)[0][0])

    rng.shuffle(plan)
    return plan


def _http_detail(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        return {"status": exc.status_code, **detail}
    return {"status": exc.status_code, "message": str(detail)}


@dataclass
class ReplayContext:
    """Personas derived from live config and catalog, not hardcoded ids."""

    returning_user_id: str
    usual_total_inr: int
    agent_sku: str | None
    agent_sku_price_inr: int


def _build_context() -> ReplayContext:
    returning_user_id = get_settings().demo_user_id
    usual = get_usual_order(returning_user_id)

    # Prefer a SKU that has complement data, so the buyer-agent channel can
    # exercise the growth path rather than always paying the baseline.
    affordable = sorted(
        (
            product
            for product in catalog.list_products()
            if product.stock > 0 and 0 < product.price_inr <= HARD_MAX_INR // 2
        ),
        key=lambda product: (
            0 if product.complements else 1,
            -product.price_inr,
            product.id,
        ),
    )
    agent_pick = affordable[0] if affordable else None

    return ReplayContext(
        returning_user_id=returning_user_id,
        usual_total_inr=int(usual.total_inr or 0),
        agent_sku=agent_pick.id if agent_pick else None,
        agent_sku_price_inr=agent_pick.price_inr if agent_pick else 0,
    )


def _clamp_budget(value: int) -> int:
    return max(1, min(int(value), HARD_MAX_INR))


def _build_request(
    scenario: str,
    index: int,
    rng: random.Random,
    ctx: ReplayContext,
) -> CreateProposalRequest:
    session_id = f"replay_{uuid.uuid4().hex[:10]}"

    if scenario == "new_user_bestseller":
        return CreateProposalRequest(
            user_id=f"replay_new_{index:03d}",
            use_usual=True,
            stated_budget_inr=_clamp_budget(rng.choice([1200, 1500, 1800])),
            with_growth=True,
            session_id=session_id,
        )

    if scenario == "agent_buyer" and ctx.agent_sku:
        return CreateProposalRequest(
            user_id=f"replay_agent_{index:03d}",
            product_ids=[ctx.agent_sku],
            stated_budget_inr=_clamp_budget(
                ctx.agent_sku_price_inr + rng.choice([150, 250])
            ),
            with_growth=True,
            session_id=session_id,
            buyer_type="external_agent",
        )

    if scenario == "tight_budget":
        # Budget exactly equal to the cart leaves zero headroom, so the growth
        # engine must decline to offer anything whatever the catalog costs.
        return CreateProposalRequest(
            user_id=ctx.returning_user_id,
            use_usual=True,
            stated_budget_inr=_clamp_budget(ctx.usual_total_inr or 700),
            with_growth=True,
            session_id=session_id,
        )

    # returning_usual and both attack scenarios share the returning-customer
    # setup, with enough headroom for a campaign to be worth evaluating.
    headroom = rng.choice([100, 200, 300])
    return CreateProposalRequest(
        user_id=ctx.returning_user_id,
        use_usual=True,
        stated_budget_inr=_clamp_budget((ctx.usual_total_inr or 699) + headroom),
        with_growth=True,
        session_id=session_id,
    )


def _run_session(
    scenario: str,
    index: int,
    rng: random.Random,
    ctx: ReplayContext,
) -> dict[str, Any]:
    started = time.perf_counter()
    record: dict[str, Any] = {
        "scenario": scenario,
        "index": index,
        "outcome": "unknown",
        "offer_shown": False,
        "merchant_decision": None,
        "addon_decision": None,
        "baseline_inr": 0,
        "paid_inr": 0,
        "uplift_inr": 0,
        "block": None,
    }

    try:
        request = _build_request(scenario, index, rng, ctx)
    except ValidationError as exc:
        record["outcome"] = "blocked"
        record["block"] = {"stage": "request_validation", "message": exc.errors()[0]["msg"]}
        return record

    record["user_id"] = request.user_id
    record["session_id"] = request.session_id
    record["stated_budget_inr"] = request.stated_budget_inr

    try:
        proposal: Proposal = checkout.create_proposal(request)
    except HTTPException as exc:
        record["outcome"] = "blocked"
        record["block"] = {"stage": "proposal_guardrails", **_http_detail(exc)}
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return record

    record["proposal_id"] = proposal.id
    record["baseline_inr"] = proposal.baseline_total_inr or proposal.total_inr
    record["offer_shown"] = bool(proposal.growth_offer)
    if proposal.campaign_decision:
        record["campaign_id"] = proposal.campaign_decision.campaign_id
        record["opportunity"] = proposal.campaign_decision.opportunity

    if proposal.status == "awaiting_merchant_approval":
        is_attack = scenario.startswith("attack_")
        reject = not is_attack and index % MERCHANT_REJECT_EVERY == MERCHANT_REJECT_EVERY - 1
        decision = "reject" if reject else "approve"
        record["merchant_decision"] = decision
        proposal = checkout.decide_merchant_campaign(
            proposal.id,
            MerchantApprovalRequest(decision=decision, note="replay batch"),
        )
        if reject:
            record["offer_shown"] = False

    if proposal.status == "awaiting_addon_decision" and proposal.growth_offer:
        if scenario == "attack_invented_addon":
            try:
                checkout.decide_addon(
                    proposal.id,
                    AddonDecisionRequest(decision="accept", product_id="invented_sku_x"),
                )
                record["outcome"] = "attack_succeeded"
                record["block"] = {"stage": "addon_decision", "message": "NOT BLOCKED"}
                record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                return record
            except HTTPException as exc:
                record["outcome"] = "blocked"
                record["block"] = {"stage": "addon_decision", **_http_detail(exc)}
                record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                return record

        accept = rng.random() < ADDON_ACCEPT_PROBABILITY
        record["addon_decision"] = "accept" if accept else "skip"
        proposal = checkout.decide_addon(
            proposal.id,
            AddonDecisionRequest(
                decision="accept" if accept else "skip",
                product_id=proposal.growth_offer.product_id if accept else None,
            ),
        )

    if scenario == "attack_total_mismatch":
        try:
            checkout.confirm_proposal(
                proposal.id,
                ConfirmationRequest(
                    expected_total_inr=proposal.total_inr + 1,
                    user_id=proposal.user_id,
                    idempotency_key=f"replay_attack_{proposal.id}",
                ),
                force_mock=True,
            )
            record["outcome"] = "attack_succeeded"
            record["block"] = {"stage": "payment_gate", "message": "NOT BLOCKED"}
        except HTTPException as exc:
            record["outcome"] = "blocked"
            record["block"] = {"stage": "payment_gate", **_http_detail(exc)}
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return record

    try:
        confirmed = checkout.confirm_proposal(
            proposal.id,
            ConfirmationRequest(
                expected_total_inr=proposal.total_inr,
                user_id=proposal.user_id,
                idempotency_key=f"replay_{proposal.id}",
            ),
            force_mock=True,
        )
    except HTTPException as exc:
        record["outcome"] = "blocked"
        record["block"] = {"stage": "payment_gate", **_http_detail(exc)}
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return record

    payment = confirmed["payment"]
    verified = checkout.verify_payment(
        VerifyPaymentRequest(
            payment_id=payment.id,
            razorpay_order_id=payment.razorpay_order_id or "",
            razorpay_payment_id=f"pay_replay_{uuid.uuid4().hex[:10]}",
            razorpay_signature="mock_ok_replay",
        )
    )

    paid_proposal: Proposal = verified["proposal"]
    growth = verified.get("growth_summary") or {}
    record["outcome"] = "paid"
    record["paid_inr"] = paid_proposal.total_inr
    record["uplift_inr"] = int(growth.get("realized_paid_uplift") or 0)
    record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return record


def run_replay(*, sessions: int = 25, seed: int | None = 7) -> dict[str, Any]:
    """Run `sessions` synthetic checkouts and return per-session + aggregate results."""
    from backend.services.demo_metrics import compute_growth_metrics

    count = max(1, min(int(sessions), MAX_SESSIONS))
    rng = random.Random(seed)
    plan = _plan_scenarios(count, rng)
    ctx = _build_context()

    batch_id = f"replay_{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    for index, scenario in enumerate(plan):
        record = _run_session(scenario, index, rng, ctx)
        results.append(record)
        log_event(
            "demo_replay_session",
            user_id=record.get("user_id"),
            session_id=record.get("session_id"),
            proposal_id=record.get("proposal_id"),
            payload={"batch_id": batch_id, **record},
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    outcomes: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    for record in results:
        outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
        scenario_counts[record["scenario"]] = scenario_counts.get(record["scenario"], 0) + 1

    paid = [r for r in results if r["outcome"] == "paid"]
    totals = {
        "paid_sessions": len(paid),
        "blocked_sessions": outcomes.get("blocked", 0),
        "attacks_that_succeeded": outcomes.get("attack_succeeded", 0),
        "baseline_gmv_inr": sum(r["baseline_inr"] for r in paid),
        "paid_gmv_inr": sum(r["paid_inr"] for r in paid),
        "realized_uplift_inr": sum(r["uplift_inr"] for r in paid),
    }

    log_event(
        "demo_replay_batch",
        payload={
            "batch_id": batch_id,
            "sessions": count,
            "seed": seed,
            "elapsed_ms": elapsed_ms,
            "outcomes": outcomes,
            "totals": totals,
        },
    )

    return {
        "batch_id": batch_id,
        "sessions_run": len(results),
        "seed": seed,
        "elapsed_ms": elapsed_ms,
        "razorpay_mode": "mock_forced",
        "scenario_labels": {sid: label for sid, _, label in SCENARIO_WEIGHTS},
        "personas": {
            "returning_user_id": ctx.returning_user_id,
            "usual_total_inr": ctx.usual_total_inr,
            "agent_sku": ctx.agent_sku,
        },
        "scenario_counts": scenario_counts,
        "outcome_counts": outcomes,
        "totals": totals,
        "sessions": results,
        "note": (
            "Synthetic customers; accept/decline behaviour is simulated. "
            "Guardrails, campaign orchestration, merchant approval, payment "
            "state machine and audit trail are the real code path. "
            "Razorpay orders are forced to mock."
        ),
        "metrics": compute_growth_metrics(),
    }
