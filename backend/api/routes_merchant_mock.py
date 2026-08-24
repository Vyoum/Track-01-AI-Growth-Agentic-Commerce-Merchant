"""Built-in merchant-shaped mock API for adapter testing (Pointer 9).

Serves the same contract ClientStoreClient expects, backed by demo JSON.
Point STORE_API_BASE_URL at http://127.0.0.1:8000/merchant-mock to exercise
the live adapter path without an external merchant.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.services import data_loader
from backend.services.data_loader import load_catalog

router = APIRouter(prefix="/merchant-mock", tags=["merchant-mock"])


@router.get("/products")
def mock_list_products(
    q: str = Query(default=""),
    category: str | None = None,
):
    products = list(load_catalog().values())
    qn = (q or "").strip().lower()
    out = []
    for p in products:
        if category and p.category != category:
            continue
        if qn:
            hay = " ".join([p.id, p.name, p.category, *p.tags]).lower()
            if qn not in hay:
                continue
        out.append(p.model_dump())
    return {"products": out}


@router.get("/products/{product_id}")
def mock_get_product(product_id: str):
    product = load_catalog().get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"product": product.model_dump()}


@router.get("/customers/{user_id}/orders/latest")
def mock_usual_order(user_id: str):
    data = data_loader.load_demo_users()
    orders = [
        o
        for o in data.get("orders", [])
        if o.get("user_id") == user_id and o.get("status") == "completed"
    ]
    if not orders:
        raise HTTPException(status_code=404, detail="No orders")
    latest = max(orders, key=lambda o: o.get("created_at", ""))
    return {
        "order_id": latest.get("order_id"),
        "created_at": latest.get("created_at"),
        "total_inr": latest.get("total_inr"),
        "items": latest.get("items", []),
        "source": "merchant_mock",
    }
