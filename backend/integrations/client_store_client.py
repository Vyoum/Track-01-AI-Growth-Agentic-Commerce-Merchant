"""HTTP client for the merchant e-commerce platform (Pointer 9).

Expected merchant API (configurable paths via env):

  GET  {STORE_API_BASE_URL}/products?q=&category=
  GET  {STORE_API_BASE_URL}/products/{id}
  GET  {STORE_API_BASE_URL}/customers/{user_id}/orders/latest

Auth: Authorization: Bearer {STORE_API_KEY}  (optional)
Demo: when USE_MOCK_CATALOG=true or base URL empty → callers use local JSON.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.config import Settings, get_settings
from backend.integrations.store_mapper import map_product, map_usual_order
from backend.models import Product, UsualOrderResponse

logger = logging.getLogger(__name__)


class MerchantStoreError(RuntimeError):
    pass


class ClientStoreClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        self.base_url = (base_url if base_url is not None else self.settings.store_api_base_url or "").rstrip("/")
        self.api_key = (self.settings.store_api_key or "").strip()
        self.timeout = float(self.settings.store_api_timeout_seconds)
        self.max_retries = int(self.settings.store_api_max_retries)
        self.products_path = self.settings.store_products_path
        self.product_path = self.settings.store_product_path
        self.usual_path = self.settings.store_usual_order_path
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "checkout-agent/0.3",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str, **path_params: str) -> str:
        formatted = path
        for key, value in path_params.items():
            formatted = formatted.replace("{" + key + "}", value)
        if not formatted.startswith("/"):
            formatted = "/" + formatted
        return f"{self.base_url}{formatted}"

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    transport=self._transport,
                ) as client:
                    resp = client.get(url, headers=self._headers(), params=params or {})
                if resp.status_code >= 500:
                    raise MerchantStoreError(
                        f"merchant API {resp.status_code} on {url}"
                    )
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 400:
                    raise MerchantStoreError(
                        f"merchant API {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError, MerchantStoreError) as exc:
                last_error = exc
                logger.warning(
                    "merchant GET failed attempt=%s url=%s err=%s",
                    attempt,
                    url,
                    exc,
                )
                if attempt > self.max_retries:
                    break
        raise MerchantStoreError(str(last_error) if last_error else "merchant request failed")

    def list_products(
        self,
        query: str = "",
        category: str | None = None,
    ) -> list[Product]:
        params: dict[str, Any] = {}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        data = self._get(self._url(self.products_path), params=params)
        if data is None:
            return []
        raw_list = data if isinstance(data, list) else data.get("products") or data.get("items") or []
        products: list[Product] = []
        for raw in raw_list:
            try:
                products.append(map_product(raw))
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("skip bad product payload: %s", exc)
        return products

    def get_product(self, product_id: str) -> Product | None:
        data = self._get(self._url(self.product_path, id=product_id))
        if data is None:
            return None
        raw = data.get("product") if isinstance(data, dict) and "product" in data else data
        if not isinstance(raw, dict):
            return None
        return map_product(raw)

    def get_usual_order(self, user_id: str) -> UsualOrderResponse:
        data = self._get(self._url(self.usual_path, user_id=user_id))
        if data is None:
            return UsualOrderResponse(
                user_id=user_id,
                order_id=None,
                items=[],
                total_inr=0,
                source="merchant_api_empty",
            )
        if not isinstance(data, dict):
            raise MerchantStoreError("usual order response must be an object")
        return map_usual_order(user_id, data)


_client: ClientStoreClient | None = None
_supabase_client = None


def get_store_client():
    """Return REST or Supabase store client based on settings."""
    global _client, _supabase_client
    settings = get_settings()
    provider = settings.resolved_store_provider
    if provider == "supabase":
        if _supabase_client is None:
            from backend.integrations.supabase_store_client import SupabaseStoreClient

            _supabase_client = SupabaseStoreClient(settings=settings)
        return _supabase_client
    if _client is None:
        _client = ClientStoreClient(settings=settings)
    return _client


def reset_store_client() -> None:
    global _client, _supabase_client
    _client = None
    _supabase_client = None
