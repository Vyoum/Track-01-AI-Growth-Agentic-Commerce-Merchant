# Merchant store contract (Pointer 9)

The checkout agent reads **products** and **latest order for a user** from the merchant.
Two connection modes are supported.

---

## Option A — Supabase (PostgREST) — recommended for this merchant

Set in `.env`:

```bash
USE_MOCK_CATALOG=false
STORE_PROVIDER=supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your_service_role_key
DEMO_USER_ID=<merchant demo customer id>
STORE_FALLBACK_TO_MOCK=true
```

PostgREST endpoints used automatically:

| Purpose | Request |
|---------|---------|
| List products | `GET /rest/v1/products?select=*` |
| One product | `GET /rest/v1/products?id=eq.{id}&select=*&limit=1` |
| Latest order | `GET /rest/v1/orders?user_id=eq.{user}&order=created_at.desc&limit=1&select=*,order_items(*)` |

**Headers** (set by the backend — do not expose keys in the frontend):

```
apikey: {SUPABASE_KEY}
Authorization: Bearer {SUPABASE_KEY}
```

**Product row fields** (aliases in parentheses):

- `id` (`product_id`, `sku`)
- `name` (`title`)
- `price_inr` (`price`; or `price_paise / 100`)
- `category` (`product_type`)
- `stock` — derived from `inventory.quantity - reserved_quantity` summed by `product_id`
- `tags[]`, `complements[]` (optional JSON)

**Inventory table** (live merchant):

```bash
SUPABASE_INVENTORY_TABLE=inventory
SUPABASE_INVENTORY_PRODUCT_COLUMN=product_id
SUPABASE_INVENTORY_QUANTITY_COLUMN=quantity
SUPABASE_INVENTORY_RESERVED_COLUMN=reserved_quantity
```

Stock is aggregated across size/SKU rows per product. A product with no inventory rows is treated as out of stock.

**Order row fields:**

- `id` or `order_id`
- `user_id` (or override `SUPABASE_ORDER_USER_COLUMN`)
- `total_inr` (`total`)
- nested `order_items[]` or separate `order_items` table with `order_id` FK

**Table overrides** (if schema differs):

```bash
SUPABASE_PRODUCTS_TABLE=products
SUPABASE_ORDERS_TABLE=orders
SUPABASE_ORDER_ITEMS_TABLE=order_items
SUPABASE_ORDER_ITEMS_FK=order_id
SUPABASE_ORDER_USER_COLUMN=user_id
SUPABASE_ORDER_STATUS_COLUMN=status
SUPABASE_ORDER_COMPLETED_STATUS=DELIVERED
SUPABASE_ORDER_DATE_COLUMN=created_at
```

You can also set `SUPABASE_URL` / `SUPABASE_KEY` via `STORE_API_BASE_URL` / `STORE_API_KEY`.

---

## Option B — Custom REST API

```bash
USE_MOCK_CATALOG=false
STORE_PROVIDER=rest
STORE_API_BASE_URL=https://api.merchant.com/v1
STORE_API_KEY=
```

Auth: `Authorization: Bearer {STORE_API_KEY}` (optional)

| Endpoint | Response |
|----------|----------|
| `GET /products?q=&category=` | `{ "products": [ Product, ... ] }` or bare array |
| `GET /products/{id}` | `{ "product": Product }` or Product |
| `GET /customers/{user_id}/orders/latest` | order object with `items[]` |

Path overrides: `STORE_PRODUCTS_PATH`, `STORE_PRODUCT_PATH`, `STORE_USUAL_ORDER_PATH`.

---

## Local demo (no external merchant)

```bash
USE_MOCK_CATALOG=false
STORE_PROVIDER=rest
STORE_API_BASE_URL=http://127.0.0.1:8000/merchant-mock
STORE_FALLBACK_TO_MOCK=true
```

Health check: `GET /health` → `"catalog_source": "supabase"` or `"merchant_api"`.
