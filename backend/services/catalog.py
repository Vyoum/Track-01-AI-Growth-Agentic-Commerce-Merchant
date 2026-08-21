"""Catalog service — server-owned product data only."""

from __future__ import annotations

from backend.models import Product
from backend.services.data_loader import load_catalog


def list_products() -> list[Product]:
    return list(load_catalog().values())


def get_product(product_id: str) -> Product | None:
    return load_catalog().get(product_id)


def search_products(query: str = "", category: str | None = None) -> list[Product]:
    q = (query or "").strip().lower()
    results: list[Product] = []
    for product in load_catalog().values():
        if category and product.category != category:
            continue
        if not q:
            results.append(product)
            continue
        haystack = " ".join(
            [product.id, product.name, product.category, *product.tags]
        ).lower()
        if q in haystack:
            results.append(product)
    return results
