"""Catalog service — mock JSON or merchant API via store_source."""

from __future__ import annotations

from backend.models import Product
from backend.services import store_source


def list_products() -> list[Product]:
    return store_source.list_products()


def get_product(product_id: str) -> Product | None:
    return store_source.get_product(product_id)


def search_products(query: str = "", category: str | None = None) -> list[Product]:
    return store_source.search_products(query=query, category=category)
