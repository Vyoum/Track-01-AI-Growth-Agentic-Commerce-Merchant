"""Demo customer book and SQLite seed used by the buildathon walkthrough."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.models import CreateProposalRequest
from backend.services import checkout
from backend.services.data_loader import load_demo_users, reload_demo_data
from backend.services.demo_metrics import compute_growth_metrics
from backend.services.demo_seed import export_sqlite_csv, seed_demo_database
from backend.services.history import get_usual_order


class DemoSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "checkout_agent.db"
        self._env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": f"sqlite:///{self.db_path}",
                "USE_MOCK_CATALOG": "true",
                "STORE_PROVIDER": "mock",
                "GROQ_API_KEY": "",
                "LLM_API_KEY": "",
                "RAZORPAY_KEY_ID": "",
                "RAZORPAY_KEY_SECRET": "",
            },
        )
        self._env.start()
        get_settings.cache_clear()
        reload_demo_data()
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._env.stop()
        get_settings.cache_clear()
        reload_demo_data()
        self._tmp.cleanup()

    def test_customer_book_and_model_badge_payload(self) -> None:
        from backend.main import app

        users = load_demo_users()["users"]
        self.assertGreaterEqual(len(users), 10)
        self.assertLessEqual(len(users), 15)
        emails = {user["email"] for user in users}
        self.assertIn("aisha.khan@example.com", emails)
        self.assertTrue(all(user.get("city") and user.get("signup_date") for user in users))

        usual = get_usual_order("demo_user_01")
        self.assertEqual(usual.order_id, "ord_demo_003")
        self.assertEqual(usual.total_inr, 699)
        self.assertEqual(usual.items[0].product_id, "prod_protein_bundle")
        self.assertEqual(get_usual_order("demo_user_12").source, "none")
        self.assertTrue(get_usual_order("demo_user_02").items)

        proposal = checkout.create_proposal(
            CreateProposalRequest(
                user_id="demo_user_01",
                use_usual=True,
                stated_budget_inr=800,
                with_growth=True,
                session_id="hero_check",
            )
        )
        self.assertEqual(proposal.baseline_total_inr, 699)
        self.assertIsNotNone(proposal.growth_offer)
        self.assertEqual(proposal.growth_offer.product_id, "prod_shaker")
        self.assertEqual(proposal.growth_offer.projected_total_inr, 798)

        with TestClient(app) as client:
            meta = client.get("/api/meta").json()
            llm = meta["llm"]
            self.assertEqual(llm["mode"], "demo_mock")
            self.assertFalse(llm["configured"])
            self.assertIsNone(llm["model"])
            self.assertIn("fallback", llm["label"].lower())
            self.assertIn(llm["configured_model"], llm["label"])
            self.assertNotIn("api_key", llm)

            book = client.get("/api/demo/customers").json()
            self.assertEqual(book["demo_user_id"], "demo_user_01")
            self.assertGreaterEqual(len(book["customers"]), 10)
            aisha = next(c for c in book["customers"] if c["user_id"] == "demo_user_01")
            self.assertEqual(aisha["city"], "Mumbai")
            self.assertEqual(aisha["usual_total_inr"], 699)
            ishaan = next(c for c in book["customers"] if c["user_id"] == "demo_user_12")
            self.assertEqual(ishaan["completed_orders"], 0)

    def test_seed_database_loads_metrics_mandates_and_csv(self) -> None:
        summary = seed_demo_database(self.db_path, force=True)
        self.assertFalse(summary["skipped"])
        self.assertGreaterEqual(summary["customers"], 10)
        self.assertGreaterEqual(summary["conversations"], summary["customers"])
        self.assertGreater(summary["spend_mandates"], summary["customers"])
        self.assertGreater(summary["paid_payments"], 0)
        self.assertGreater(summary["proposals"], summary["paid_payments"])

        metrics = compute_growth_metrics()
        self.assertGreater(metrics["funnel"]["paid_orders"], 0)
        self.assertGreater(metrics["revenue"]["paid_gmv_inr"], 0)
        self.assertGreater(metrics["revenue"]["realized_uplift_inr"], 0)
        self.assertEqual(metrics["safety"]["unauthorized_charges"], 0)
        self.assertEqual(
            metrics["revenue"]["paid_gmv_inr"],
            metrics["revenue"]["baseline_gmv_inr"] + metrics["revenue"]["realized_uplift_inr"],
        )

        again = seed_demo_database(self.db_path, force=False)
        self.assertTrue(again["skipped"])
        self.assertEqual(again["paid_payments"], summary["paid_payments"])

        csv_dir = Path(self._tmp.name) / "csv"
        written = export_sqlite_csv(self.db_path, csv_dir)
        self.assertIn("customers.csv", written)
        self.assertIn("spend_mandates.csv", written)
        self.assertIn("audit_events.csv", written)
        self.assertIn("conversations.csv", written)
        customers_csv = (csv_dir / "customers.csv").read_text(encoding="utf-8")
        self.assertIn("aisha.khan@example.com", customers_csv)
        self.assertIn("Vikram Singh", customers_csv)
