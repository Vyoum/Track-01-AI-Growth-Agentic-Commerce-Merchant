# Merchant store API contract (Pointer 9)
#
# Point your store at these endpoints (or set path overrides in .env).
# Auth: Authorization: Bearer <STORE_API_KEY> (optional)
#
# GET /products?q=&category=
#   Response: { "products": [ Product, ... ] }
#   OR a bare array of products.
#
# GET /products/{id}
#   Response: { "product": Product } OR Product
#
# GET /customers/{user_id}/orders/latest
#   Response: {
#     "order_id": "...",
#     "total_inr": 699,
#     "items": [
#       { "product_id": "...", "name": "...", "qty": 1, "unit_price_inr": 699 }
#     ]
#   }
#
# Product fields (accepted aliases in parentheses):
#   id (product_id, sku)
#   name (title)
#   price_inr (price; or price_paise / 100)
#   category (product_type)
#   stock (inventory, quantity)
#   tags[]
#   complements[]: { product_id, priority, reason } OR string ids
#   substitute_with (optional)
#
# Local demo without a real merchant:
#   USE_MOCK_CATALOG=false
#   STORE_API_BASE_URL=http://127.0.0.1:8000/merchant-mock
#   STORE_FALLBACK_TO_MOCK=true
