"""Catalog tool — wraps catalog service for later agent use."""

from __future__ import annotations

from backend.services import catalog


def get_products(query: str = "", category: str | None = None):
    return catalog.search_products(query=query, category=category)


def get_product(product_id: str):
    return catalog.get_product(product_id)
