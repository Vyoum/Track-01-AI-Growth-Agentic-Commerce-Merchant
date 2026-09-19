# Independent buyer agent (A2A)

Public HTTP only — no `backend/` imports.

## ACP flow (default)

1. `GET /.well-known/acp.json`
2. `GET /api/products?q=…`
3. `POST /checkout_sessions` (`API-Version: 2026-04-17`)
4. `POST /checkout_sessions/{id}` with `buyer_actions` (campaign approve / addon accept|skip)
5. `POST /checkout_sessions/{id}/complete` with demo SPT `{ type: spt_demo, token: spt_demo_… }`

Same catalog prices, addon gate, mandate CAS as humans. This is an **ACP-shaped adapter**, not ChatGPT Instant Checkout / Stripe Shared Payment Tokens.

```bash
AUTO_MERCHANT_APPROVE=1 MERCHANT_BASE_URL=http://127.0.0.1:8000 \
  python buyer_agent/run_purchase.py
```

Legacy `proposal-confirm-v1` routes still exist for curl/debug.
