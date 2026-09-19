/**
 * Browser A2A buyer — ACP checkout sessions (same as buyer_agent/run_purchase.py).
 */

import { searchProducts } from "./api.js";

const ACP_VERSION = "2026-04-17";
const API_BASE = import.meta.env.VITE_API_BASE || "";

function scoreProduct(product, goal) {
  const want = String(goal.want || "")
    .trim()
    .toLowerCase();
  const budget = Number(goal.budget_inr) || 0;
  const price = Number(product.price_inr) || 0;
  const stock = Number(product.stock) || 0;

  if (stock < 1 || price > budget) return -1;

  const haystack = [
    product.id,
    product.name,
    product.category,
    ...(product.tags || []),
  ]
    .join(" ")
    .toLowerCase();

  let score = 0;
  if (want && haystack.includes(want)) score += 10;
  for (const token of want.split(/\s+/)) {
    if (token.length > 2 && haystack.includes(token)) score += 3;
  }
  if (price <= budget) score += 1;
  return score;
}

export function pickProduct(products, goal) {
  const ranked = [...products].sort(
    (a, b) => scoreProduct(b, goal) - scoreProduct(a, goal)
  );
  if (!ranked.length) return null;
  const best = ranked[0];
  return scoreProduct(best, goal) >= 0 ? best : null;
}

export function shouldAcceptAddon(offer, goal, maxAddonInr = 150) {
  if (!offer) return false;
  const price = Number(offer.price_inr) || 0;
  const projected = Number(offer.projected_total_inr) || 0;
  const budget = Number(goal.budget_inr) || 0;
  return price <= maxAddonInr && projected <= budget;
}

function acpHeaders(idempotency) {
  const headers = {
    "Content-Type": "application/json",
    "API-Version": ACP_VERSION,
    Authorization: "Bearer demo-key",
  };
  if (idempotency) headers["Idempotency-Key"] = idempotency;
  return headers;
}

async function acpJson(path, { method = "GET", body, idempotency } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: acpHeaders(idempotency),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.message || res.statusText;
    const err = new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail)
    );
    err.status = res.status;
    err.detail = data.detail || data;
    throw err;
  }
  return data;
}

export async function runBuyerPurchase({
  want = "protein",
  budgetInr = 800,
  userId = "external_buyer_01",
  maxAddonInr = 150,
  autoMerchantApprove = true,
  onStep,
}) {
  const goal = { want, budget_inr: budgetInr, user_id: userId };
  const trace = [];
  const step = (msg) => {
    trace.push(msg);
    onStep?.(msg);
  };

  const acp = await fetch(`${API_BASE}/.well-known/acp.json`).then((r) => r.json());
  step(
    `discovered acp ${acp.api_version} partner=${acp.instant_checkout_partner}`
  );

  const productsResp = await searchProducts(want);
  const products = productsResp.products || [];
  if (!products.length) throw new Error(`No products for query=${want}`);

  const chosen = pickProduct(products, goal);
  if (!chosen) throw new Error("No in-stock product fits buyer goal/budget");
  step(`selected ${chosen.id} (${chosen.name}) ₹${chosen.price_inr}`);

  let session = await acpJson("/checkout_sessions", {
    method: "POST",
    idempotency: `acp-ui-${crypto.randomUUID().slice(0, 8)}`,
    body: {
      currency: "inr",
      line_items: [{ id: chosen.id, quantity: 1 }],
      stated_budget_inr: budgetInr,
      buyer: { id: userId },
      with_growth: true,
    },
  });
  const sid = session.id;
  step(`session ${sid} status=${session.status}`);

  async function resolveInputs() {
    const codes = new Set((session.messages || []).map((m) => m.code));
    const actions = {};
    if (codes.has("merchant_approval_required")) {
      if (!autoMerchantApprove) {
        throw new Error("Awaiting merchant approval — enable auto-approve");
      }
      actions.campaign = "approve";
    }
    if (codes.has("addon_decision_required")) {
      const addonMsg = (session.messages || []).find(
        (m) => m.code === "addon_decision_required"
      );
      const offer = addonMsg?.addon || {};
      actions.addon = shouldAcceptAddon(offer, goal, maxAddonInr)
        ? "accept"
        : "skip";
    }
    if (Object.keys(actions).length) {
      session = await acpJson(`/checkout_sessions/${sid}`, {
        method: "POST",
        body: { buyer_actions: actions },
      });
      step(`updated ${JSON.stringify(actions)} → ${session.status}`);
    }
  }

  if (session.status === "needs_input") {
    await resolveInputs();
    if (session.status === "needs_input") await resolveInputs();
  }

  const completeBody = {
    payment_data: {
      instrument: { type: "spt_demo", token: "spt_demo_a2a" },
    },
  };
  try {
    session = await acpJson(`/checkout_sessions/${sid}/complete`, {
      method: "POST",
      idempotency: `acp-pay-${sid}`,
      body: completeBody,
    });
  } catch (err) {
    if (err.status === 409 && err.detail?.code === "MANDATE_CAP_WARNING") {
      step("impulse warning — completing with ack");
      completeBody.acknowledge_impulse_warning = true;
      session = await acpJson(`/checkout_sessions/${sid}/complete`, {
        method: "POST",
        idempotency: `acp-pay-ack-${sid}`,
        body: completeBody,
      });
    } else {
      throw err;
    }
  }

  const total = session.totals?.total_inr;
  step(`complete status=${session.status} total ₹${total}`);

  const proposalId = session.proposal_id;
  let proposal = null;
  try {
    proposal = await fetch(`${API_BASE}/api/proposals/${proposalId}`).then((r) =>
      r.json()
    );
  } catch {
    proposal = { id: proposalId, total_inr: total, status: session.status };
  }

  return {
    status:
      session.status === "completed"
        ? "a2a_purchase_completed"
        : session.status,
    proposal_id: proposalId,
    checkout_session_id: sid,
    proposal,
    total_inr: total,
    uplift_inr: 0,
    buyer_type: "external_agent",
    protocol: "acp_adapter",
    trace,
    checkoutResult: session,
    session_id: sid,
  };
}
