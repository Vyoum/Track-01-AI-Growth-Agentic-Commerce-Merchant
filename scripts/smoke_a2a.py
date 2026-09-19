#!/usr/bin/env python3
"""End-to-end A2A smoke: ACP checkout sessions → audit event."""

from __future__ import annotations

import json
import os
import sys
import tempfile
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

ACP_HEADERS = {
    "API-Version": "2026-04-17",
    "Authorization": "Bearer demo-key",
}


def main() -> None:
    tmp = tempfile.TemporaryDirectory()
    db_path = os.path.join(tmp.name, "a2a_smoke.db")
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": f"sqlite:///{db_path}",
            "USE_MOCK_CATALOG": "true",
            "STORE_PROVIDER": "mock",
            "RAZORPAY_KEY_ID": "",
            "RAZORPAY_KEY_SECRET": "",
        },
    ):
        get_settings.cache_clear()
        reset_store_client()
        init_db()
        client = TestClient(app)

        catalog = client.get("/.well-known/agent-catalog.json")
        assert catalog.status_code == 200
        assert catalog.json()["checkout_protocol"] == "proposal-confirm-v1"

        acp = client.get("/.well-known/acp.json")
        assert acp.status_code == 200
        assert acp.json()["instant_checkout_partner"] is False
        print("acp discovery ok")

        goal = {"want": "protein", "budget_inr": 800, "user_id": "external_buyer_01"}
        products = client.get("/api/products", params={"q": "protein"}).json()["products"]
        chosen = pick_product(products, goal)
        assert chosen and chosen["id"] == "prod_protein_bundle"
        print("buyer catalog pick ok:", chosen["name"])

        created = client.post(
            "/checkout_sessions",
            headers={**ACP_HEADERS, "Idempotency-Key": f"smoke-{uuid.uuid4().hex[:8]}"},
            json={
                "line_items": [{"id": chosen["id"], "quantity": 1}],
                "stated_budget_inr": goal["budget_inr"],
                "buyer": {"id": goal["user_id"]},
                "with_growth": True,
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        sid = session["id"]

        def resolve() -> None:
            nonlocal session
            codes = {m.get("code") for m in session.get("messages") or []}
            actions = {}
            if "merchant_approval_required" in codes:
                actions["campaign"] = "approve"
            if "addon_decision_required" in codes:
                offer = next(
                    (
                        m.get("addon") or {}
                        for m in session.get("messages") or []
                        if m.get("code") == "addon_decision_required"
                    ),
                    {},
                )
                actions["addon"] = (
                    "accept"
                    if should_accept_addon(offer, goal, max_addon_inr=150)
                    else "skip"
                )
            if actions:
                upd = client.post(
                    f"/checkout_sessions/{sid}",
                    headers=ACP_HEADERS,
                    json={"buyer_actions": actions},
                )
                assert upd.status_code == 200, upd.text
                session = upd.json()
                print("updated", actions, "→", session["status"])

        if session["status"] == "needs_input":
            resolve()
            if session["status"] == "needs_input":
                resolve()

        assert session["status"] == "ready_for_complete", session
        total = session["totals"]["total_inr"]
        assert total >= 699, total

        complete = client.post(
            f"/checkout_sessions/{sid}/complete",
            headers={**ACP_HEADERS, "Idempotency-Key": f"smoke-pay-{sid}"},
            json={
                "payment_data": {
                    "instrument": {"type": "spt_demo", "token": "spt_demo_smoke"}
                }
            },
        )
        assert complete.status_code == 200, complete.text
        done = complete.json()
        assert done["status"] == "completed"
        print("acp complete ok")

        events = list_events(limit=200)
        a2a = [e for e in events if e.get("event_type") == "a2a_purchase_completed"]
        assert a2a, "missing a2a_purchase_completed"
        last = a2a[-1]["payload"]
        assert last["buyer_type"] == "external_agent"
        assert last["total_inr"] == total
        print("audit a2a_purchase_completed ok")

        summary = client.get("/api/a2a/summary").json()
        assert summary["external_agent_purchases"] >= 1
        print("a2a summary ok:", summary["external_agent_purchases"], "purchases")

    get_settings.cache_clear()
    reset_store_client()
    tmp.cleanup()
    print(json.dumps({"status": "a2a_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
