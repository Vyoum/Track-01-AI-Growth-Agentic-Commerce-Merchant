"""Adversarial checkout tests: races, malformed input, and DB invariants."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import connect, init_db
from backend.integrations.client_store_client import reset_store_client
from backend.integrations.razorpay_client import CreatedOrder, reset_razorpay_client
from backend.main import app
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    PaymentRecord,
    PaymentStatus,
)
from backend.services import checkout, store
from backend.services.data_loader import reload_demo_data


class CountingMockRazorpay:
    public_key_id = None

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def create_order(self, *, amount_inr, receipt, notes=None, force_mock=False):
        del force_mock
        with self._lock:
            self.calls += 1
            call = self.calls
        # Keep the winner in-flight long enough for the competing request to
        # observe payment_pending before the payment row exists.
        time.sleep(0.08)
        order_id = f"order_mock_concurrent_{call}"
        return CreatedOrder(
            order_id=order_id,
            amount_paise=amount_inr * 100,
            currency="INR",
            receipt=receipt,
            mock=True,
            raw={"id": order_id, "notes": notes or {}, "mock": True},
        )


class AdversarialCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "adversarial.db")
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

    def _ready_proposal(self):
        proposal = checkout.create_proposal(
            CreateProposalRequest(
                user_id="adversarial_user",
                product_ids=["prod_protein_bundle"],
                stated_budget_inr=800,
                with_growth=False,
                session_id=f"adversarial_{uuid.uuid4().hex[:8]}",
            )
        )
        self.assertEqual(proposal.status.value, "awaiting_confirmation")
        return proposal

    def _campaign_proposal(self):
        proposal = checkout.create_proposal(
            CreateProposalRequest(
                user_id="adversarial_user",
                product_ids=["prod_protein_bundle"],
                stated_budget_inr=800,
                with_growth=True,
            )
        )
        self.assertEqual(proposal.status.value, "awaiting_addon_decision")
        return proposal

    def test_concurrent_double_confirm_creates_one_razorpay_order(self) -> None:
        proposal = self._ready_proposal()
        fake = CountingMockRazorpay()
        barrier = threading.Barrier(2)

        def confirm(key: str):
            barrier.wait(timeout=2)
            try:
                result = checkout.confirm_proposal(
                    proposal.id,
                    ConfirmationRequest(
                        expected_total_inr=proposal.total_inr,
                        user_id=proposal.user_id,
                        idempotency_key=key,
                    ),
                )
                return ("ok", result["payment"].id)
            except HTTPException as exc:
                return ("error", exc.status_code, exc.detail)

        with patch(
            "backend.integrations.razorpay_client.get_razorpay_client",
            return_value=fake,
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        confirm,
                        ["concurrent-key-a", "concurrent-key-b"],
                    )
                )

        self.assertEqual(fake.calls, 1, results)
        self.assertEqual(sum(r[0] == "ok" for r in results), 1, results)
        loser = next(r for r in results if r[0] == "error")
        self.assertEqual(loser[1], 409)
        self.assertEqual(loser[2]["code"], "payment_in_progress")

        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM payments WHERE proposal_id = ?",
                (proposal.id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_idempotency_key_replays_same_payment(self) -> None:
        proposal = self._ready_proposal()
        body = ConfirmationRequest(
            expected_total_inr=proposal.total_inr,
            user_id=proposal.user_id,
            idempotency_key="same-key",
        )
        first = checkout.confirm_proposal(proposal.id, body)
        second = checkout.confirm_proposal(proposal.id, body)
        self.assertEqual(first["payment"].id, second["payment"].id)
        self.assertTrue(second["idempotent_replay"])

    def test_unique_index_rejects_second_payment_for_proposal(self) -> None:
        proposal = self._ready_proposal()
        now = datetime.now(timezone.utc)

        def payment(suffix: str) -> PaymentRecord:
            return PaymentRecord(
                id=f"pay_{suffix}",
                proposal_id=proposal.id,
                status=PaymentStatus.PENDING,
                amount_inr=proposal.total_inr,
                razorpay_order_id=f"order_{suffix}",
                mock=True,
                created_at=now,
                updated_at=now,
            )

        store.save_payment(payment("first"))
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_payment(payment("second"))

        with connect() as conn:
            index = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = 'ux_payments_proposal_id'
                """
            ).fetchone()
        self.assertIsNotNone(index)
        self.assertIn("UNIQUE INDEX", index["sql"].upper())

    def test_wrong_addon_product_id_is_rejected_without_mutation(self) -> None:
        proposal = self._campaign_proposal()
        original_offer = proposal.growth_offer
        self.assertIsNotNone(original_offer)

        with self.assertRaises(HTTPException) as caught:
            checkout.decide_addon(
                proposal.id,
                AddonDecisionRequest(
                    decision="accept",
                    product_id="invented_product_id",
                ),
            )
        self.assertEqual(caught.exception.status_code, 409)

        unchanged = checkout.get_proposal(proposal.id)
        self.assertEqual(unchanged.status.value, "awaiting_addon_decision")
        self.assertEqual(unchanged.growth_offer.product_id, original_offer.product_id)

    def test_missing_addon_id_safely_uses_server_offer(self) -> None:
        proposal = self._campaign_proposal()
        offer = proposal.growth_offer
        accepted = checkout.decide_addon(
            proposal.id,
            AddonDecisionRequest(decision="accept"),
        )
        self.assertEqual(accepted.status.value, "awaiting_confirmation")
        self.assertEqual(
            accepted.total_inr,
            (proposal.baseline_total_inr or proposal.total_inr) + offer.price_inr,
        )

    def test_invalid_budgets_fail_at_api_boundary(self) -> None:
        for budget in (-1, 0, 5001, 10**12):
            with self.subTest(budget=budget):
                response = self.client.post(
                    "/api/proposals",
                    json={
                        "user_id": "adversarial_user",
                        "product_ids": ["prod_protein_bundle"],
                        "stated_budget_inr": budget,
                        "with_growth": False,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
