# Independent buyer agent (A2A demo)

This folder is **deliberately separate** from `backend/`. It has no special access —
only the same public HTTP APIs a human-facing frontend would use.

## Flow

1. `GET /.well-known/agent-catalog.json` — discover endpoints & policies
2. `GET /api/products?q=…` — search catalog (buyer's own ranking logic)
3. `POST /api/proposals` — create proposal (`buyer_type: external_agent`)
4. `POST /api/proposals/{id}/campaign/decide` — **merchant desk** (if campaign pending)
5. `POST /api/proposals/{id}/addon` — buyer accepts/skips add-on (autonomous rule)
6. `POST /api/proposals/{id}/confirm` — payment gate (same as human)
7. `POST /api/payments/verify` — mock verify in test mode

## Run (mock catalog hero demo)

Terminal 1 — merchant API with mock catalog:

```bash
USE_MOCK_CATALOG=true uvicorn backend.main:app --reload --port 8000
```

Terminal 2 — buyer agent:

```bash
AUTO_MERCHANT_APPROVE=1 MERCHANT_BASE_URL=http://127.0.0.1:8000 \
  python buyer_agent/run_purchase.py
```

Expected: `prod_protein_bundle` + optional shaker → ₹798, audit event `a2a_purchase_completed`.

## Buyer autonomy

- **Product pick** — `buyer_logic.pick_product()` scores catalog against `BUYER_WANT`
- **Add-on decision** — `should_accept_addon()` accepts complements ≤ ₹150 within budget
- **No backend imports** — only `httpx` + public JSON APIs

## Live Supabase

Set `BUYER_WANT=necklace` (or another term that matches merchant catalog).
The merchant API can stay on Supabase; the buyer agent does not care about the data source.
