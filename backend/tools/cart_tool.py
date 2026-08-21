"""Cart tool — builds server-priced carts."""

from __future__ import annotations

from backend.services.cart import build_cart


def build_cart_from_ids(
    user_id: str,
    product_ids: list[str],
    quantities: dict[str, int] | None = None,
):
    return build_cart(user_id=user_id, product_ids=product_ids, quantities=quantities)


def view_cart(*_args, **_kwargs):
    raise NotImplementedError("Session cart view arrives with agent sessions later")
