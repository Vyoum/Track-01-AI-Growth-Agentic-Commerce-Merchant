"""User history tool."""

from __future__ import annotations

from backend.services.history import get_usual_order


def get_usual(user_id: str):
    return get_usual_order(user_id)
