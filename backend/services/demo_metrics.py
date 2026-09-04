"""Growth funnel + money-safety aggregates derived from the audit trail.

Read-only: every number here is a fold over events that the checkout core
already emits. No separate counters to drift out of sync.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from backend.db import connect

MAX_EVENTS = 20000

TRACKED_EVENT_TYPES = (
    "proposal_created",
    "proposal_rejected",
    "campaign_proposed",
    "campaign_merchant_approved",
    "campaign_merchant_rejected",
    "campaign_template_applied",
    "campaign_template_paused",
    "growth_offer_shown",
    "growth_offer_none",
    "addon_accepted",
    "addon_skipped",
    "addon_rejected_by_guardrail",
    "payment_confirmation_received",
    "payment_order_created",
    "payment_verified_paid",
    "payment_idempotent_replay",
    "payment_signature_invalid",
    "payment_failed",
    "razorpay_order_create_failed",
    "a2a_purchase_completed",
    "demo_replay_session",
)


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 1)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_tracked_events(limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in TRACKED_EVENT_TYPES)
    sql = (
        "SELECT id, ts, session_id, user_id, event_type, payload_json "
        f"FROM audit_events WHERE event_type IN ({placeholders}) "
        "ORDER BY id ASC LIMIT ?"
    )
    with connect() as conn:
        rows = conn.execute(sql, (*TRACKED_EVENT_TYPES, limit)).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) or {}
        except (TypeError, ValueError):
            payload = {}
        events.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "event_type": row["event_type"],
                "payload": payload,
            }
        )
    return events


def compute_growth_metrics(limit: int = MAX_EVENTS) -> dict[str, Any]:
    events = load_tracked_events(limit=limit)
    counts = Counter(e["event_type"] for e in events)

    confirmed_proposal_ids: set[str] = set()
    paid_gmv = 0
    realized_uplift = 0
    projected_uplift = 0
    direct_offers = 0
    paid_amounts: list[int] = []
    unauthorized: list[str] = []
    channel_paid: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    scenario_outcomes: dict[str, Counter[str]] = {}
    recent_paid: list[dict[str, Any]] = []

    for event in events:
        payload = event["payload"]
        etype = event["event_type"]
        proposal_id = payload.get("proposal_id")

        if etype == "payment_confirmation_received" and proposal_id:
            confirmed_proposal_ids.add(proposal_id)

        elif etype == "growth_offer_shown":
            projected_uplift += _int(payload.get("offer", {}).get("uplift_amount_inr"))
            if not payload.get("pending_merchant_approval"):
                direct_offers += 1

        elif etype == "payment_verified_paid":
            amount = _int(payload.get("amount_inr"))
            uplift = _int(payload.get("realized_paid_uplift"))
            paid_gmv += amount
            realized_uplift += uplift
            paid_amounts.append(amount)
            channel_paid[payload.get("buyer_type") or "human"] += 1
            if proposal_id and proposal_id not in confirmed_proposal_ids:
                unauthorized.append(proposal_id)
            recent_paid.append(
                {
                    "ts": event["ts"],
                    "proposal_id": proposal_id,
                    "amount_inr": amount,
                    "realized_uplift_inr": uplift,
                    "buyer_type": payload.get("buyer_type") or "human",
                    "mock": bool(payload.get("mock")),
                }
            )

        elif etype == "demo_replay_session":
            scenario = str(payload.get("scenario") or "unknown")
            outcome = str(payload.get("outcome") or "unknown")
            scenario_counts[scenario] += 1
            scenario_outcomes.setdefault(scenario, Counter())[outcome] += 1

    accepted = counts["addon_accepted"]
    declined = counts["addon_skipped"]
    offers_shown = counts["growth_offer_shown"]
    paid_orders = counts["payment_verified_paid"]

    guardrail_blocks = (
        counts["proposal_rejected"]
        + counts["addon_rejected_by_guardrail"]
        + counts["campaign_merchant_rejected"]
        + counts["payment_signature_invalid"]
    )
    money_actions = counts["payment_order_created"]

    funnel = {
        "proposals_created": counts["proposal_created"],
        "proposals_blocked_by_guardrail": counts["proposal_rejected"],
        "campaigns_proposed": counts["campaign_proposed"],
        "merchant_approved": counts["campaign_merchant_approved"]
        + counts["campaign_template_applied"],
        "merchant_rejected": counts["campaign_merchant_rejected"],
        "offers_shown": offers_shown,
        # An offer only reaches the customer after an enabled template applies,
        # a leftover per-checkout approve, or a direct add-on with no campaign.
        "offers_reaching_customer": direct_offers
        + counts["campaign_merchant_approved"]
        + counts["campaign_template_applied"],
        "offers_with_no_eligible_addon": counts["growth_offer_none"],
        "offers_accepted": accepted,
        "offers_declined": declined,
        "offers_blocked": counts["addon_rejected_by_guardrail"]
        + counts["campaign_merchant_rejected"],
        "acceptance_rate_pct": _pct(accepted, accepted + declined),
        "paid_orders": paid_orders,
    }

    baseline_gmv = max(0, paid_gmv - realized_uplift)
    revenue = {
        "baseline_gmv_inr": baseline_gmv,
        "paid_gmv_inr": paid_gmv,
        "realized_uplift_inr": realized_uplift,
        "projected_uplift_inr": projected_uplift,
        "uplift_pct_of_baseline": _pct(realized_uplift, baseline_gmv),
        "avg_order_value_inr": (
            round(paid_gmv / len(paid_amounts)) if paid_amounts else 0
        ),
        "avg_uplift_per_paid_order_inr": (
            round(realized_uplift / len(paid_amounts)) if paid_amounts else 0
        ),
    }

    safety = {
        "money_actions": money_actions,
        "explicit_confirmations": counts["payment_confirmation_received"],
        "unauthorized_charges": len(unauthorized),
        "explicitly_gated_pct": (
            100.0 if money_actions and not unauthorized else _pct(0, money_actions)
        ),
        "duplicate_payments_prevented": counts["payment_idempotent_replay"],
        "guardrail_blocks": guardrail_blocks,
        "invalid_signatures_rejected": counts["payment_signature_invalid"],
        "merchant_rejections": counts["campaign_merchant_rejected"],
        "payment_failures_surfaced": counts["payment_failed"]
        + counts["razorpay_order_create_failed"],
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_scanned": len(events),
        "funnel": funnel,
        "revenue": revenue,
        "safety": safety,
        "channels": {
            "paid_by_human": channel_paid.get("human", 0),
            "paid_by_external_agent": channel_paid.get("external_agent", 0),
            "a2a_purchases_completed": counts["a2a_purchase_completed"],
        },
        "scenarios": {
            name: {"sessions": total, "outcomes": dict(scenario_outcomes.get(name, {}))}
            for name, total in scenario_counts.most_common()
        },
        "recent_paid": list(reversed(recent_paid[-8:])),
    }
