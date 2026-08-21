"""Thin Razorpay SDK wrapper — TEST MODE ONLY."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import razorpay
from razorpay.errors import SignatureVerificationError

from backend.config import Settings, get_settings


class RazorpayConfigError(RuntimeError):
    pass


@dataclass
class CreatedOrder:
    order_id: str
    amount_paise: int
    currency: str
    receipt: str
    mock: bool
    raw: dict[str, Any]


def keys_are_usable(settings: Settings | None = None) -> bool:
    """True when test keys look real (not .env.example placeholders)."""
    s = settings or get_settings()
    key_id = (s.razorpay_key_id or "").strip()
    secret = (s.razorpay_key_secret or "").strip()
    if not key_id.startswith("rzp_test_"):
        return False
    if key_id in {"rzp_test_xxxxxxxx", "rzp_test_your_key"}:
        return False
    if not secret or secret in {"replace_me", "your_secret", "xxxxxxxx"}:
        return False
    if "xxxx" in key_id.lower():
        return False
    return True


class RazorpayClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if self.settings.razorpay_mode != "test":
            raise RazorpayConfigError("Only Razorpay test mode is allowed")
        key_id = (self.settings.razorpay_key_id or "").strip()
        if key_id.startswith("rzp_live_"):
            raise RazorpayConfigError("Live Razorpay keys are forbidden")
        self._client: razorpay.Client | None = None
        if keys_are_usable(self.settings):
            self._client = razorpay.Client(
                auth=(self.settings.razorpay_key_id, self.settings.razorpay_key_secret)
            )

    @property
    def live_ready(self) -> bool:
        """Misnomer historically — means 'test API ready' (real test keys configured)."""
        return self._client is not None

    @property
    def public_key_id(self) -> str | None:
        if not self.live_ready:
            return None
        return self.settings.razorpay_key_id

    def create_order(
        self,
        *,
        amount_inr: int,
        receipt: str,
        notes: dict[str, Any] | None = None,
        force_mock: bool = False,
    ) -> CreatedOrder:
        amount_paise = int(amount_inr) * 100
        currency = "INR"
        notes = notes or {}

        if force_mock or not self.live_ready:
            order_id = f"order_mock_{uuid.uuid4().hex[:14]}"
            return CreatedOrder(
                order_id=order_id,
                amount_paise=amount_paise,
                currency=currency,
                receipt=receipt,
                mock=True,
                raw={
                    "id": order_id,
                    "amount": amount_paise,
                    "currency": currency,
                    "receipt": receipt,
                    "status": "created",
                    "notes": notes,
                    "mock": True,
                },
            )

        assert self._client is not None
        try:
            raw = self._client.order.create(
                {
                    "amount": amount_paise,
                    "currency": currency,
                    "receipt": receipt[:40],
                    "payment_capture": 1,
                    "notes": {k: str(v)[:500] for k, v in notes.items()},
                }
            )
        except Exception as exc:  # noqa: BLE001 — surface as config/API error
            raise RazorpayConfigError(f"Razorpay order.create failed: {exc}") from exc

        return CreatedOrder(
            order_id=raw["id"],
            amount_paise=int(raw["amount"]),
            currency=raw.get("currency", currency),
            receipt=raw.get("receipt", receipt),
            mock=False,
            raw=dict(raw),
        )

    def verify_payment_signature(
        self,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> bool:
        if not self.live_ready:
            # Mock verify: accept signatures that start with mock_ok_
            return razorpay_signature.startswith("mock_ok_") and razorpay_order_id.startswith(
                "order_mock_"
            )

        assert self._client is not None
        try:
            self._client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "razorpay_signature": razorpay_signature,
                }
            )
            return True
        except SignatureVerificationError:
            return False

    def fetch_payment(self, razorpay_payment_id: str) -> dict[str, Any]:
        if not self.live_ready:
            return {
                "id": razorpay_payment_id,
                "status": "captured",
                "mock": True,
            }
        assert self._client is not None
        return dict(self._client.payment.fetch(razorpay_payment_id))


_default_client: RazorpayClient | None = None


def get_razorpay_client() -> RazorpayClient:
    global _default_client
    if _default_client is None:
        _default_client = RazorpayClient()
    return _default_client


def reset_razorpay_client() -> None:
    global _default_client
    _default_client = None
