"""Judge-facing demo endpoints: replay batches and audit-derived metrics."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import MerchantStoreError, reset_store_client
from backend.integrations.razorpay_client import reset_razorpay_client
from backend.main import app
from backend.services import store_source
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
        store_source.reset_source_status()
        reload_demo_data()
        init_db()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self._env.stop()
        get_settings.cache_clear()
        reset_store_client()
        reset_razorpay_client()
        store_source.reset_source_status()
        self._tmp.cleanup()

    def test_health_reports_temporary_mock_fallback(self) -> None:
        class UnavailableMerchant:
            def list_products(self, query="", category=None):
                del query, category
                raise MerchantStoreError("merchant temporarily unavailable")

        with patch.dict(
            os.environ,
            {
                "USE_MOCK_CATALOG": "false",
                "STORE_PROVIDER": "rest",
                "STORE_API_BASE_URL": "https://merchant.invalid",
                "STORE_FALLBACK_TO_MOCK": "true",
            },
        ):
            get_settings.cache_clear()
            reset_store_client()
            store_source.reset_source_status()
            with patch.object(
                store_source,
                "get_store_client",
                return_value=UnavailableMerchant(),
            ):
                products = store_source.list_products()

            self.assertGreater(len(products), 0)
            health = self.client.get("/health").json()
            self.assertEqual(health["configured_catalog_source"], "merchant_api")
            self.assertEqual(health["catalog_source"], "mock_json")
            self.assertTrue(health["catalog_fallback_active"])
            self.assertIn("temporary mock data", health["catalog_fallback_reason"])

            class RecoveredMerchant:
                def list_products(self, query="", category=None):
                    del query, category
                    return products

            with patch.object(
                store_source,
                "get_store_client",
                return_value=RecoveredMerchant(),
            ):
                store_source.list_products()

            recovered_health = self.client.get("/health").json()
            self.assertEqual(recovered_health["catalog_source"], "merchant_api")
            self.assertFalse(recovered_health["catalog_fallback_active"])
            self.assertIsNone(recovered_health["catalog_fallback_reason"])

    def test_merchant_name_tracks_live_catalog_configuration(self) -> None:
        with patch.dict(os.environ, {"USE_MOCK_CATALOG": "true"}):
            get_settings.cache_clear()
            self.assertEqual(self.client.get("/api/meta").json()["merchant"], "Demo Fitness Store")

        with patch.dict(
            os.environ,
            {
                "USE_MOCK_CATALOG": "false",
                "STORE_PROVIDER": "supabase",
                "SUPABASE_URL": "https://merchant.supabase.co",
                "SUPABASE_KEY": "test-merchant-key",
            },
        ):
            get_settings.cache_clear()
            store_source.reset_source_status()
            self.assertEqual(self.client.get("/api/meta").json()["merchant"], "Live Ethnic Store")
            manifest = self.client.get("/.well-known/agent-catalog.json").json()
            self.assertEqual(manifest["merchant"], "Live Ethnic Store")

    def test_metrics_are_zeroed_on_a_fresh_database(self) -> None:
        body = self.client.get("/api/metrics/growth").json()

        self.assertEqual(body["funnel"]["proposals_created"], 0)
        self.assertEqual(body["revenue"]["paid_gmv_inr"], 0)
        self.assertEqual(body["safety"]["unauthorized_charges"], 0)

    def test_chat_exposes_groq_tool_provenance_in_response_and_audit(self) -> None:
        session_id = "judge_provenance_groq"
        simulated = {
            "reply": "I found matching products.",
            "tool_used": "groq",
            "tool_trace": ["search_products", "create_proposal_from_products"],
        }
        with patch(
            "backend.agent.orchestrator._run_groq_loop",
            return_value=simulated,
        ):
            response = self.client.post(
                "/api/chat",
                json={
                    "message": "Find me something useful under ₹800",
                    "session_id": session_id,
                    "user_id": "judge_provenance_user",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["handled_by"], "groq")
        self.assertEqual(body["tool_trace"], simulated["tool_trace"])
        self.assertTrue(body["model"])

        events = self.client.get(
            "/api/audit", params={"session_id": session_id}
        ).json()["events"]
        reply_event = next(e for e in events if e["event_type"] == "agent_reply")
        self.assertEqual(reply_event["payload"]["handled_by"], "groq")
        self.assertEqual(reply_event["payload"]["tool_trace"], simulated["tool_trace"])
        self.assertFalse(reply_event["payload"]["fallback"])

    def test_chat_labels_deterministic_fallback_without_groq(self) -> None:
        with patch.dict(
            os.environ,
            {"GROQ_API_KEY": "", "LLM_API_KEY": ""},
        ):
            get_settings.cache_clear()
            response = self.client.post(
                "/api/chat",
                json={
                    "message": "Order my usual, under ₹800",
                    "session_id": "judge_provenance_fallback",
                    "user_id": "demo_user_01",
                },
            )

        get_settings.cache_clear()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["handled_by"], "fallback")
        self.assertEqual(body["tool_trace"], ["create_proposal_from_usual"])
        self.assertIsNone(body["model"])
        self.assertIsNotNone(body["proposal"])

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
        self.assertGreater(funnel["offers_reaching_customer"], 0)

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

    def test_enabled_template_skips_per_checkout_merchant_click(self) -> None:
        created = self.client.post(
            "/api/proposals",
            json={
                "user_id": "demo_user_01",
                "use_usual": True,
                "stated_budget_inr": 800,
                "with_growth": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["status"], "awaiting_addon_decision")
        self.assertEqual(
            body["campaign_decision"]["approval_mode"],
            "template_policy",
        )
        pending = self.client.get("/api/merchant/campaigns/pending").json()
        self.assertEqual(pending["count"], 0)

    def test_paused_template_hides_addon_without_a_merchant_queue(self) -> None:
        first = self.client.post(
            "/api/proposals",
            json={
                "user_id": "demo_user_01",
                "use_usual": True,
                "stated_budget_inr": 800,
                "with_growth": True,
            },
        ).json()
        campaign_id = first["campaign_decision"]["campaign_id"]
        paused = self.client.post(
            f"/api/merchant/campaigns/{campaign_id}/policy",
            json={"status": "paused", "note": "test pause"},
        )
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertGreaterEqual(paused.json().get("offers_retracted", 0), 1)

        live = self.client.get(f"/api/proposals/{first['id']}").json()
        self.assertIsNone(live.get("growth_offer"))
        self.assertEqual(live["status"], "awaiting_confirmation")

        second = self.client.post(
            "/api/proposals",
            json={
                "user_id": "demo_user_01",
                "use_usual": True,
                "stated_budget_inr": 800,
                "with_growth": True,
            },
        ).json()
        self.assertEqual(second["status"], "awaiting_confirmation")
        self.assertIsNone(second.get("growth_offer"))
        self.assertEqual(
            (second.get("campaign_decision") or {}).get("merchant_approval_status"),
            "paused",
        )
        pending = self.client.get("/api/merchant/campaigns/pending").json()
        self.assertEqual(pending["count"], 0)


if __name__ == "__main__":
    unittest.main()
