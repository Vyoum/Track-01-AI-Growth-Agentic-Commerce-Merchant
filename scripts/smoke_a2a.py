#!/usr/bin/env python3
"""End-to-end A2A smoke: manifest → buyer flow → audit event."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buyer_agent"))

from fastapi.testclient import TestClient

from backend.audit.logger import list_events
from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.main import app
from buyer_logic import pick_product, should_accept_addon


def main() -> None:
    with patch.dict(
        os.environ,
        {
            "USE_MOCK_CATALOG": "true",
            "RAZORPAY_KEY_ID": "",
            "RAZORPAY_KEY_SECRET": "",
        },
    ):
        get_settings.cache_clear()
        reset_store_client()
        init_db()
        client = TestClient(app)

        manifest = client.get("/.well-known/agent-catalog.json")
        assert manifest.status_code == 200
        mbody = manifest.json()
        assert mbody["checkout_protocol"] == "proposal-confirm-v1"
        assert mbody["policies"]["requires_explicit_confirmation"] is True
        print("manifest ok")

        goal = {"want": "protein", "budget_inr": 800, "user_id": "external_buyer_01"}
        products = client.get("/api/products", params={"q": "protein"}).json()["products"]
        chosen = pick_product(products, goal)
        assert chosen and chosen["id"] == "prod_protein_bundle"
        print("buyer catalog pick ok:", chosen["name"])

        session_id = f"a2a_smoke_{uuid.uuid4().hex[:8]}"
        proposal = client.post(
            "/api/proposals",
            json={
                "user_id": goal["user_id"],
                "product_ids": [chosen["id"]],
                "stated_budget_inr": goal["budget_inr"],
                "with_growth": True,
                "session_id": session_id,
                "buyer_type": "external_agent",
            },
        ).json()
        pid = proposal["id"]
        assert proposal["buyer_type"] == "external_agent"

        if proposal["status"] == "awaiting_merchant_approval":
            proposal = client.post(
                f"/api/proposals/{pid}/campaign/decide",
                json={"decision": "approve", "note": "smoke merchant desk"},
            ).json()
            print("merchant campaign approved")

        offer = proposal.get("growth_offer")
        if proposal["status"] == "awaiting_addon_decision" and offer:
            assert should_accept_addon(offer, goal, max_addon_inr=150)
            proposal = client.post(
                f"/api/proposals/{pid}/addon",
                json={"decision": "accept", "product_id": offer["product_id"]},
            ).json()
            print("buyer accepted add-on")

        total = proposal["total_inr"]
        assert total == 798, total

        checkout = client.post(
            f"/api/proposals/{pid}/confirm",
            json={
                "expected_total_inr": total,
                "user_id": goal["user_id"],
                "idempotency_key": f"smoke-a2a-{pid}",
            },
        ).json()
        payment = checkout["payment"]
        assert payment["amount_inr"] == 798

        verified = client.post(
            "/api/payments/verify",
            json={
                "payment_id": payment["id"],
                "razorpay_order_id": payment["razorpay_order_id"],
                "razorpay_payment_id": f"pay_a2a_{uuid.uuid4().hex[:8]}",
                "razorpay_signature": "mock_ok_a2a_smoke",
            },
        ).json()
        assert verified["payment"]["status"] == "paid"
        print("payment verified ok")

        events = list_events(limit=200)
        a2a = [e for e in events if e.get("event_type") == "a2a_purchase_completed"]
        assert a2a, "missing a2a_purchase_completed"
        last = a2a[-1]["payload"]
        assert last["buyer_type"] == "external_agent"
        assert last["total_inr"] == 798
        assert last["uplift_inr"] == 99
        print("audit a2a_purchase_completed ok")

        summary = client.get("/api/a2a/summary").json()
        assert summary["external_agent_purchases"] >= 1
        print("a2a summary ok:", summary["external_agent_purchases"], "purchases")

    get_settings.cache_clear()
    reset_store_client()
    print(json.dumps({"status": "a2a_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
