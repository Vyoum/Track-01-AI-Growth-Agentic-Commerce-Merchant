# Track 01 — Conversational Checkout Agent

Razorpay AI Buildathon: AI Growth & Agentic Commerce (merchant demo, **Razorpay test mode only**).

## What's ready

- **Pointer 1** — frozen demo scope + seed JSON
- **Pointer 2** — FastAPI + React scaffold, SQLite, test-mode Razorpay key checks
- **Pointer 3** — deterministic checkout core (no LLM yet)

### Checkout API (Pointer 3)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/products?q=` | Search catalog |
| GET | `/api/users/{id}/usual` | Resolve “the usual” |
| POST | `/api/proposals` | Build validated proposal (server prices) |
| GET | `/api/proposals/{id}` | Fetch proposal |
| POST | `/api/proposals/{id}/confirm` | Gate: exact total → mock payment order |
| POST | `/api/proposals/{id}/cancel` | Cancel proposal |

```bash
# Smoke test
.venv/bin/python scripts/smoke_checkout_core.py
```

- **Pointer 4** — Growth Decision Tool (optional complementary add-on + uplift metrics)

### Growth flow (Pointer 4)

1. `POST /api/proposals` `{ use_usual: true, stated_budget_inr: 800 }` → baseline ₹699 + shaker offer  
2. `POST /api/proposals/{id}/growth/decide` `{ decision: "accept" }` → total ₹798  
3. `POST /api/proposals/{id}/confirm` `{ expected_total_inr: 798 }` → mock payment  

- **Pointer 5–6** — Guardrails (substitute/trim/gates) + audit trail (SQLite/JSONL, redacted)

```bash
.venv/bin/python scripts/smoke_guardrails.py
```

- **Pointer 7** — Razorpay test-mode checkout (real orders when keys set; mock verify fallback)

### Razorpay (Pointer 7)

1. Put real test keys in `.env`:
   ```bash
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   RAZORPAY_MODE=test
   ```
2. Restart API → `/health` shows `"razorpay_test_ready": true`
3. UI: **Start demo** → **Add it** → **Confirm & pay** → Razorpay Checkout (test card)
4. Without keys: same UI auto-verifies with mock signature (no dashboard txn)

- **Pointer 8** — Groq chat agent + tool calling + gated phrases

### Chat demo (Pointer 8)

1. Restart API after setting `GROQ_API_KEY` and `LLM_MODEL=openai/gpt-oss-120b`
2. Open http://127.0.0.1:5173
3. Chat: `Order my usual, under ₹800` → `yes, add it` → `confirm payment`

- **Pointer 9** — Merchant store adapter (mock by default; live HTTP when configured)

### Merchant adapter (Pointer 9)

Default: `USE_MOCK_CATALOG=true` (local JSON).

To exercise the live HTTP adapter against this app’s mock merchant API:

```bash
USE_MOCK_CATALOG=false
STORE_API_BASE_URL=http://127.0.0.1:8000/merchant-mock
STORE_FALLBACK_TO_MOCK=true
```

Contract: [`docs/merchant_api_contract.md`](docs/merchant_api_contract.md)

```bash
.venv/bin/python scripts/smoke_merchant.py
```


## Quick start

```bash
# 1) Env
cp .env.example .env

# 2) Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --app-dir . --port 8000

# 3) Frontend (other terminal)
cd frontend
npm install
npm run dev
```

- API health: http://127.0.0.1:8000/health  
- Chat UI: http://127.0.0.1:5173  

## Folder map

```
backend/
  main.py              # FastAPI entry
  config.py            # env + test-mode Razorpay checks
  db.py                # SQLite init
  agent/               # orchestrator, prompts, guardrails (stubs)
  tools/               # catalog, cart, history, payment, growth (stubs)
  integrations/        # merchant + Razorpay clients (stubs)
  audit/               # audit logger (stub)
  data/                # demo JSON + SQLite file
frontend/
  src/                 # ChatWindow, OrderSummaryCard, api
docs/
  demo_scope.md        # frozen scenarios & acceptance criteria
```

## Safety

- `RAZORPAY_MODE` must be `test`
- `RAZORPAY_KEY_ID` must start with `rzp_test_` when set
- Live keys raise a startup/settings error
