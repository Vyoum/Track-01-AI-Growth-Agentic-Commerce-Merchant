"""Supabase PostgREST client for merchant catalog + order history.

Uses Supabase's auto-generated REST API (not a custom merchant REST layer):

  GET {SUPABASE_URL}/rest/v1/{products_table}?select=*
  GET {SUPABASE_URL}/rest/v1/{products_table}?id=eq.{id}&select=*&limit=1
  GET {SUPABASE_URL}/rest/v1/{orders_table}?{user_col}=eq.{user_id}&order=...&limit=1

Auth headers (required by PostgREST):
  apikey: {SUPABASE_KEY}
  Authorization: Bearer {SUPABASE_KEY}

Use the service role key on the backend only — never expose it to the browser.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import Settings, get_settings
from backend.integrations.client_store_client import MerchantStoreError
from backend.integrations.store_mapper import map_product, map_usual_order
from backend.models import Product, UsualOrderResponse

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SupabaseStoreClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        self.base_url = self.settings.effective_supabase_url.rstrip("/")
        self.api_key = self.settings.effective_supabase_key
        self.timeout = float(self.settings.store_api_timeout_seconds)
        self.max_retries = int(self.settings.store_api_max_retries)
        self._transport = transport

        self.products_table = self.settings.supabase_products_table
        self.orders_table = self.settings.supabase_orders_table
        self.order_items_table = self.settings.supabase_order_items_table
        self.order_user_column = self.settings.supabase_order_user_column
        self.order_status_column = self.settings.supabase_order_status_column
        self.order_completed_status = self.settings.supabase_order_completed_status
        self.order_date_column = self.settings.supabase_order_date_column
        self.order_items_fk = self.settings.supabase_order_items_fk
        self.product_id_column = self.settings.supabase_product_id_column

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "checkout-agent/0.4",
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    def _rest_url(self, table: str) -> str:
        if not _SAFE_IDENT.match(table):
            raise MerchantStoreError(f"unsafe supabase table name: {table}")
        return f"{self.base_url}/rest/v1/{quote(table, safe='')}"

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
                        f"supabase API {resp.status_code} on {url}"
                    )
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 400:
                    raise MerchantStoreError(
                        f"supabase API {resp.status_code}: {resp.text[:300]}"
                    )
                if resp.status_code == 204 or not resp.content:
                    return []
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError, MerchantStoreError) as exc:
                last_error = exc
                logger.warning(
                    "supabase GET failed attempt=%s url=%s err=%s",
                    attempt,
                    url,
                    exc,
                )
                if attempt > self.max_retries:
                    break
        raise MerchantStoreError(str(last_error) if last_error else "supabase request failed")

    def _as_rows(self, data: Any) -> list[dict[str, Any]]:
        if data is None:
            return []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def _product_select(self) -> str:
        return "*"

    def _order_select(self) -> str:
        relation = (self.order_items_table or "").strip()
        if relation and _SAFE_IDENT.match(relation):
            return f"*,{relation}(*)"
        return "*"

    def list_products(
        self,
        query: str = "",
        category: str | None = None,
    ) -> list[Product]:
        params: dict[str, Any] = {"select": self._product_select()}
        if category:
            params["category"] = f"eq.{category}"
        q = (query or "").strip()
        if q:
            safe = q.replace(",", " ").replace("(", " ").replace(")", " ")
            params["or"] = (
                f"(name.ilike.*{safe}*,category.ilike.*{safe}*,"
                f"{self.product_id_column}.ilike.*{safe}*)"
            )
        data = self._get(self._rest_url(self.products_table), params=params)
        products: list[Product] = []
        for raw in self._as_rows(data):
            try:
                products.append(map_product(raw))
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("skip bad supabase product row: %s", exc)
        return products

    def get_product(self, product_id: str) -> Product | None:
        params = {
            "select": self._product_select(),
            self.product_id_column: f"eq.{product_id}",
            "limit": "1",
        }
        rows = self._as_rows(
            self._get(self._rest_url(self.products_table), params=params)
        )
        if not rows:
            return None
        return map_product(rows[0])

    def get_usual_order(self, user_id: str) -> UsualOrderResponse:
        params: dict[str, Any] = {
            "select": self._order_select(),
            self.order_user_column: f"eq.{user_id}",
            "order": f"{self.order_date_column}.desc",
            "limit": "1",
        }
        status_col = (self.order_status_column or "").strip()
        status_val = (self.order_completed_status or "").strip()
        if status_col and status_val and _SAFE_IDENT.match(status_col):
            params[status_col] = f"eq.{status_val}"

        rows = self._as_rows(
            self._get(self._rest_url(self.orders_table), params=params)
        )
        if not rows:
            return UsualOrderResponse(
                user_id=user_id,
                order_id=None,
                items=[],
                total_inr=0,
                source="supabase_empty",
            )

        order = rows[0]
        items_key = self.order_items_table
        nested = order.get(items_key)
        if isinstance(nested, list) and nested:
            order = {**order, "items": nested}
        elif not order.get("items") and not order.get("line_items"):
            order_id = str(order.get("id") or order.get("order_id") or "")
            fk_col = (self.order_items_fk or "order_id").strip()
            if order_id and _SAFE_IDENT.match(fk_col):
                line_params = {
                    "select": "*",
                    fk_col: f"eq.{order_id}",
                }
                line_rows = self._as_rows(
                    self._get(
                        self._rest_url(self.order_items_table),
                        params=line_params,
                    )
                )
                if line_rows:
                    order = {**order, "items": line_rows}

        return map_usual_order(user_id, order)
