# Testing and demo guide

How to run, demo, and test **Track 01 — Conversational Checkout Agent** end to end.

Frozen product scenarios live in [`docs/demo_scope.md`](docs/demo_scope.md). This file is the operational checklist: start the stack, walk the UI, hit the APIs, inspect traces, run smoke scripts, and exercise failure modes.

There is **no LangSmith / Langfuse / OpenTelemetry** in this repo. Agent “trails” are custom: the **AI Commerce Trace** panel in the UI, plus `GET /api/proposals/{id}/audit`, SQLite `audit_events`, and an optional JSONL file.

---

## 1. Start the stack (local demo)

Recommended for a first demo: **mock catalog** so totals match the frozen hero path (usual ₹699 + shaker ₹99 = ₹798). `.env.example` is oriented toward a live Supabase merchant; override that for mock.

### 1.1 Env

```bash
cp .env.example .env
```

For the **mock-catalog hero demo**, set at least:

```bash
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_URL=sqlite:///./backend/data/checkout_agent.db
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

USE_MOCK_CATALOG=true
DEMO_USER_ID=demo_user_01
STORE_FALLBACK_TO_MOCK=true

# Optional — chat uses Groq when set; gates + fallback still work without it
GROQ_API_KEY=
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
LLM_BASE_URL=https://api.groq.com/openai/v1

# Placeholders → local mock verify. Real rzp_test_ keys → Razorpay Checkout + dashboard txn
RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
RAZORPAY_KEY_SECRET=replace_me
RAZORPAY_MODE=test
```

Restart the API after any `.env` change. Settings are loaded from the repo-root `.env` (`backend/config.py`).

### 1.2 Backend (port 8000)

From the **repo root**:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --app-dir . --port 8000
```

Seed JSON is loaded at runtime (no extra seed command):

| File | Role |
|------|------|
| `backend/data/mock_catalog.json` | 15 demo products, stock, complements |
| `backend/data/demo_users.json` | User `demo_user_01` (Aisha Khan) + 3 completed orders |
| `backend/data/guardrail_policy.json` | Budget/item caps, gates, confirmation phrases |
| `backend/data/campaigns.json` | Campaign catalog + copy templates |

SQLite is created on API startup at `backend/data/checkout_agent.db` (proposals, payments, conversations, audit events). Wipe it between clean runs:

```bash
.venv/bin/python scripts/reset_test_env.py
```

### 1.3 Frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/health` and `/api` to `http://127.0.0.1:8000`. There is **no** proxy for `/.well-known`, `/merchant-mock`, or `/docs` — hit those on port 8000.

Optional frontend env (not in `.env.example`): `VITE_API_BASE` in `frontend/src/api.js`. Leave unset so the Vite proxy is used.

### 1.4 Sanity check

| URL | What “good” looks like |
|-----|------------------------|
| http://127.0.0.1:8000/health | `"status": "ok"`, `"razorpay_mode": "test"`, `"use_mock_catalog": true`, `"catalog_source": "mock_json"`, `"demo_user_id": "demo_user_01"` |
| http://127.0.0.1:8000/api/meta | `"merchant": "Demo Fitness Store"`, `"features.checkout_core": true`. `"features.agent"` is true only if `GROQ_API_KEY` is set |
| http://127.0.0.1:8000/docs | FastAPI Swagger (root `/` redirects here) |
| http://127.0.0.1:5173 | Chat UI. Header pill: **Agent online (Groq)** or **API online** |

`razorpay_test_ready` is `true` only when `RAZORPAY_KEY_ID` starts with `rzp_test_` and is **not** a placeholder (`rzp_test_xxxxxxxx` / `xxxx` in the id, secret not `replace_me`).

---

## 2. Recommended demo walkthrough

Two equally valid paths. The **button path** is deterministic (no LLM). The **chat path** is the hero narrative.

Single frontend page: **http://127.0.0.1:5173/** — there is no client-side router. Panels: **Chat** (left), **Merchant Desk** / **Order summary** / **AI COMMERCE TRACE** (right, as the flow progresses).

### Path A — UI buttons (no Groq required)

1. Open http://127.0.0.1:5173
2. Confirm header pill is **API online** (or Groq). Order summary **Backend** section shows Razorpay ready true/false.
3. Click **Start demo: usual under ₹800**
   - Calls `POST /api/proposals` with `user_id=demo_user_01`, `use_usual=true`, `stated_budget_inr=800`, `with_growth=true`
   - **Good:** proposal status `awaiting_merchant_approval`; baseline ₹699; offer Shaker Bottle ₹99 → ₹798; **Merchant Desk** appears
4. In **Merchant Desk**, click **Approve campaign**
   - **Good:** status becomes `awaiting_addon_decision`; **Add it** / **Skip** appear
5. Click **Add it**
   - **Good:** total ₹798; status `awaiting_confirmation`; **Confirm & pay ₹798**
6. Click **Confirm & pay ₹798**
   - **No real keys:** auto mock-verify. Message like `Mock payment verified ₹798. Realized uplift: ₹99.`
   - **Real `rzp_test_` keys:** Razorpay Checkout modal. Pay with a [Razorpay test-mode card](https://razorpay.com/docs/payments/payments/test-card-details/). On success, realized uplift ₹99.
7. Read **AI COMMERCE TRACE** (right column): USER REQUEST → BASE CART → OPPORTUNITY → GROWTH → WHY? → GUARDRAILS (Budget / Stock / Category / Addon gate) → MERCHANT APPROVAL → USER APPROVAL → PAYMENT. Footer should say **All required checks passed**.

### Path B — Chat (hero script from `docs/demo_scope.md`)

Needs mock catalog + `demo_user_01`. With `GROQ_API_KEY`, Groq drives catalog/proposal tools. Without it, fallback still handles “usual” + gates.

1. Chat: `Order my usual, under ₹800`
   - **Good:** usual Daily Protein Bundle ₹699; campaign pending merchant approval (do **not** accept add-on yet)
2. Click **Approve campaign** on Merchant Desk
3. Chat: `yes, add it` (or click **Add it**)
   - **Good:** total ₹798; prompt to confirm payment
4. Chat: `confirm payment` (or **Confirm & pay**)
   - Mock: chat says mock payment verified + realized uplift
   - Real keys: Checkout modal; chat reports payment successful after verify

Skip path (same proposal before confirm): `no, just the usual` or **Skip** → total stays ₹699.

Cancel phrases (`cancel`, `stop`, `no don't pay`) cancel the proposal via the chat gate.

### Path C — A2A buyer agent (separate process)

Terminal 1 — merchant API on mock catalog:

```bash
USE_MOCK_CATALOG=true uvicorn backend.main:app --reload --port 8000
```

Terminal 2:

```bash
AUTO_MERCHANT_APPROVE=1 MERCHANT_BASE_URL=http://127.0.0.1:8000 \
  python buyer_agent/run_purchase.py
```

**Good:** `prod_protein_bundle` + shaker → ₹798, `"status": "a2a_purchase_completed"`. Then:

```bash
curl -s http://127.0.0.1:8000/api/a2a/summary | python3 -m json.tool
```

Expect `external_agent_purchases >= 1` and `total_uplift_inr` including 99 from this run.

Without `AUTO_MERCHANT_APPROVE=1` the buyer agent exits: proposal is waiting on Merchant Desk (or `POST /api/proposals/{id}/campaign/decide`).

---

## 3. Routes, pages, and trails

### 3.1 Frontend (one page)

| What | Where |
|------|--------|
| Chat | http://127.0.0.1:5173/ — panel **Chat** |
| Order summary + **Start demo** / **Add it** / **Skip** / **Confirm & pay** | same URL, **Order summary** |
| Merchant campaign approval | **Merchant Desk** (only when `status === awaiting_merchant_approval`) |
| Decision / guardrail trace | **AI COMMERCE TRACE** (`DecisionCenter`) |
| API status pill | header — Groq vs API-only |

Chat and the demo button **hardcode** `user_id: "demo_user_01"` in `frontend/src/api.js`. They do **not** read `DEMO_USER_ID`. Live-Supabase demos with a merchant customer id must use curl/scripts, not this UI, unless that id is literally `demo_user_01`.

### 3.2 Backend HTTP

Interactive catalog: http://127.0.0.1:8000/docs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Redirect to `/docs` |
| GET | `/health` | Env, catalog source, Razorpay readiness |
| GET | `/api/meta` | Merchant name, feature flags, demo user |
| GET | `/.well-known/agent-catalog.json` | A2A discovery (hit **:8000**, not Vite) |
| GET | `/api/products?q=&category=` | Search catalog |
| GET | `/api/products/{id}` | One product |
| GET | `/api/users/{user_id}/usual` | Latest completed order (“the usual”) |
| POST | `/api/proposals` | Create proposal (server prices) |
| GET | `/api/proposals/{id}` | Fetch proposal (may expire after 10 min TTL) |
| POST | `/api/proposals/{id}/campaign/decide` | Merchant approve/reject campaign |
| GET | `/api/merchant/campaigns/pending` | Queue of `awaiting_merchant_approval` |
| GET | `/api/merchant/campaigns/catalog` | Campaign JSON (`backend/data/campaigns.json`) |
| POST | `/api/proposals/{id}/growth/decide` | Customer accept/skip add-on |
| POST | `/api/proposals/{id}/addon` | Alias of growth/decide (A2A) |
| POST | `/api/proposals/{id}/confirm` | Payment gate → Razorpay/mock order |
| POST | `/api/proposals/{id}/cancel` | Cancel proposal |
| POST | `/api/proposals/{id}/payment/fail` | Mark payment failed (Checkout dismiss) |
| POST | `/api/payments/verify` | Signature verify (real or `mock_ok_`) |
| POST | `/api/chat` | Agent turn (gates first, then Groq/fallback) |
| GET | `/api/proposals/{id}/audit` | Events + `decision_trace` + `gate_traces` |
| GET | `/api/audit?session_id=&user_id=&limit=` | Raw audit events |
| GET | `/api/a2a/summary` | Count of `a2a_purchase_completed` |
| GET | `/merchant-mock/products` | Built-in merchant-shaped mock (adapter tests) |
| GET | `/merchant-mock/products/{id}` | |
| GET | `/merchant-mock/customers/{user_id}/orders/latest` | |

There is **no Razorpay webhook route**. Capture is Checkout.js `handler` → `POST /api/payments/verify`. Dismiss → `POST /api/proposals/{id}/payment/fail`.

### 3.3 Inspect trails when something fails

**In the UI**

After a proposal exists, **AI COMMERCE TRACE** loads `GET /api/proposals/{id}/audit` and shows pass/fail guardrails, merchant/user approval, payment, and (for A2A) an external-agent banner.

**HTTP**

```bash
# Replace PROPOSAL_ID from Order summary
curl -s http://127.0.0.1:8000/api/proposals/PROPOSAL_ID/audit | python3 -m json.tool

curl -s 'http://127.0.0.1:8000/api/audit?limit=50' | python3 -m json.tool

# Chat session id is returned by POST /api/chat as session_id (prefix sess_)
curl -s 'http://127.0.0.1:8000/api/audit?session_id=sess_...&limit=100' | python3 -m json.tool
```

Audit payload fields: `events[]`, `decision_trace` (checks + narrative + `summary.all_required_passed`), `gate_traces`, `checks`, `checks_summary`.

**On disk**

| Store | Path |
|-------|------|
| SQLite | `backend/data/checkout_agent.db` — tables `audit_events`, `conversations`, `proposals`, `payments` |
| JSONL | `backend/audit/audit_log.jsonl` (gitignored; secrets redacted) |

```bash
sqlite3 backend/data/checkout_agent.db \
  "SELECT id, ts, event_type, user_id, session_id FROM audit_events ORDER BY id DESC LIMIT 30;"
```

**Event types you should see on the hero path**

`agent_user_message` → `proposal_source_selected` → `guardrail_evaluated` → `proposal_created` → `campaign_proposed` / `growth_offer_shown` → `decision_trace` → `agent_reply` → `campaign_merchant_approved` → `gate_trace` (merchant) → `addon_accepted` or `addon_skipped` → `gate_trace` (addon) → `payment_confirmation_received` → `payment_order_created` → `payment_verified_paid` (and `a2a_purchase_completed` if `buyer_type=external_agent`).

Failures: `proposal_rejected`, `addon_rejected_by_guardrail`, `razorpay_order_create_failed`, `payment_signature_invalid`, `payment_failed`, `proposal_expired`, `proposal_cancelled`.

Agent-only: `agent_gate_handled` (`handled_by`: `merchant_gate` / `addon_gate` / `payment_gate`).

**Chat response `handled_by`**

| Value | Meaning |
|-------|---------|
| `groq` | LLM tool loop |
| `fallback` / `fallback_after_groq_error` | No key or Groq error; “usual” still works |
| `merchant_gate` / `addon_gate` / `payment_gate` | Deterministic phrase gates (LLM cannot bypass) |

**Not in this repo:** LangSmith, Langfuse, Jaeger, `/metrics`, a merchant admin dashboard, or a dedicated logs UI. FastAPI request logs are the uvicorn console.

---

## 4. Automated tests

There is **no pytest suite, no `tests/` directory, and no frontend/e2e tests** (`frontend/package.json` only has `dev` / `build` / `preview`). Coverage is **smoke scripts** under `scripts/`. Run from the **repo root** with the venv active.

```bash
source .venv/bin/activate
```

| Command | What it covers | Notes |
|---------|----------------|-------|
| `.venv/bin/python scripts/smoke_checkout_core.py` | Search, usual = ₹699, proposal without growth, budget block, confirm, idempotency | Hardcodes `demo_user_01`; expects mock usual ₹699 |
| `.venv/bin/python scripts/smoke_growth.py` | Campaign → merchant approve → accept/skip/tight budget; confirm skipped if real Razorpay keys | Uses `DEMO_USER_ID` from `.env` |
| `.venv/bin/python scripts/smoke_guardrails.py` | Substitute / denied category / trim (mock SKUs); merchant + addon gates; total/user mismatch; audit + redaction | Mock SKU checks skip if catalog is live |
| `.venv/bin/python scripts/smoke_razorpay.py` | Order create; mock verify + fail path **or** prints “complete payment in UI” if real keys | |
| `.venv/bin/python scripts/smoke_agent.py` | Offline gates (forces mock catalog): merchant blocks “yes, add it”, then addon → 798 → confirm. Optional live Groq if `GROQ_API_KEY` | |
| `.venv/bin/python scripts/smoke_merchant.py` | Product mapper, mock source, HTTP adapter vs `/merchant-mock`, fake Supabase headers | No live network to your project |
| `.venv/bin/python scripts/smoke_bestseller_fallback.py` | No history → bestsellers; usual OOS → bestsellers (not labeled as usual) | Forces mock catalog |
| `.venv/bin/python scripts/smoke_a2a.py` | Manifest → buyer pick → approve → addon → confirm → mock verify → `a2a_purchase_completed` | Forces mock catalog + empty Razorpay keys |
| `.venv/bin/python scripts/test_supabase_live.py` | Live `/health`, `/api/meta`, products, usual, one proposal | Requires real Supabase `.env`; hardcodes user `cmqm2iwuy0000kvsqj1nqf090` |
| `.venv/bin/python scripts/reset_test_env.py` | Deletes and recreates SQLite | |

Suggested pre-demo batch (mock catalog, placeholder Razorpay keys):

```bash
export USE_MOCK_CATALOG=true
.venv/bin/python scripts/reset_test_env.py
.venv/bin/python scripts/smoke_checkout_core.py
.venv/bin/python scripts/smoke_growth.py
.venv/bin/python scripts/smoke_guardrails.py
.venv/bin/python scripts/smoke_razorpay.py
.venv/bin/python scripts/smoke_agent.py
.venv/bin/python scripts/smoke_merchant.py
.venv/bin/python scripts/smoke_bestseller_fallback.py
.venv/bin/python scripts/smoke_a2a.py
```

Each successful smoke prints a JSON `status` like `pointer3_smoke_passed` / `a2a_smoke_passed`. Non-zero exit = failed assert.

`scripts/smoke_growth.py` and `scripts/smoke_guardrails.py` **skip payment confirm** when real test keys are configured (`keys_are_usable()`); use `smoke_razorpay.py` or the UI for payment.

Buyer agent (needs API already running): see Path C above and [`buyer_agent/README.md`](buyer_agent/README.md).

---

## 5. Manual test plan

Use mock catalog unless a case says otherwise. After each case, glance at **AI COMMERCE TRACE** and/or `/api/proposals/{id}/audit`.

### 5.1 Happy path (human)

| # | Steps | Good |
|---|--------|------|
| H1 | Path A buttons, mock keys | Paid ₹798, realized uplift ₹99, mock payment, all required checks passed |
| H2 | Path B chat with Groq | Same totals; chat never claims the add-on was auto-added; merchant approve before accept |
| H3 | Path B **without** Groq | Fallback still builds usual + campaign; pill is **API online**; later `yes, add it` / `confirm payment` still gated |
| H4 | Skip add-on | Total ₹699; projected uplift may still show on the offer, **realized** 0 after pay |
| H5 | Merchant **Reject** | Status `awaiting_confirmation` on baseline only; no add-on buttons; pay ₹699 |

### 5.2 Payments (Razorpay)

| # | Steps | Good |
|---|--------|------|
| P1 | Placeholder keys, Confirm & pay | No Checkout modal; auto `POST /api/payments/verify` with `mock_ok_…`; `payment.mock === true` |
| P2 | Real `rzp_test_` keys, restart API | `/health` → `razorpay_test_ready: true`; Checkout modal; dashboard shows test order |
| P3 | Dismiss Checkout | `POST .../payment/fail` reason `user_cancelled`; message to create a new proposal / say confirm again; no duplicate charge |
| P4 | Confirm twice with same `idempotency_key` | Second response `idempotent_replay: true` (smoke_checkout_core covers this) |
| P5 | Confirm with wrong `expected_total_inr` | HTTP 409 `total_mismatch` |
| P6 | Confirm as a different `user_id` | HTTP 403 `user_mismatch` |
| P7 | Confirm while `awaiting_merchant_approval` | 409 `merchant_gate` |
| P8 | Confirm while `awaiting_addon_decision` | 409 `addon_gate` |
| P9 | Invalid signature on verify | `payment_signature_invalid`; payment failed |
| P10 | `RAZORPAY_MODE=live` or `rzp_live_…` key | Settings error at startup — live keys are rejected |

There is **no webhook** to test. `payment_max_retries` in policy is **1 automatic retry on `order.create`**, not a second Checkout attempt after user cancel.

### 5.3 Agent / commerce

| # | Steps | Good |
|---|--------|------|
| A1 | Chat `yes, add it` **before** merchant approve | `handled_by: merchant_gate`; cart unchanged |
| A2 | Chat `confirm payment` while add-on pending | Blocked; must accept or skip first |
| A3 | Bare `yes` after add-on question | Treated as add-on accept, **not** payment |
| A4 | New user / unknown usual via API `user_id=new_user`, `use_usual=true` | `proposal_source: bestsellers`; reason is *popular picks*, not “your usual” |
| A5 | Tight budget: Start demo equivalent with `stated_budget_inr` = baseline (e.g. 699) | `growth_offer` null; status `awaiting_confirmation` (no Merchant Desk) |
| A6 | A2A Path C | Audit event `a2a_purchase_completed`; Decision Center A2A block if that proposal is loaded |
| A7 | `GET /.well-known/agent-catalog.json` on **:8000** | `checkout_protocol: proposal-confirm-v1`; policies require confirmation, addon decision, merchant approval |

### 5.4 Catalog / merchant adapter

| # | Steps | Good |
|---|--------|------|
| M1 | `GET /api/products?q=protein` (mock) | Includes `prod_protein_bundle` |
| M2 | `GET /api/users/demo_user_01/usual` (mock) | `ord_demo_003`, total 699, Daily Protein Bundle |
| M3 | REST adapter against built-in mock (API running) | In `.env`: `USE_MOCK_CATALOG=false`, `STORE_PROVIDER=rest`, `STORE_API_BASE_URL=http://127.0.0.1:8000/merchant-mock`. `/health` → `catalog_source: merchant_api`. Usual still 699 |
| M4 | Live Supabase | See §7. Then `scripts/test_supabase_live.py`. `/health` → `catalog_source: supabase`. Totals **may not** be 699/798 — they come from the merchant DB |
| M5 | Unreachable store + `STORE_FALLBACK_TO_MOCK=true` | Warning in API logs; mock JSON used |

### 5.5 Guardrails and edge cases

Mock-catalog SKUs from `docs/demo_scope.md` / `mock_catalog.json`:

| # | Input | Good |
|---|--------|------|
| G1 | `prod_gift_card` or `prod_alcohol_clean` in `product_ids` | Denied category; proposal rejected or item stripped with a reason |
| G2 | `prod_protein_oos` with `allow_substitute: true` | Substitute to in-stock sibling; reason in proposal + audit |
| G3 | Bundle + oats with `stated_budget_inr: 800`, `allow_trim: true` | Trim with one-line reason |
| G4 | Bundle + creatine, budget 800, no trim | Budget exceeded — proposal rejected |
| G5 | Wait > 10 minutes (`proposal_ttl_seconds: 600`) then confirm | 410 `expired` |
| G6 | Chat `cancel` on a live proposal | Status `cancelled`; cannot pay |
| G7 | Paid proposal cancel | 409 “Paid proposals cannot be cancelled” |
| G8 | Confirm after skip, then try `yes, add it` | Payment gate: no pending add-on |

### 5.6 Chat curl (gates without UI)

```bash
# 1) Create session + usual proposal (fallback or Groq)
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Order my usual, under ₹800","user_id":"demo_user_01"}'

# 2) Approve (use proposal id from step 1)
curl -s http://127.0.0.1:8000/api/proposals/PROPOSAL_ID/campaign/decide \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","note":"demo"}'

# 3) Accept add-on via chat (pass session_id from step 1)
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"yes, add it","session_id":"SESSION_ID","user_id":"demo_user_01"}'

# 4) Confirm
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"confirm payment","session_id":"SESSION_ID","user_id":"demo_user_01"}'
```

If mock payment, verify:

```bash
curl -s http://127.0.0.1:8000/api/payments/verify \
  -H 'Content-Type: application/json' \
  -d '{"payment_id":"pay_...","razorpay_order_id":"order_mock_...","razorpay_payment_id":"pay_mock_1","razorpay_signature":"mock_ok_demo"}'
```

Mock verify only accepts signatures starting with `mock_ok_` and order ids starting with `order_mock_`.

### 5.7 API-only hero (no chat)

```bash
curl -s http://127.0.0.1:8000/api/proposals \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo_user_01","use_usual":true,"stated_budget_inr":800,"with_growth":true,"session_id":"demo_api"}'

curl -s http://127.0.0.1:8000/api/proposals/PROPOSAL_ID/campaign/decide \
  -H 'Content-Type: application/json' -d '{"decision":"approve"}'

curl -s http://127.0.0.1:8000/api/proposals/PROPOSAL_ID/growth/decide \
  -H 'Content-Type: application/json' \
  -d '{"decision":"accept","product_id":"prod_shaker"}'

curl -s http://127.0.0.1:8000/api/proposals/PROPOSAL_ID/confirm \
  -H 'Content-Type: application/json' \
  -d '{"expected_total_inr":798,"user_id":"demo_user_01","idempotency_key":"demo-1"}'
```

Then mock-verify as above. **Good:** amount 798, `growth_summary.projected_uplift_inr` 99, `realized_paid_uplift` 99 after verify (null until paid).

---

## 6. What “good” looks like (acceptance)

Matches [`docs/demo_scope.md`](docs/demo_scope.md) and `guardrail_policy.json`:

1. **No charge before gate** — Razorpay `order.create` only after confirm of **this** proposal id + exact total.
2. **No silent upsell** — add-on is optional; merchant must approve campaign first; customer accept/skip is a second step.
3. **Budget** — proposed total ≤ stated budget and ≤ ₹5000 hard max.
4. **Explainable** — skip/trim/substitute/recommend has a one-line reason in chat/proposal and in the audit/Decision Center.
5. **Audit** — money actions logged; secrets redacted (`***REDACTED***` / `rzp_test_***`).
6. **Uplift honesty** — projected vs realized; realized stays `null` until verify.
7. **Failure** — cancel/decline ends with a clear message; no hang; new proposal to retry (policy: one automatic `order.create` retry only).
8. **Test mode only** — live Razorpay keys refused.

Hero numbers (mock catalog only): usual **₹699**, shaker **₹99**, paid **₹798**, uplift **₹99 (~14.2%)**.

---

## 7. Catalog modes (do not mix them blindly)

### Mock (deterministic demo)

```bash
USE_MOCK_CATALOG=true
DEMO_USER_ID=demo_user_01
```

Usual = order `ord_demo_003`. Complements prefer `prod_shaker`.

### Live Supabase

`.env.example` defaults (`USE_MOCK_CATALOG=false`, `STORE_PROVIDER=supabase`, `DEMO_USER_ID=cmqm2iwuy0000kvsqj1nqf090`). Fill `SUPABASE_URL` + `SUPABASE_KEY` (service role, backend only). Contract: [`docs/merchant_api_contract.md`](docs/merchant_api_contract.md).

```bash
.venv/bin/python scripts/test_supabase_live.py
```

That script **hardcodes** user `cmqm2iwuy0000kvsqj1nqf090`. The **frontend still posts `demo_user_01`**, so the UI will not follow that merchant user.

Completed-order filter in `.env.example` is `SUPABASE_ORDER_COMPLETED_STATUS=DELIVERED`. Code default if unset is `completed`. A mismatch yields empty usual → bestseller fallback.

### Custom REST

`STORE_PROVIDER=rest` + `STORE_API_BASE_URL`. Local loopback: `http://127.0.0.1:8000/merchant-mock`.

---

## 8. Gotchas

| Symptom | Likely cause |
|---------|----------------|
| Hero totals are not 699/798 | Live catalog, or UI user `demo_user_01` has no mock usual (Supabase mode) |
| UI ignores `DEMO_USER_ID` | Hardcoded in `frontend/src/api.js` |
| Pill never says Groq / chat is shallow | Missing `GROQ_API_KEY`; restart API. Fallback still handles “usual” |
| Chat works but add-on never offered | Merchant Desk not approved; or budget = baseline so no offer |
| Confirm 409 merchant/addon gate | Clicked pay too early |
| `razorpay_test_ready: false` but keys “set” | Placeholder `rzp_test_xxxxxxxx` / `replace_me` / `xxxx` in key id |
| Startup error about live keys | `RAZORPAY_MODE` not `test`, or `rzp_live_` id |
| Checkout.js fails to load | Network/adblock; script is `https://checkout.razorpay.com/v1/checkout.js` |
| CORS error | Origin not in `CORS_ORIGINS`; use `http://127.0.0.1:5173` or `http://localhost:5173` |
| `/.well-known/agent-catalog.json` 404 on :5173 | Vite does not proxy it — use :8000 |
| Smoke checkout fails usual ≠ 699 | `.env` pointed at live store; force `USE_MOCK_CATALOG=true` or expect skip/fail |
| Growth/guardrail smokes skip confirm | Real test keys present — by design |
| Stale proposals / weird audit | Old SQLite; run `scripts/reset_test_env.py` |
| Proposal suddenly expired | 10 minute TTL |
| A2A dies on merchant approval | Set `AUTO_MERCHANT_APPROVE=1` or approve in UI |
| Groq replies invent products | Should not — tools only; if it happens, check tool_trace in `agent_reply` audit and that tools returned catalog ids |
| Settings cache after `.env` edit | Restart uvicorn (`get_settings` is lru-cached) |

Phrase lists (chat gates) are in `backend/data/guardrail_policy.json` → `confirmation_wording`.

---

## 9. What this repo does not have

Documented so you do not hunt for them:

- No pytest / Jest / Playwright / Cypress
- No LangSmith, Langfuse, or distributed tracing product
- No Razorpay webhooks
- No Docker Compose / production deploy guide in-repo
- No multi-user auth or OTP (`docs/demo_scope.md` out of scope)
- No separate merchant admin app (Merchant Desk is an in-page campaign approve/reject)
- No frontend tests

Related docs: [`README.md`](README.md) (quick start), [`docs/demo_scope.md`](docs/demo_scope.md) (scenarios), [`docs/merchant_api_contract.md`](docs/merchant_api_contract.md) (store adapter), [`buyer_agent/README.md`](buyer_agent/README.md) (A2A).
