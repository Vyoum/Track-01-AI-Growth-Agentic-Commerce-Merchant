"""Merchant account authentication and protected administration routes."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import connect, init_db
from backend.main import app


class MerchantAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "merchant-auth.db")
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
        init_db()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        get_settings.cache_clear()
        self._env.stop()
        self._tmp.cleanup()

    def _register(self, *, email: str = "owner@example.com"):
        return self.client.post(
            "/api/merchant/auth/register",
            json={
                "name": "Atelier Store",
                "email": email,
                "password": "correct-horse-123",
            },
        )

    def test_register_returns_session_and_account_without_secrets(self) -> None:
        response = self._register()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["access_token"].startswith("mss_"))
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["merchant"]["email"], "owner@example.com")
        self.assertEqual(body["merchant"]["slug"], "atelier-store")
        self.assertNotIn("password", body["merchant"])
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)

        # Browser clients authenticate with the HttpOnly cookie. API clients can
        # use the returned bearer token instead.
        account = self.client.get("/api/merchant/account")
        self.assertEqual(account.status_code, 200, account.text)
        self.assertEqual(account.json()["id"], body["merchant"]["id"])

    def test_credentials_and_raw_tokens_are_not_stored(self) -> None:
        body = self._register().json()
        with connect() as conn:
            merchant = conn.execute("SELECT * FROM merchants").fetchone()
            session = conn.execute("SELECT * FROM merchant_sessions").fetchone()
        self.assertNotEqual(merchant["password_hash"], "correct-horse-123")
        self.assertNotIn("correct-horse-123", merchant["password_hash"])
        self.assertNotEqual(session["token_hash"], body["access_token"])
        self.assertNotIn(body["access_token"], session["token_hash"])

    def test_duplicate_email_is_case_insensitive(self) -> None:
        self.assertEqual(self._register(email="Owner@Example.com").status_code, 201)
        duplicate = self._register(email="owner@example.com")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_login_rejects_wrong_password_and_accepts_correct_password(self) -> None:
        self._register()
        wrong = self.client.post(
            "/api/merchant/auth/login",
            json={"email": "owner@example.com", "password": "wrong-password"},
        )
        self.assertEqual(wrong.status_code, 401)

        correct = self.client.post(
            "/api/merchant/auth/login",
            json={"email": "OWNER@example.com", "password": "correct-horse-123"},
        )
        self.assertEqual(correct.status_code, 200, correct.text)

    def test_logout_revokes_current_session(self) -> None:
        token = self._register().json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/merchant/account", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/merchant/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/merchant/account", headers=headers).status_code, 401)

    def test_expired_session_is_rejected(self) -> None:
        token = self._register().json()["access_token"]
        with connect() as conn:
            conn.execute(
                "UPDATE merchant_sessions SET expires_at = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),),
            )
            conn.commit()
        response = self.client.get(
            "/api/merchant/account",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 401)

    def test_campaign_administration_requires_merchant_session(self) -> None:
        unauthenticated = self.client.get("/api/merchant/campaigns/catalog")
        self.assertEqual(unauthenticated.status_code, 401)

        token = self._register().json()["access_token"]
        authenticated = self.client.get(
            "/api/merchant/campaigns/catalog",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(authenticated.status_code, 200, authenticated.text)
        self.assertGreater(len(authenticated.json()["campaigns"]), 0)

    def test_campaign_policy_state_is_isolated_between_merchants(self) -> None:
        first = self._register(email="first@example.com").json()
        second = self._register(email="second@example.com").json()
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}

        first_catalog = self.client.get(
            "/api/merchant/campaigns/catalog",
            headers=first_headers,
        ).json()
        campaign_id = first_catalog["campaigns"][0]["id"]
        paused = self.client.post(
            f"/api/merchant/campaigns/{campaign_id}/policy",
            json={"status": "paused"},
            headers=first_headers,
        )
        self.assertEqual(paused.status_code, 200, paused.text)

        first_after = self.client.get(
            "/api/merchant/campaigns/catalog",
            headers=first_headers,
        ).json()
        second_after = self.client.get(
            "/api/merchant/campaigns/catalog",
            headers=second_headers,
        ).json()
        first_policy = next(c for c in first_after["campaigns"] if c["id"] == campaign_id)
        second_policy = next(c for c in second_after["campaigns"] if c["id"] == campaign_id)
        self.assertEqual(first_policy["live_status"], "paused")
        self.assertEqual(second_policy["live_status"], "enabled")

    def test_merchant_cannot_decide_another_merchants_proposal(self) -> None:
        first = self._register(email="first@example.com").json()
        second = self._register(email="second@example.com").json()
        proposal = self.client.post(
            "/api/proposals",
            json={
                "merchant_id": first["merchant"]["id"],
                "user_id": "demo_user_01",
                "product_ids": ["prod_protein_bundle"],
                "stated_budget_inr": 800,
                "with_growth": True,
            },
        ).json()
        response = self.client.post(
            f"/api/proposals/{proposal['id']}/campaign/decide",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
