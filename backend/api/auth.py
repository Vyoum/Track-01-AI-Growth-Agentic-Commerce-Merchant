"""FastAPI authentication dependencies for merchant-only operations."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.models import MerchantAccount
from backend.services import merchant_auth

merchant_bearer = HTTPBearer(auto_error=False)
MERCHANT_SESSION_COOKIE = "checkoutpilot_merchant_session"


def require_merchant_credentials(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(merchant_bearer),
) -> tuple[MerchantAccount, str]:
    token = None
    if credentials is not None and credentials.scheme.casefold() == "bearer":
        token = credentials.credentials
    if token is None:
        token = request.cookies.get(MERCHANT_SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Merchant authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    merchant = merchant_auth.authenticate_token(token)
    if merchant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired merchant session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return merchant, token


def require_merchant(
    authenticated: tuple[MerchantAccount, str] = Depends(require_merchant_credentials),
) -> MerchantAccount:
    return authenticated[0]
