"""User order history — mock JSON or merchant API via store_source."""

from __future__ import annotations

from backend.models import UsualOrderResponse
from backend.services import store_source


def get_usual_order(user_id: str) -> UsualOrderResponse:
    return store_source.get_usual_order(user_id)
