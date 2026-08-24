#!/usr/bin/env python3
"""Smoke-test merchant adapter: mock source + HTTP client against /merchant-mock."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import ClientStoreClient, reset_store_client
from backend.integrations.store_mapper import map_product, map_usual_order
from backend.main import app
from backend.services import store_source


def test_mapper() -> None:
    p = map_product(
        {
            "sku": "sku_1",
            "title": "Test Item",
            "price": 199,
            "product_type": "grocery",
            "inventory": 5,
            "related_products": ["sku_2"],
        }
    )
    assert p.id == "sku_1"
    assert p.price_inr == 199
    assert p.complements[0].product_id == "sku_2"

    usual = map_usual_order(
        "u1",
        {
            "id": "o1",
            "total": 699,
            "line_items": [
                {"sku": "prod_protein_bundle", "title": "Protein", "quantity": 1, "price": 699}
            ],
        },
    )
    assert usual.total_inr == 699
    assert usual.items[0].product_id == "prod_protein_bundle"
    print("mapper ok")


def test_mock_source() -> None:
    get_settings.cache_clear()
    reset_store_client()
    assert store_source.source_label() == "mock_json"
    products = store_source.search_products("protein")
    assert products
    usual = store_source.get_usual_order("demo_user_01")
    assert usual.total_inr == 699
    print("mock source ok:", usual.order_id)


def test_http_adapter_against_merchant_mock() -> None:
    init_db()
    asgi = TestClient(app)

    r = asgi.get("/merchant-mock/products", params={"q": "shaker"})
    assert r.status_code == 200
    assert r.json()["products"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = dict(request.url.params)
        method = request.method.upper()
        if method != "GET":
            return httpx.Response(405)
        resp = asgi.get(path, params=query)
        return httpx.Response(resp.status_code, json=resp.json())

    store = ClientStoreClient(
        base_url="http://merchant.test/merchant-mock",
        transport=httpx.MockTransport(handler),
    )
    assert store.configured is True
    products = store.list_products(query="protein")
    assert any(p.id == "prod_protein_bundle" for p in products)
    product = store.get_product("prod_shaker")
    assert product is not None and product.price_inr == 99
    usual = store.get_usual_order("demo_user_01")
    assert usual.total_inr == 699
    print("http adapter ok:", usual.order_id, "products=", len(products))


def main() -> None:
    test_mapper()
    test_mock_source()
    test_http_adapter_against_merchant_mock()
    print(json.dumps({"status": "pointer9_merchant_adapter_smoke_passed"}, indent=2))


if __name__ == "__main__":
    main()
