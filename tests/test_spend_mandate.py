"""Persistent spend mandate: warn, allow bypass, record only on verified pay."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.integrations.razorpay_client import reset_razorpay_client
from backend.main import app
from backend.models import (
    ConfirmationRequest,
    CreateProposalRequest,
    FailPaymentRequest,
    PaymentStatus,
    VerifyPaymentRequest,
)
from backend.services import checkout, spend_mandate
from backend.services.data_loader import reload_demo_data


class SpendMandateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "mandate.db")
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
        self.user_id = f"mandate_{uuid.uuid4().hex[:8]}"
        self.session_id = f"sess_{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        self.client.close()
        reset_razorpay_client()
        reset_store_client()
        get_settings.cache_clear()
        self._env.stop()
        self._tmp.cleanup()

    def _proposal(self, *, session_id: str | None = None):
        proposal = checkout.create_proposal(
            CreateProposalRequest(
                user_id=self.user_id,
                product_ids=["prod_protein_bundle"],
                stated_budget_inr=800,
                with_growth=False,
                session_id=session_id or self.session_id,
            )
        )
        self.assertEqual(proposal.status.value, "awaiting_confirmation")
        return proposal

    def _confirm(self, proposal, *, ack: bool = False, force_mock: bool = False):
        return checkout.confirm_proposal(
            proposal.id,
            ConfirmationRequest(
                expected_total_inr=proposal.total_inr,
                user_id=proposal.user_id,
                idempotency_key=f"m-{proposal.id}-{uuid.uuid4().hex[:6]}",
                acknowledge_impulse_warning=ack,
            ),
            force_mock=force_mock,
        )

    def _verify(self, confirmed):
        payment = confirmed["payment"]
        return checkout.verify_payment(
            VerifyPaymentRequest(
                payment_id=payment.id,
                razorpay_order_id=payment.razorpay_order_id,
                razorpay_payment_id=f"pay_{uuid.uuid4().hex[:8]}",
                razorpay_signature="mock_ok_mandate",
            )
        )

    def _pay(self, *, ack: bool = False):
        proposal = self._proposal()
        confirmed = self._confirm(proposal, ack=ack)
        return self._verify(confirmed)

    def test_warning_then_ack_continues(self) -> None:
        self._pay()
        self._pay()
        third = self._proposal()
        with self.assertRaises(HTTPException) as raised:
            self._confirm(third)
        self.assertEqual(raised.exception.status_code, 409)
        detail = raised.exception.detail
        self.assertEqual(detail["code"], "MANDATE_CAP_WARNING")
        self.assertTrue(detail["bypass_allowed"])

        confirmed = self._confirm(third, ack=True)
        self.assertEqual(confirmed["payment"].status, PaymentStatus.PENDING)
        self.assertTrue(confirmed["spend_mandate"]["bypassed"])
        verified = self._verify(confirmed)
        self.assertEqual(verified["payment"].status, PaymentStatus.PAID)
        snap = spend_mandate.snapshot(self.user_id, self.session_id)
        session_ledger = next(item for item in snap["ledgers"] if item["period"] == "session")
        self.assertGreaterEqual(session_ledger["spent_so_far_inr"], 3 * 699)

    def test_second_confirm_without_ack_also_bypasses_after_warning(self) -> None:
        self._pay()
        self._pay()
        third = self._proposal()
        with self.assertRaises(HTTPException):
            self._confirm(third)
        confirmed = self._confirm(third, ack=False)
        self.assertTrue(confirmed["spend_mandate"]["bypassed"])

    def test_dismiss_does_not_increment_ledger(self) -> None:
        proposal = self._proposal()
        confirmed = self._confirm(proposal)
        checkout.fail_payment(
            proposal.id, FailPaymentRequest(reason="user_cancelled")
        )
        snap = spend_mandate.snapshot(self.user_id, self.session_id)
        session_ledger = next(item for item in snap["ledgers"] if item["period"] == "session")
        self.assertEqual(session_ledger["spent_so_far_inr"], 0)
        self.assertEqual(session_ledger["reserved_inr"], 0)

    def test_concurrent_confirms_cas_one_remaining_slot(self) -> None:
        """Two 699 carts after one paid 699 share remaining 1301 — only one CAS hold wins."""
        self._pay()
        first = self._proposal()
        second = self._proposal()
        barrier = threading.Barrier(2)
        wins: list = []
        losses: list = []

        def confirm(proposal):
            barrier.wait(timeout=2)
            try:
                wins.append(self._confirm(proposal))
            except HTTPException as exc:
                losses.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm, first), pool.submit(confirm, second)]
            for future in futures:
                future.result(timeout=5)

        self.assertEqual(len(wins), 1, wins)
        self.assertEqual(len(losses), 1)
        self.assertEqual(losses[0].status_code, 409)
        self.assertEqual(losses[0].detail["code"], "MANDATE_CAP_WARNING")
        snap = spend_mandate.snapshot(self.user_id, self.session_id)
        session_ledger = next(item for item in snap["ledgers"] if item["period"] == "session")
        self.assertEqual(session_ledger["spent_so_far_inr"], 699)
        self.assertEqual(session_ledger["reserved_inr"], 699)

    def test_concurrent_under_cap_both_reserve(self) -> None:
        first = self._proposal()
        second = self._proposal()
        barrier = threading.Barrier(2)
        wins: list = []

        def confirm(proposal):
            barrier.wait(timeout=2)
            wins.append(self._confirm(proposal))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm, first), pool.submit(confirm, second)]
            for future in futures:
                future.result(timeout=5)

        self.assertEqual(len(wins), 2)
        snap = spend_mandate.snapshot(self.user_id, self.session_id)
        session_ledger = next(item for item in snap["ledgers"] if item["period"] == "session")
        self.assertEqual(session_ledger["spent_so_far_inr"], 0)
        self.assertEqual(session_ledger["reserved_inr"], 2 * 699)

    def test_replay_and_force_mock_skip_mandate(self) -> None:
        replay_session = f"replay_{uuid.uuid4().hex[:8]}"
        proposal = self._proposal(session_id=replay_session)
        confirmed = self._confirm(proposal, force_mock=True)
        self.assertFalse(confirmed["spend_mandate"]["enforced"])
        self._verify(confirmed)
        snap = spend_mandate.snapshot(self.user_id, replay_session)
        session_ledger = next(item for item in snap["ledgers"] if item["period"] == "session")
        self.assertEqual(session_ledger["spent_so_far_inr"], 0)

    def test_spend_mandate_api(self) -> None:
        response = self.client.get(
            "/api/spend-mandate",
            params={"user_id": self.user_id, "session_id": self.session_id},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tightest_period"], "session")
        self.assertEqual(body["tightest_remaining_inr"], 2000)
        self.assertIn("impulsive", body["note"].lower())


if __name__ == "__main__":
    unittest.main()
