"""ACP-shaped public checkout session API (adapter)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from backend.services import acp_checkout

router = APIRouter(tags=["acp"])

ACP_VERSION = acp_checkout.ACP_API_VERSION


def _check_version(api_version: str | None) -> None:
    if not api_version:
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_api_version", "message": "Send API-Version: 2026-04-17"},
        )


def _normalize_idempotency(value: str | None) -> str | None:
    key = (value or "").strip()
    return key or None


def _check_bearer(authorization: str | None) -> None:
    if not authorization:
        return
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})


@router.get("/.well-known/acp.json")
def acp_discovery() -> dict[str, Any]:
    return {
        "protocol": "acp",
        "api_version": ACP_VERSION,
        "profile": "adapter_over_proposal_confirm_v1",
        "instant_checkout_partner": False,
        "merchant_of_record": True,
        "currency": "INR",
        "endpoints": {
            "create_session": "POST /checkout_sessions",
            "get_session": "GET /checkout_sessions/{id}",
            "update_session": "POST /checkout_sessions/{id}",
            "complete": "POST /checkout_sessions/{id}/complete",
            "cancel": "POST /checkout_sessions/{id}/cancel",
            "catalog": "GET /api/products",
        },
        "payment": {
            "delegated_demo_token": "spt_demo_*",
            "live_stripe_spt": False,
            "razorpay_test_checkout_js": True,
        },
        "notes": [
            "Same guardrails as human checkout (catalog prices, addon gate, mandate CAS).",
            "Not ChatGPT Instant Checkout. Open spec shape for A2A agents.",
        ],
    }


@router.post("/checkout_sessions")
def create_checkout_session(
    body: dict[str, Any],
    api_version: str | None = Header(default=None, alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: str | None = Header(default=None),
):
    _check_version(api_version)
    _check_bearer(authorization)
    session = acp_checkout.create_session(
        body, idempotency_key=_normalize_idempotency(idempotency_key)
    )
    return JSONResponse(status_code=201, content=session)


@router.get("/checkout_sessions/{checkout_session_id}")
def get_checkout_session(
    checkout_session_id: str,
    api_version: str | None = Header(default=None, alias="API-Version"),
    authorization: str | None = Header(default=None),
):
    _check_version(api_version)
    _check_bearer(authorization)
    return acp_checkout.get_session(checkout_session_id)


@router.post("/checkout_sessions/{checkout_session_id}")
def update_checkout_session(
    checkout_session_id: str,
    body: dict[str, Any],
    api_version: str | None = Header(default=None, alias="API-Version"),
    authorization: str | None = Header(default=None),
):
    _check_version(api_version)
    _check_bearer(authorization)
    return acp_checkout.update_session(checkout_session_id, body)


@router.post("/checkout_sessions/{checkout_session_id}/complete")
def complete_checkout_session(
    checkout_session_id: str,
    body: dict[str, Any],
    api_version: str | None = Header(default=None, alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: str | None = Header(default=None),
):
    _check_version(api_version)
    _check_bearer(authorization)
    return acp_checkout.complete_session(
        checkout_session_id,
        body,
        idempotency_key=_normalize_idempotency(idempotency_key),
    )


@router.post("/checkout_sessions/{checkout_session_id}/cancel")
def cancel_checkout_session(
    checkout_session_id: str,
    api_version: str | None = Header(default=None, alias="API-Version"),
    authorization: str | None = Header(default=None),
):
    _check_version(api_version)
    _check_bearer(authorization)
    return acp_checkout.cancel_session(checkout_session_id)
