"""Load frozen demo JSON: catalog, users, guardrail policy."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR
from backend.models import Product


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_catalog() -> dict[str, Product]:
    raw = _read_json(DATA_DIR / "mock_catalog.json")
    products = [Product.model_validate(p) for p in raw["products"]]
    return {p.id: p for p in products}


@lru_cache
def load_demo_users() -> dict[str, Any]:
    return _read_json(DATA_DIR / "demo_users.json")


@lru_cache
def load_policy() -> dict[str, Any]:
    return _read_json(DATA_DIR / "guardrail_policy.json")


@lru_cache
def load_campaigns() -> dict[str, Any]:
    return _read_json(DATA_DIR / "campaigns.json")


def reload_demo_data() -> None:
    load_catalog.cache_clear()
    load_demo_users.cache_clear()
    load_policy.cache_clear()
    load_campaigns.cache_clear()
