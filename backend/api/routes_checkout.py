"""HTTP routes for deterministic checkout core."""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.models import (
    ConfirmationRequest,
    CreateProposalRequest,
    SearchProductsResponse,
)
from backend.services import catalog, checkout, history

router = APIRouter(prefix="/api", tags=["checkout"])


@router.get("/products", response_model=SearchProductsResponse)
def api_search_products(
    q: str = Query(default=""),
    category: str | None = None,
):
    products = catalog.search_products(query=q, category=category)
    return SearchProductsResponse(products=products, count=len(products))


@router.get("/products/{product_id}")
def api_get_product(product_id: str):
    product = catalog.get_product(product_id)
    if not product:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/users/{user_id}/usual")
def api_usual_order(user_id: str):
    return history.get_usual_order(user_id)


@router.post("/proposals")
def api_create_proposal(body: CreateProposalRequest):
    return checkout.create_proposal(body)


@router.get("/proposals/{proposal_id}")
def api_get_proposal(proposal_id: str):
    return checkout.get_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/confirm")
def api_confirm_proposal(proposal_id: str, body: ConfirmationRequest):
    return checkout.confirm_proposal(proposal_id, body)


@router.post("/proposals/{proposal_id}/cancel")
def api_cancel_proposal(proposal_id: str):
    return checkout.cancel_proposal(proposal_id)
