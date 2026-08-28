#!/usr/bin/env python3
"""Verify explicit bestseller paths for missing and unavailable usual orders."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings
from backend.db import init_db
from backend.integrations.client_store_client import reset_store_client
from backend.models import CreateProposalRequest, LineItem, Product, UsualOrderResponse
from backend.services import checkout
from backend.services.bestsellers import (
    BESTSELLER_REASON,
    USUAL_UNAVAILABLE_REASON,
)


def _item(product_id: str, name: str, price: int, reason: str) -> LineItem:
    return LineItem(
        product_id=product_id,
        name=name,
        qty=1,
        unit_price_inr=price,
        line_total_inr=price,
        reason=reason,
    )


def main() -> None:
    with patch.dict(os.environ, {"USE_MOCK_CATALOG": "true"}):
        get_settings.cache_clear()
        reset_store_client()
        init_db()

        no_history = checkout.create_proposal(
            CreateProposalRequest(
                user_id="new_user",
                use_usual=True,
                stated_budget_inr=800,
                with_growth=False,
            )
        )
        assert no_history.proposal_source == "bestsellers"
        assert no_history.source_reason == BESTSELLER_REASON
        assert no_history.items
        print("no-history bestseller path ok")

        historical = UsualOrderResponse(
            user_id="returning_user",
            order_id="old_order",
            items=[
                _item(
                    "prod_protein_bundle",
                    "Daily Protein Bundle",
                    699,
                    "matched last purchase",
                )
            ],
            total_inr=699,
            source="most_recent_completed_order",
        )
        out_of_stock = Product(
            id="prod_protein_bundle",
            name="Daily Protein Bundle",
            price_inr=699,
            category="supplements",
            stock=0,
        )

        with (
            patch.object(checkout, "get_usual_order", return_value=historical),
            patch.object(checkout.catalog, "get_product", return_value=out_of_stock),
        ):
            unavailable = checkout.create_proposal(
                CreateProposalRequest(
                    user_id="returning_user",
                    use_usual=True,
                    stated_budget_inr=800,
                    with_growth=False,
                )
            )

        assert unavailable.proposal_source == "bestsellers"
        assert unavailable.source_reason == USUAL_UNAVAILABLE_REASON
        assert all(
            item.product_id != "prod_protein_bundle" for item in unavailable.items
        )
        print("out-of-stock bestseller path ok")

    get_settings.cache_clear()
    reset_store_client()
    print("bestseller fallback smoke passed")


if __name__ == "__main__":
    main()
