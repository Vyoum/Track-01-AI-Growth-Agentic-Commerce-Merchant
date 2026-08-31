#!/usr/bin/env python3
"""
Independent buyer agent — uses only public merchant HTTP APIs.

No imports from backend/. Discovers checkout via /.well-known/agent-catalog.json.

Usage:
  MERCHANT_BASE_URL=http://127.0.0.1:8000 python buyer_agent/run_purchase.py

Optional env:
  BUYER_WANT=protein
  BUYER_BUDGET_INR=800
  BUYER_USER_ID=external_buyer_01
  BUYER_MAX_ADDON_INR=150
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "buyer_agent"))

from buyer_logic import pick_product, should_accept_addon  # noqa: E402


def _base_url() -> str:
    return (os.environ.get("MERCHANT_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def _goal() -> dict[str, Any]:
    return {
        "want": os.environ.get("BUYER_WANT", "protein"),
        "budget_inr": int(os.environ.get("BUYER_BUDGET_INR", "800")),
        "user_id": os.environ.get("BUYER_USER_ID", "external_buyer_01"),
    }


def _get(client: httpx.Client, path: str, **params: Any) -> Any:
    r = client.get(f"{_base_url()}{path}", params=params or None)
    r.raise_for_status()
    return r.json()


def _post(client: httpx.Client, path: str, body: dict[str, Any]) -> Any:
    r = client.post(f"{_base_url()}{path}", json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed {r.status_code}: {r.text[:400]}")
    return r.json()


def run_purchase(*, auto_merchant_approve: bool = False) -> dict[str, Any]:
    goal = _goal()
    session_id = f"a2a_{uuid.uuid4().hex[:12]}"
    trace: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        manifest = _get(client, "/.well-known/agent-catalog.json")
        trace.append(f"discovered protocol={manifest.get('checkout_protocol')}")

        products_resp = _get(client, "/api/products", q=goal["want"])
        products = products_resp.get("products") or []
        if not products:
            raise RuntimeError(f"No products for query={goal['want']!r}")

        chosen = pick_product(products, goal)
        if not chosen:
            raise RuntimeError("No in-stock product fits buyer goal/budget")

        trace.append(
            f"selected {chosen['id']} ({chosen['name']}) ₹{chosen['price_inr']}"
        )

        proposal = _post(
            client,
            "/api/proposals",
            {
                "user_id": goal["user_id"],
                "product_ids": [chosen["id"]],
                "stated_budget_inr": goal["budget_inr"],
                "with_growth": True,
                "session_id": session_id,
                "buyer_type": "external_agent",
            },
        )
        proposal_id = proposal["id"]
        trace.append(f"proposal {proposal_id} status={proposal['status']}")

        if proposal["status"] == "awaiting_merchant_approval":
            if not auto_merchant_approve:
                raise RuntimeError(
                    "Proposal awaiting merchant campaign approval. "
                    "Approve via Merchant Desk UI or re-run with "
                    "AUTO_MERCHANT_APPROVE=1 (uses public /campaign/decide API)."
                )
            proposal = _post(
                client,
                f"/api/proposals/{proposal_id}/campaign/decide",
                {"decision": "approve", "note": "A2A demo merchant desk simulation"},
            )
            trace.append(f"merchant approved campaign → status={proposal['status']}")

        offer = proposal.get("growth_offer")
        if proposal["status"] == "awaiting_addon_decision" and offer:
            max_addon = int(os.environ.get("BUYER_MAX_ADDON_INR", "150"))
            if should_accept_addon(offer, goal, max_addon_inr=max_addon):
                proposal = _post(
                    client,
                    f"/api/proposals/{proposal_id}/addon",
                    {
                        "decision": "accept",
                        "product_id": offer["product_id"],
                    },
                )
                trace.append(f"accepted add-on {offer['name']} → total ₹{proposal['total_inr']}")
            else:
                proposal = _post(
                    client,
                    f"/api/proposals/{proposal_id}/addon",
                    {"decision": "skip"},
                )
                trace.append(f"skipped add-on → total ₹{proposal['total_inr']}")

        expected_total = int(proposal["total_inr"])
        idempotency_key = f"a2a-{proposal_id}-{uuid.uuid4().hex[:8]}"
        checkout = _post(
            client,
            f"/api/proposals/{proposal_id}/confirm",
            {
                "expected_total_inr": expected_total,
                "user_id": goal["user_id"],
                "idempotency_key": idempotency_key,
            },
        )
        trace.append(f"payment order {checkout['payment']['razorpay_order_id']}")

        payment = checkout["payment"]
        if payment.get("mock"):
            verified = _post(
                client,
                "/api/payments/verify",
                {
                    "payment_id": payment["id"],
                    "razorpay_order_id": payment["razorpay_order_id"],
                    "razorpay_payment_id": f"pay_a2a_{uuid.uuid4().hex[:8]}",
                    "razorpay_signature": "mock_ok_a2a",
                },
            )
        else:
            verified = {
                "note": "Real Razorpay checkout required — complete payment in UI, then verify",
                "checkout": checkout.get("checkout"),
                "payment": payment,
            }

        growth = verified.get("growth_summary") or checkout.get("growth_summary") or {}
        result = {
            "status": "a2a_purchase_completed" if payment.get("mock") else "a2a_payment_pending",
            "proposal_id": proposal_id,
            "total_inr": expected_total,
            "uplift_inr": growth.get("realized_paid_uplift")
            or growth.get("projected_uplift_inr")
            or 0,
            "buyer_type": "external_agent",
            "trace": trace,
            "verified": verified if payment.get("mock") else None,
        }
        return result


def main() -> None:
    auto = os.environ.get("AUTO_MERCHANT_APPROVE", "").lower() in {"1", "true", "yes"}
    try:
        result = run_purchase(auto_merchant_approve=auto)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        sys.exit(1)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
