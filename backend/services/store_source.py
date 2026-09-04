"""Catalog / history source: mock JSON or live merchant API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from backend.config import get_settings
from backend.integrations.client_store_client import (
    ClientStoreClient,
    MerchantStoreError,
    get_store_client,
)
from backend.models import Product, UsualOrderResponse
from backend.services import data_loader

logger = logging.getLogger(__name__)

_source_status_lock = Lock()
_source_status: dict[str, Any] = {
    "configured_catalog_source": None,
    "catalog_source": None,
    "catalog_fallback_active": False,
    "catalog_fallback_reason": None,
    "catalog_source_checked_at": None,
}


def use_merchant_api() -> bool:
    settings = get_settings()
    if settings.use_mock_catalog:
        return False
    provider = settings.resolved_store_provider
    if provider == "supabase":
        return bool(
            settings.effective_supabase_url.strip()
            and settings.effective_supabase_key.strip()
        )
    return bool(settings.store_api_base_url.strip())


def _configured_source_label() -> str:
    settings = get_settings()
    if not use_merchant_api():
        return "mock_json"
    if settings.resolved_store_provider == "supabase":
        return "supabase"
    return "merchant_api"


def _record_source_status(
    *,
    active_source: str,
    fallback_active: bool,
    reason: str | None = None,
) -> None:
    configured_source = _configured_source_label()
    with _source_status_lock:
        _source_status.update(
            {
                "configured_catalog_source": configured_source,
                "catalog_source": active_source,
                "catalog_fallback_active": fallback_active,
                "catalog_fallback_reason": reason,
                "catalog_source_checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )


def _mark_configured_source() -> None:
    configured_source = _configured_source_label()
    _record_source_status(
        active_source=configured_source,
        fallback_active=False,
    )


def _mark_live_source() -> None:
    _mark_configured_source()


def _mark_mock_fallback(operation: str) -> None:
    configured_source = _configured_source_label()
    provider = "Supabase" if configured_source == "supabase" else "merchant API"
    _record_source_status(
        active_source="mock_json",
        fallback_active=True,
        reason=f"{provider} unavailable during {operation}; serving temporary mock data",
    )


def reset_source_status() -> None:
    """Clear process-local provenance (used after configuration changes/tests)."""
    with _source_status_lock:
        _source_status.update(
            {
                "configured_catalog_source": None,
                "catalog_source": None,
                "catalog_fallback_active": False,
                "catalog_fallback_reason": None,
                "catalog_source_checked_at": None,
            }
        )


def source_status() -> dict[str, Any]:
    """Return configured and actual runtime catalog provenance."""
    configured_source = _configured_source_label()
    with _source_status_lock:
        if _source_status["configured_catalog_source"] != configured_source:
            _source_status.update(
                {
                    "configured_catalog_source": configured_source,
                    "catalog_source": configured_source,
                    "catalog_fallback_active": False,
                    "catalog_fallback_reason": None,
                    "catalog_source_checked_at": None,
                }
            )
        return dict(_source_status)


def list_products() -> list[Product]:
    if not use_merchant_api():
        _mark_configured_source()
        return list(data_loader.load_catalog().values())
    try:
        products = get_store_client().list_products()
        _mark_live_source()
        return products
    except MerchantStoreError as exc:
        if get_settings().store_fallback_to_mock:
            logger.warning("merchant list_products failed, falling back to mock: %s", exc)
            _mark_mock_fallback("catalog listing")
            return list(data_loader.load_catalog().values())
        raise


def get_product(product_id: str) -> Product | None:
    if not use_merchant_api():
        _mark_configured_source()
        return data_loader.load_catalog().get(product_id)
    try:
        product = get_store_client().get_product(product_id)
        if product is not None:
            _mark_live_source()
            return product
        # Some merchants only expose list endpoints — fall through to search cache
        for p in get_store_client().list_products():
            if p.id == product_id:
                _mark_live_source()
                return p
        _mark_live_source()
        if get_settings().store_fallback_to_mock:
            return data_loader.load_catalog().get(product_id)
        return None
    except MerchantStoreError as exc:
        if get_settings().store_fallback_to_mock:
            logger.warning("merchant get_product failed, falling back to mock: %s", exc)
            _mark_mock_fallback("product lookup")
            return data_loader.load_catalog().get(product_id)
        raise


def search_products(query: str = "", category: str | None = None) -> list[Product]:
    if not use_merchant_api():
        _mark_configured_source()
        return _local_search(query, category)
    try:
        products = get_store_client().list_products(query=query, category=category)
        # If merchant ignores filters, apply locally
        _mark_live_source()
        return _filter_products(products, query, category)
    except MerchantStoreError as exc:
        if get_settings().store_fallback_to_mock:
            logger.warning("merchant search failed, falling back to mock: %s", exc)
            _mark_mock_fallback("catalog search")
            return _local_search(query, category)
        raise


def get_usual_order(user_id: str) -> UsualOrderResponse:
    if not use_merchant_api():
        _mark_configured_source()
        return _local_usual(user_id)
    try:
        usual = get_store_client().get_usual_order(user_id)
        _mark_live_source()
        # Reprice with live catalog when possible
        items = []
        for item in usual.items:
            live = get_product(item.product_id)
            if live:
                items.append(
                    item.model_copy(
                        update={
                            "name": live.name,
                            "unit_price_inr": live.price_inr,
                            "line_total_inr": live.price_inr * item.qty,
                        }
                    )
                )
            else:
                items.append(item)
        total = sum(i.line_total_inr for i in items)
        return usual.model_copy(update={"items": items, "total_inr": total})
    except MerchantStoreError as exc:
        if get_settings().store_fallback_to_mock:
            logger.warning("merchant usual order failed, falling back to mock: %s", exc)
            usual = _local_usual(user_id)
            # _local_usual may perform product lookups; ensure the final status
            # still records that this request used fallback history.
            _mark_mock_fallback("order-history lookup")
            return usual
        raise


def _filter_products(
    products: list[Product],
    query: str = "",
    category: str | None = None,
) -> list[Product]:
    q = (query or "").strip().lower()
    out: list[Product] = []
    for product in products:
        if category and product.category != category:
            continue
        if not q:
            out.append(product)
            continue
        haystack = " ".join(
            [product.id, product.name, product.category, *product.tags]
        ).lower()
        if q in haystack:
            out.append(product)
    return out


def _local_search(query: str = "", category: str | None = None) -> list[Product]:
    return _filter_products(list(data_loader.load_catalog().values()), query, category)


def _local_usual(user_id: str) -> UsualOrderResponse:
    data = data_loader.load_demo_users()
    user_orders = [
        o
        for o in data.get("orders", [])
        if o.get("user_id") == user_id and o.get("status") == "completed"
    ]
    if not user_orders:
        return UsualOrderResponse(
            user_id=user_id,
            order_id=None,
            items=[],
            total_inr=0,
            source="none",
        )

    from backend.models import LineItem

    latest = max(user_orders, key=lambda o: o.get("created_at", ""))
    items: list[LineItem] = []
    for raw in latest.get("items", []):
        product = get_product(raw["product_id"])
        unit = product.price_inr if product else int(raw["unit_price_inr"])
        name = product.name if product else raw["name"]
        qty = int(raw.get("qty", 1))
        items.append(
            LineItem(
                product_id=raw["product_id"],
                name=name,
                qty=qty,
                unit_price_inr=unit,
                line_total_inr=unit * qty,
                reason="matched last purchase",
            )
        )
    total = sum(i.line_total_inr for i in items)
    return UsualOrderResponse(
        user_id=user_id,
        order_id=latest.get("order_id"),
        items=items,
        total_inr=total,
        source="most_recent_completed_order",
    )


def source_label() -> str:
    return str(source_status()["catalog_source"])
