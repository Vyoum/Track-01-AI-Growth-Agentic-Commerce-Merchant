# Demo Scope — Frozen (Pointer 1)

Built for: Razorpay AI Buildathon, Track 01 — AI Growth & Agentic Commerce  
Mode: Razorpay **test-mode only**  
Merchant: demo e-commerce catalog (mock first; live client API later)

---

## Primary demo scenarios

### Scenario A — Revenue-increasing add-on (hero demo)

| Step | What happens |
|------|----------------|
| User says | `"Order my usual, under ₹800"` |
| History resolves | Usual cart = **Daily Protein Bundle** @ ₹699 |
| Growth tool | Finds complementary **Shaker Bottle** @ ₹99 (commonly bought with it) |
| Projected total | ₹699 + ₹99 = **₹798** |
| Guardrail | ₹798 < ₹800 → pass |
| Agent asks | Add-on offer with reason (does **not** auto-add) |
| User says | `"Yes, add it"` |
| Agent asks | Final payment confirmation for ₹798 |
| User says | `"Confirm payment"` / Confirm button |
| Razorpay | Test-mode order for **₹798** |
| Metric shown | Baseline ₹699 → paid ₹798 → uplift **₹99 (~14.2%)** |

### Scenario B — Budget / stock adjustment

| Step | What happens |
|------|----------------|
| User says | `"Order my usual, under ₹700"` |
| History resolves | Usual = ₹699 |
| Growth tool | Best add-on is ₹99 → **₹798 exceeds budget** → no add-on offered (or cheaper eligible add-on if any) |
| Optional stock fail | If usual SKU is marked OOS in demo flag → substitute closest in-stock match with explanation |
| Guardrail | Trim / skip with one-line reason in chat + audit |

### Scenario C — Failed payment (graceful)

| Step | What happens |
|------|----------------|
| Flow | Same as A through confirmation |
| Failure | Simulate Razorpay test failure / cancel |
| Agent | One retry, then clear failure message — no silent hang, no duplicate charge |

---

## Demo user

| Field | Value |
|-------|--------|
| `user_id` | `demo_user_01` |
| Name | Aisha Khan |
| Phone (demo) | `+91-9000000001` |
| Default budget cue | ₹800 (from utterance; policy max still applies) |
| Past orders | 3 completed orders (see `backend/data/demo_users.json`) |
| “Usual” definition | Most recent completed order’s line items (order `ord_demo_003`) |

---

## Catalog snapshot (15 products)

Full SKUs live in `backend/data/mock_catalog.json`.

| ID | Name | Price (₹) | Category | Role in demo |
|----|------|-----------|----------|--------------|
| `prod_protein_bundle` | Daily Protein Bundle | 699 | supplements | **Usual order** |
| `prod_shaker` | Shaker Bottle | 99 | accessories | **Hero add-on** |
| `prod_creatine` | Creatine Monohydrate 250g | 449 | supplements | Alt add-on (over budget with usual under ₹800) |
| `prod_multivitamin` | Daily Multivitamin 60s | 349 | supplements | Complementary |
| `prod_oats` | Rolled Oats 1kg | 249 | grocery | Complementary |
| `prod_peanut_butter` | Natural Peanut Butter 500g | 299 | grocery | Complementary |
| `prod_whey_sample` | Whey Sample Sachet | 79 | supplements | Cheap add-on for Scenario B |
| `prod_yoga_mat` | Yoga Mat | 799 | fitness | Over-budget with usual |
| `prod_resistance_band` | Resistance Band Set | 399 | fitness | Complementary fitness |
| `prod_green_tea` | Green Tea 100 bags | 199 | grocery | Complementary |
| `prod_electrolyte` | Electrolyte Drink Mix | 149 | supplements | Complementary |
| `prod_protein_bar` | Protein Bar Box (6) | 299 | grocery | Complementary |
| `prod_gift_card` | Store Gift Card ₹500 | 500 | gift_cards | **Denied category** |
| `prod_alcohol_clean` | Cocktail Mixer Kit | 599 | alcohol_adjacent | **Denied category** |
| `prod_protein_oos` | Limited Edition Flavour | 699 | supplements | **OOS** substitute demo |

---

## Complementary relationships (growth rules)

Defined on each product as `complements` + `priority` in catalog JSON.

Hero path:

- `prod_protein_bundle` → prefers `prod_shaker` (priority 1), then `prod_whey_sample`, `prod_electrolyte`, etc.

Eligibility for any recommendation (deterministic):

1. Listed as complement of an item already in the base cart  
2. In stock (`stock > 0`)  
3. Category not denied  
4. Not already in cart  
5. `base_total + price <= stated_budget`  
6. Cart item count after add ≤ `max_items`  
7. Not rejected earlier in the same session  

---

## Guardrail policy (frozen values)

Source of truth: `backend/data/guardrail_policy.json`

| Rule | Value |
|------|--------|
| Hard max order value | ₹5,000 (server ceiling even if user says more) |
| Demo stated budget (Scenario A) | ₹800 from user utterance |
| Max items per auto-proposal | 5 |
| Denied categories | `gift_cards`, `alcohol_adjacent` |
| Payment gate | Explicit confirmation of **exact proposal_id + total** |
| Add-on gate | Explicit accept/skip **before** payment confirmation |
| Proposal TTL | 10 minutes |
| Payment retry | Max 1 automatic retry on transient failure |
| Razorpay mode | `test` only; reject live keys |

### Confirmation wording (exact phrases for UI + agent)

**Add-on offer (agent):**

> I found your usual order for ₹699 (Daily Protein Bundle). I also found Shaker Bottle for ₹99, which is commonly bought with it. Total would be ₹798, still under your ₹800 budget. Want me to add it?

**Accept add-on:** user `"Yes, add it"` / button **Add to order**  
**Skip add-on:** user `"No, just the usual"` / button **Skip**

**Payment confirmation (agent):**

> Ready to pay ₹798 for: Daily Protein Bundle (₹699) + Shaker Bottle (₹99). Confirm payment?

**Confirm payment:** user `"Confirm payment"` / button **Confirm & pay**  
**Cancel:** user `"Cancel"` / button **Cancel**

Rule: `"yes"` after an add-on question only accepts the add-on. Payment requires a separate confirm on the latest proposal.

---

## Acceptance criteria (definition of done for scope)

1. **No payment without gate** — Razorpay order/payment APIs are never called until the user confirms the exact current proposal (id + total).  
2. **No silent upsell** — Growth recommendations are always optional and explained; cart is never silently padded to budget.  
3. **Budget bound** — Final proposed total never exceeds the user’s stated budget or the hard ceiling.  
4. **Explainable** — Every skip, trim, substitution, or recommendation has a one-line reason in the reply and in the audit trail.  
5. **Audit every money action** — User message, tool results, recommendation, gates, confirmation, Razorpay ids/status, failures/retries are logged (secrets redacted).  
6. **Uplift honesty** — Demo metrics distinguish *projected* uplift (offer shown) vs *realized* uplift (successfully paid).  
7. **One failure path** — Scenario C ends with a clear failure message after at most one retry.  
8. **Test mode only** — Non-test Razorpay credentials are rejected at startup / payment time.

---

## Out of scope for v1 demo

- Live production Razorpay keys  
- Full merchant admin dashboard  
- Multi-user auth / real OTP  
- LLM inventing products not in catalog  
- Auto-charging without confirmation  

---

## Seed files

| File | Purpose |
|------|---------|
| `backend/data/mock_catalog.json` | Products, stock, complements |
| `backend/data/demo_users.json` | Demo user + past orders |
| `backend/data/guardrail_policy.json` | Bounds, gates, wording keys |
| `docs/demo_scope.md` | This freeze document |
