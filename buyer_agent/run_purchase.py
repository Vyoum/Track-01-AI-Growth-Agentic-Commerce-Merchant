#!/usr/bin/env python3
"""
Independent buyer agent — ACP checkout sessions over public HTTP.

Discovers GET /.well-known/acp.json then:
  POST /checkout_sessions → update (addon/campaign) → complete (spt_demo).

Does not import backend/. Same money gates as humans.

Usage:
  MERCHANT_BASE_URL=http://127.0.0.1:8000 python buyer_agent/run_purchase.py

Optional:
  BUYER_WANT, BUYER_BUDGET_INR, BUYER_USER_ID, BUYER_MAX_ADDON_INR
  AUTO_MERCHANT_APPROVE=1
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

ACP_VERSION = "2026-04-17"


def _base_url() -> str:
    return (os.environ.get("MERCHANT_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def _goal() -> dict[str, Any]:
    return {
        "want": os.environ.get("BUYER_WANT", "protein"),
        "budget_inr": int(os.environ.get("BUYER_BUDGET_INR", "800")),
        "user_id": os.environ.get("BUYER_USER_ID", "external_buyer_01"),
    }


def _headers(*, idempotency: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "API-Version": ACP_VERSION,
        "Authorization": "Bearer demo-key",
    }
    if idempotency:
        headers["Idempotency-Key"] = idempotency
    return headers


def run_purchase(*, auto_merchant_approve: bool = False) -> dict[str, Any]:
    goal = _goal()
    trace: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        acp = client.get(f"{_base_url()}/.well-known/acp.json")
        acp.raise_for_status()
        manifest = acp.json()
        trace.append(
            f"discovered acp api_version={manifest.get('api_version')} "
            f"instant_checkout_partner={manifest.get('instant_checkout_partner')}"
        )

        products_resp = client.get(
            f"{_base_url()}/api/products", params={"q": goal["want"]}
        )
        products_resp.raise_for_status()
        products = products_resp.json().get("products") or []
        if not products:
            raise RuntimeError(f"No products for query={goal['want']!r}")

        chosen = pick_product(products, goal)
        if not chosen:
            raise RuntimeError("No in-stock product fits buyer goal/budget")
        trace.append(
            f"selected {chosen['id']} ({chosen['name']}) ₹{chosen['price_inr']}"
        )

        create = client.post(
            f"{_base_url()}/checkout_sessions",
            headers=_headers(idempotency=f"acp-create-{uuid.uuid4().hex[:10]}"),
            json={
                "currency": "inr",
                "line_items": [{"id": chosen["id"], "quantity": 1}],
                "stated_budget_inr": goal["budget_inr"],
                "buyer": {"id": goal["user_id"]},
                "with_growth": True,
            },
        )
        if create.status_code >= 400:
            raise RuntimeError(f"create session failed {create.status_code}: {create.text[:400]}")
        session = create.json()
        sid = session["id"]
        trace.append(f"session {sid} status={session.get('status')}")

        def refresh() -> dict[str, Any]:
            r = client.get(
                f"{_base_url()}/checkout_sessions/{sid}",
                headers=_headers(),
            )
            r.raise_for_status()
            return r.json()

        if session.get("status") == "needs_input":
            codes = {m.get("code") for m in session.get("messages") or []}
            actions: dict[str, str] = {}
            if "merchant_approval_required" in codes:
                if not auto_merchant_approve:
                    raise RuntimeError(
                        "Session needs merchant approval. AUTO_MERCHANT_APPROVE=1"
                    )
                actions["campaign"] = "approve"
            if "addon_decision_required" in codes:
                addon_msg = next(
                    (m for m in session["messages"] if m.get("code") == "addon_decision_required"),
                    {},
                )
                offer = addon_msg.get("addon") or {}
                max_addon = int(os.environ.get("BUYER_MAX_ADDON_INR", "150"))
                fake_offer = {
                    "price_inr": offer.get("price_inr"),
                    "projected_total_inr": offer.get("projected_total_inr"),
                }
                actions["addon"] = (
                    "accept"
                    if should_accept_addon(fake_offer, goal, max_addon_inr=max_addon)
                    else "skip"
                )
            if actions:
                upd = client.post(
                    f"{_base_url()}/checkout_sessions/{sid}",
                    headers=_headers(idempotency=f"acp-upd-{uuid.uuid4().hex[:8]}"),
                    json={"buyer_actions": actions},
                )
                if upd.status_code >= 400:
                    raise RuntimeError(f"update failed {upd.status_code}: {upd.text[:400]}")
                session = upd.json()
                trace.append(
                    f"updated actions={actions} → status={session.get('status')} "
                    f"total_inr={session.get('totals', {}).get('total_inr')}"
                )

            session = refresh()
            if session.get("status") == "needs_input":
                codes = {m.get("code") for m in session.get("messages") or []}
                if "addon_decision_required" in codes and "addon" not in actions:
                    addon_msg = next(
                        (
                            m
                            for m in session["messages"]
                            if m.get("code") == "addon_decision_required"
                        ),
                        {},
                    )
                    offer = addon_msg.get("addon") or {}
                    max_addon = int(os.environ.get("BUYER_MAX_ADDON_INR", "150"))
                    fake_offer = {
                        "price_inr": offer.get("price_inr"),
                        "projected_total_inr": offer.get("projected_total_inr"),
                    }
                    upd = client.post(
                        f"{_base_url()}/checkout_sessions/{sid}",
                        headers=_headers(),
                        json={
                            "buyer_actions": {
                                "addon": "accept"
                                if should_accept_addon(
                                    fake_offer, goal, max_addon_inr=max_addon
                                )
                                else "skip"
                            }
                        },
                    )
                    upd.raise_for_status()
                    session = upd.json()
                    trace.append(f"addon resolved → status={session.get('status')}")

        complete_body = {
            "payment_data": {
                "instrument": {"type": "spt_demo", "token": "spt_demo_a2a"},
            }
        }
        complete = client.post(
            f"{_base_url()}/checkout_sessions/{sid}/complete",
            headers=_headers(idempotency=f"acp-pay-{sid}"),
            json=complete_body,
        )
        if complete.status_code == 409:
            detail = complete.json().get("detail") or {}
            if detail.get("code") == "MANDATE_CAP_WARNING":
                trace.append("impulse warning — completing with ack")
                complete_body["acknowledge_impulse_warning"] = True
                complete = client.post(
                    f"{_base_url()}/checkout_sessions/{sid}/complete",
                    headers=_headers(idempotency=f"acp-pay-ack-{sid}"),
                    json=complete_body,
                )
        if complete.status_code >= 400:
            raise RuntimeError(
                f"complete failed {complete.status_code}: {complete.text[:400]}"
            )
        session = complete.json()
        total = (session.get("totals") or {}).get("total_inr")
        trace.append(f"complete status={session.get('status')} total ₹{total}")

        paid = session.get("status") == "completed"
        return {
            "status": "a2a_purchase_completed" if paid else session.get("status"),
            "checkout_session_id": sid,
            "proposal_id": session.get("proposal_id"),
            "total_inr": total,
            "buyer_type": "external_agent",
            "protocol": "acp_adapter",
            "trace": trace,
            "session": session,
        }


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
