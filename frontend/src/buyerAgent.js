/**
 * Browser-side autonomous buyer agent — same public APIs as buyer_agent/run_purchase.py.
 * No backend imports; mirrors buyer_logic.py scoring rules.
 */

import {
  confirmProposalForUser,
  decideAddon,
  decideCampaign,
  fetchAgentManifest,
  searchProducts,
  verifyPayment,
} from "./api.js";

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

async function postProposal(body) {
  const API_BASE = import.meta.env.VITE_API_BASE || "";
  const res = await fetch(`${API_BASE}/api/proposals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.message || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

/**
 * Run full A2A purchase from the browser.
 * @param {object} options
 * @param {string} options.want - catalog search term
 * @param {number} options.budgetInr
 * @param {string} options.userId
 * @param {number} options.maxAddonInr
 * @param {boolean} options.autoMerchantApprove
 * @param {(step: string) => void} [options.onStep]
 */
export async function runBuyerPurchase({
  want = "protein",
  budgetInr = 800,
  userId = "external_buyer_01",
  maxAddonInr = 150,
  autoMerchantApprove = true,
  onStep,
}) {
  const goal = { want, budget_inr: budgetInr, user_id: userId };
  const sessionId = `a2a_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  const trace = [];

  const step = (msg) => {
    trace.push(msg);
    onStep?.(msg);
  };

  const manifest = await fetchAgentManifest();
  step(`discovered protocol=${manifest.checkout_protocol}`);

  const productsResp = await searchProducts(want);
  const products = productsResp.products || [];
  if (!products.length) {
    throw new Error(`No products for query=${want}`);
  }

  const chosen = pickProduct(products, goal);
  if (!chosen) {
    throw new Error("No in-stock product fits buyer goal/budget");
  }
  step(`selected ${chosen.id} (${chosen.name}) ₹${chosen.price_inr}`);

  let proposal = await postProposal({
    user_id: userId,
    product_ids: [chosen.id],
    stated_budget_inr: budgetInr,
    with_growth: true,
    session_id: sessionId,
    buyer_type: "external_agent",
  });
  const proposalId = proposal.id;
  step(`proposal ${proposalId} status=${proposal.status}`);

  if (proposal.status === "awaiting_merchant_approval") {
    if (!autoMerchantApprove) {
      throw new Error(
        "Awaiting merchant approval — enable auto-approve or approve on /merchant"
      );
    }
    proposal = await decideCampaign(
      proposalId,
      "approve",
      "A2A demo UI merchant desk simulation"
    );
    step(`merchant approved campaign → status=${proposal.status}`);
  }

  const offer = proposal.growth_offer;
  if (proposal.status === "awaiting_addon_decision" && offer) {
    if (shouldAcceptAddon(offer, goal, maxAddonInr)) {
      proposal = await decideAddon(proposalId, "accept", offer.product_id);
      step(`accepted add-on ${offer.name} → total ₹${proposal.total_inr}`);
    } else {
      proposal = await decideAddon(proposalId, "skip");
      step(`skipped add-on → total ₹${proposal.total_inr}`);
    }
  }

  const expectedTotal = proposal.total_inr;
  const idempotencyKey = `a2a-${proposalId}-${crypto.randomUUID().slice(0, 8)}`;
  const checkout = await confirmProposalForUser(
    proposalId,
    expectedTotal,
    userId,
    idempotencyKey
  );
  step(`payment order ${checkout.payment?.razorpay_order_id || "created"}`);

  const payment = checkout.payment;
  let verified = null;
  let status = "a2a_payment_pending";

  if (payment?.mock) {
    verified = await verifyPayment({
      payment_id: payment.id,
      razorpay_order_id: payment.razorpay_order_id,
      razorpay_payment_id: `pay_a2a_${crypto.randomUUID().slice(0, 8)}`,
      razorpay_signature: "mock_ok_a2a",
    });
    status = "a2a_purchase_completed";
    step(`mock payment verified ₹${verified.payment?.amount_inr}`);
  } else {
    step("real Razorpay mode — payment pending human/agent verify");
  }

  const growth =
    verified?.growth_summary || checkout.growth_summary || {};

  return {
    status,
    proposal_id: proposalId,
    proposal: verified?.proposal || proposal,
    total_inr: expectedTotal,
    uplift_inr: growth.realized_paid_uplift ?? growth.projected_uplift_inr ?? 0,
    buyer_type: "external_agent",
    trace,
    checkoutResult: verified || checkout,
    session_id: sessionId,
  };
}
