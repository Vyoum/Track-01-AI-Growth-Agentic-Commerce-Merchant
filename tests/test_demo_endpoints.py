"""Judge-facing demo endpoints: replay batches and audit-derived metrics."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.integrations.razorpay_client import reset_razorpay_client
from backend.main import app
from backend.services.data_loader import reload_demo_data
from backend.services.demo_replay import _plan_scenarios


class DemoEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "demo.db")
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
        self._env.stop()
        get_settings.cache_clear()
        reset_store_client()
        reset_razorpay_client()
        self._tmp.cleanup()

    def test_metrics_are_zeroed_on_a_fresh_database(self) -> None:
        body = self.client.get("/api/metrics/growth").json()

        self.assertEqual(body["funnel"]["proposals_created"], 0)
        self.assertEqual(body["revenue"]["paid_gmv_inr"], 0)
        self.assertEqual(body["safety"]["unauthorized_charges"], 0)

    def test_every_batch_includes_both_attack_scenarios(self) -> None:
        import random

        for count in (4, 10, 25, 50):
            plan = _plan_scenarios(count, random.Random(7))
            self.assertEqual(len(plan), count, f"count={count}")
            self.assertIn("attack_invented_addon", plan, f"count={count}")
            self.assertIn("attack_total_mismatch", plan, f"count={count}")

    def test_replay_pays_orders_and_blocks_every_attack(self) -> None:
        body = self.client.post(
            "/api/demo/replay", json={"sessions": 10, "seed": 7}
        ).json()

        self.assertEqual(body["sessions_run"], 10)
        self.assertEqual(body["razorpay_mode"], "mock_forced")
        self.assertGreater(body["totals"]["paid_sessions"], 0)
        self.assertGreater(body["totals"]["blocked_sessions"], 0)
        self.assertEqual(body["totals"]["attacks_that_succeeded"], 0)

        # Every attack session must carry the rejection that stopped it.
        for session in body["sessions"]:
            if session["scenario"].startswith("attack_"):
                self.assertEqual(session["outcome"], "blocked", session)
                self.assertIn(session["block"]["status"], (400, 409, 422), session)

    def test_replay_moves_the_audit_derived_metrics(self) -> None:
        self.client.post("/api/demo/replay", json={"sessions": 10, "seed": 7})
        body = self.client.get("/api/metrics/growth").json()

        funnel = body["funnel"]
        revenue = body["revenue"]
        safety = body["safety"]

        self.assertEqual(funnel["proposals_created"], 10)
        self.assertEqual(funnel["paid_orders"], funnel["proposals_created"] - 2)
        self.assertGreater(revenue["paid_gmv_inr"], 0)
        self.assertEqual(
            revenue["paid_gmv_inr"],
            revenue["baseline_gmv_inr"] + revenue["realized_uplift_inr"],
        )

        # The safety invariant the scoreboard advertises.
        self.assertEqual(safety["unauthorized_charges"], 0)
        self.assertEqual(safety["explicitly_gated_pct"], 100.0)
        self.assertGreater(safety["guardrail_blocks"], 0)

    def test_replay_is_deterministic_for_a_fixed_seed(self) -> None:
        first = self.client.post(
            "/api/demo/replay", json={"sessions": 10, "seed": 3}
        ).json()
        second = self.client.post(
            "/api/demo/replay", json={"sessions": 10, "seed": 3}
        ).json()

        self.assertEqual(first["scenario_counts"], second["scenario_counts"])
        self.assertEqual(first["outcome_counts"], second["outcome_counts"])
        self.assertEqual(
            first["totals"]["realized_uplift_inr"],
            second["totals"]["realized_uplift_inr"],
        )

    def test_replay_batch_size_is_bounded(self) -> None:
        self.assertEqual(
            self.client.post("/api/demo/replay", json={"sessions": 5000}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/demo/replay", json={"sessions": 0}).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
