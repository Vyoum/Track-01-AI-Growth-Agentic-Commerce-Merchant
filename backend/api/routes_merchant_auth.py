"""Merchant registration, login, session, and account routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.api.auth import (
    MERCHANT_SESSION_COOKIE,
    require_merchant,
    require_merchant_credentials,
)
from backend.audit.logger import log_event
from backend.models import (
    MerchantAccount,
    MerchantAuthResponse,
    MerchantLoginRequest,
    MerchantRegisterRequest,
)
from backend.services import merchant_auth
from backend.config import get_settings

router = APIRouter(prefix="/api/merchant", tags=["merchant-auth"])


def _set_session_cookie(response: Response, result: MerchantAuthResponse) -> None:
    settings = get_settings()
    response.set_cookie(
        key=MERCHANT_SESSION_COOKIE,
        value=result.access_token,
        max_age=settings.merchant_session_ttl_hours * 60 * 60,
        httponly=True,
        secure=settings.app_env.casefold() in {"production", "staging"},
        samesite="lax",
        path="/api",
    )


@router.post(
    "/auth/register",
    response_model=MerchantAuthResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_merchant(body: MerchantRegisterRequest, response: Response):
    try:
        result = merchant_auth.register(body)
    except merchant_auth.MerchantEmailExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_event(
        "merchant_registered",
        user_id=result.merchant.id,
        payload={"merchant_id": result.merchant.id, "slug": result.merchant.slug},
    )
    _set_session_cookie(response, result)
    return result


@router.post("/auth/login", response_model=MerchantAuthResponse)
def login_merchant(body: MerchantLoginRequest, response: Response):
    try:
        result = merchant_auth.login(body)
    except merchant_auth.MerchantAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    log_event(
        "merchant_logged_in",
        user_id=result.merchant.id,
        payload={"merchant_id": result.merchant.id},
    )
    _set_session_cookie(response, result)
    return result


@router.get("/account", response_model=MerchantAccount)
def get_merchant_account(
    merchant: MerchantAccount = Depends(require_merchant),
):
    return merchant


@router.post("/auth/logout")
def logout_merchant(
    response: Response,
    authenticated: tuple[MerchantAccount, str] = Depends(require_merchant_credentials),
):
    merchant, token = authenticated
    merchant_auth.revoke_token(token)
    log_event(
        "merchant_logged_out",
        user_id=merchant.id,
        payload={"merchant_id": merchant.id},
    )
    response.delete_cookie(MERCHANT_SESSION_COOKIE, path="/api")
    return {"ok": True}


@router.post("/auth/logout-all")
def logout_all_merchant_sessions(
    response: Response,
    authenticated: tuple[MerchantAccount, str] = Depends(require_merchant_credentials),
):
    merchant, _token = authenticated
    revoked = merchant_auth.revoke_all_sessions(merchant.id)
    log_event(
        "merchant_all_sessions_revoked",
        user_id=merchant.id,
        payload={"merchant_id": merchant.id, "sessions_revoked": revoked},
    )
    response.delete_cookie(MERCHANT_SESSION_COOKIE, path="/api")
    return {"ok": True, "sessions_revoked": revoked}
