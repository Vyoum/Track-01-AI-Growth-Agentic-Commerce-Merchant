"""ACP checkout-session adapter over the existing money core."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.integrations.razorpay_client import reset_razorpay_client
from backend.main import app
from backend.services.data_loader import reload_demo_data

ACP_HEADERS = {
    "API-Version": "2026-04-17",
    "Authorization": "Bearer demo-key",
}


class AcpCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "acp.db")
        self._env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": f"sqlite:///{db_path}",
                "USE_MOCK_CATALOG": "true",
                "STORE_PROVIDER": "mock",
                "RAZORPAY_KEY_ID": "",
                "RAZORPAY_KEY_SECRET": "",
            },
        )
        self._env.start()
        get_settings.cache_clear()
        reset_store_client()
        reset_razorpay_client()
        reload_demo_data()
        init_db()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        reset_razorpay_client()
        reset_store_client()
        get_settings.cache_clear()
        self._env.stop()
        self._tmp.cleanup()

    def test_missing_api_version(self) -> None:
        response = self.client.post(
            "/checkout_sessions",
            json={"line_items": [{"id": "prod_protein_bundle"}]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "missing_api_version")

    def test_discovery(self) -> None:
        response = self.client.get("/.well-known/acp.json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["api_version"], "2026-04-17")
        self.assertFalse(body["instant_checkout_partner"])

    def test_full_session_complete_mock(self) -> None:
        created = self.client.post(
            "/checkout_sessions",
            headers={**ACP_HEADERS, "Idempotency-Key": f"t-{uuid.uuid4().hex[:8]}"},
            json={
                "line_items": [{"id": "prod_protein_bundle", "quantity": 1}],
                "stated_budget_inr": 800,
                "buyer": {"id": "acp_buyer_01"},
                "with_growth": True,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        session = created.json()
        sid = session["id"]
        self.assertTrue(sid.startswith("cs_"))

        if session["status"] == "needs_input":
            actions = {}
            codes = {m["code"] for m in session.get("messages") or []}
            if "merchant_approval_required" in codes:
                actions["campaign"] = "approve"
            if "addon_decision_required" in codes:
                actions["addon"] = "skip"
            if actions:
                updated = self.client.post(
                    f"/checkout_sessions/{sid}",
                    headers=ACP_HEADERS,
                    json={"buyer_actions": actions},
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                session = updated.json()
                if session["status"] == "needs_input":
                    updated = self.client.post(
                        f"/checkout_sessions/{sid}",
                        headers=ACP_HEADERS,
                        json={"buyer_actions": {"addon": "skip"}},
                    )
                    session = updated.json()

        self.assertEqual(session["status"], "ready_for_complete", session)

        complete = self.client.post(
            f"/checkout_sessions/{sid}/complete",
            headers={**ACP_HEADERS, "Idempotency-Key": f"pay-{sid}"},
            json={
                "payment_data": {
                    "instrument": {"type": "spt_demo", "token": "spt_demo_test"}
                }
            },
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        done = complete.json()
        self.assertEqual(done["status"], "completed")
        self.assertGreaterEqual(done["totals"]["total_inr"], 699)
        self.assertEqual(done["order"]["mock"], True)

    def test_complete_without_demo_token(self) -> None:
        created = self.client.post(
            "/checkout_sessions",
            headers=ACP_HEADERS,
            json={
                "line_items": [{"id": "prod_protein_bundle", "quantity": 1}],
                "stated_budget_inr": 800,
                "buyer": {"id": "acp_buyer_token"},
                "with_growth": False,
            },
        )
        sid = created.json()["id"]
        session = created.json()
        if session["status"] == "needs_input":
            self.client.post(
                f"/checkout_sessions/{sid}",
                headers=ACP_HEADERS,
                json={"buyer_actions": {"campaign": "approve", "addon": "skip"}},
            )
        bad = self.client.post(
            f"/checkout_sessions/{sid}/complete",
            headers=ACP_HEADERS,
            json={"payment_data": {"instrument": {"type": "spt", "token": "tok_live"}}},
        )
        self.assertEqual(bad.status_code, 400)

    def test_cancel_session(self) -> None:
        created = self.client.post(
            "/checkout_sessions",
            headers=ACP_HEADERS,
            json={
                "line_items": [{"id": "prod_protein_bundle", "quantity": 1}],
                "stated_budget_inr": 800,
                "buyer": {"id": "acp_buyer_cancel"},
                "with_growth": False,
            },
        )
        sid = created.json()["id"]
        cancelled = self.client.post(
            f"/checkout_sessions/{sid}/cancel",
            headers=ACP_HEADERS,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertIn(cancelled.json()["status"], {"canceled", "failed"})


if __name__ == "__main__":
    unittest.main()
