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

Payment orders are **mock** (`order_mock_…`) until the Razorpay pointer.

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
