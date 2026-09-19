const API_BASE = import.meta.env.VITE_API_BASE || "";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`Request failed: ${path} (${res.status})`);
  }
  return res.json();
}

async function postJson(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
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

export async function inspectApiRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

export function fetchProposalAudit(proposalId) {
  return getJson(`/api/proposals/${proposalId}/audit`);
}

export function fetchProposal(proposalId) {
  return getJson(`/api/proposals/${proposalId}`);
}

export function fetchHealth() {
  return getJson("/health");
}

export function fetchMeta() {
  return getJson("/api/meta");
}

export function sendChat(message, sessionId, userId = "demo_user_01") {
  return postJson("/api/chat", {
    message,
    session_id: sessionId || undefined,
    user_id: userId,
  });
}

export function createUsualProposal(budget = 800) {
  return postJson("/api/proposals", {
    user_id: "demo_user_01",
    use_usual: true,
    stated_budget_inr: budget,
    with_growth: true,
    session_id: "ui_demo_user_01",
  });
}

export function decideAddon(proposalId, decision, productId) {
  return postJson(`/api/proposals/${proposalId}/growth/decide`, {
    decision,
    product_id: productId || undefined,
  });
}

export function decideCampaign(proposalId, decision, note) {
  return postJson(`/api/proposals/${proposalId}/campaign/decide`, {
    decision,
    note: note || undefined,
  });
}

export function fetchPendingCampaigns() {
  return getJson("/api/merchant/campaigns/pending");
}

export function fetchCampaignCatalog() {
  return getJson("/api/merchant/campaigns/catalog");
}

export function setCampaignPolicy(campaignId, status, note) {
  return postJson(`/api/merchant/campaigns/${campaignId}/policy`, {
    status,
    note: note || undefined,
  });
}

export function fetchAgentManifest() {
  return getJson("/.well-known/agent-catalog.json");
}

export function fetchAcpManifest() {
  return getJson("/.well-known/acp.json");
}

export function fetchA2aSummary() {
  return getJson("/api/a2a/summary");
}

export function fetchGrowthMetrics() {
  return getJson("/api/metrics/growth");
}

export function fetchDemoScenarios() {
  return getJson("/api/demo/scenarios");
}

export function runDemoReplay(sessions = 25, seed = 7) {
  return postJson("/api/demo/replay", { sessions, seed });
}

export function createProposal(body) {
  return postJson("/api/proposals", body);
}

export function searchProducts(q = "") {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return getJson(`/api/products${qs}`);
}

export function fetchSpendMandate(userId, sessionId) {
  const params = new URLSearchParams({ user_id: userId });
  if (sessionId) params.set("session_id", sessionId);
  return getJson(`/api/spend-mandate?${params.toString()}`);
}

export function confirmProposalForUser(
  proposalId,
  expectedTotalInr,
  userId,
  idempotencyKey,
  acknowledgeImpulseWarning = false
) {
  return postJson(`/api/proposals/${proposalId}/confirm`, {
    expected_total_inr: expectedTotalInr,
    user_id: userId,
    idempotency_key: idempotencyKey,
    acknowledge_impulse_warning: acknowledgeImpulseWarning,
  });
}

export function confirmProposal(
  proposalId,
  expectedTotalInr,
  acknowledgeImpulseWarning = false
) {
  return postJson(`/api/proposals/${proposalId}/confirm`, {
    expected_total_inr: expectedTotalInr,
    user_id: "demo_user_01",
    idempotency_key: `ui_${proposalId}_${expectedTotalInr}`,
    acknowledge_impulse_warning: acknowledgeImpulseWarning,
  });
}

export function verifyPayment(payload) {
  return postJson("/api/payments/verify", payload);
}

export function failPayment(proposalId, reason = "user_cancelled") {
  return postJson(`/api/proposals/${proposalId}/payment/fail`, { reason });
}

export function loadRazorpayScript() {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) {
      resolve(window.Razorpay);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve(window.Razorpay);
    script.onerror = () => reject(new Error("Failed to load Razorpay checkout.js"));
    document.body.appendChild(script);
  });
}

export async function openRazorpayCheckout(result, onSuccess, onDismiss) {
  const checkout = result?.checkout;
  if (!checkout?.key_id || !checkout?.order_id) {
    throw new Error(
      "Razorpay Checkout.js needs a live test order (key_id + order_id). Mock orders never open the modal."
    );
  }
  const Razorpay = await loadRazorpayScript();
  let settled = false;
  const options = {
    key: checkout.key_id,
    amount: checkout.amount_paise,
    currency: checkout.currency,
    name: checkout.name,
    description: checkout.description,
    order_id: checkout.order_id,
    notes: checkout.notes,
    handler: async (response) => {
      if (settled) return;
      settled = true;
      await onSuccess?.(response);
    },
    modal: {
      ondismiss: async () => {
        if (settled) return;
        settled = true;
        await onDismiss?.();
      },
    },
  };
  const rzp = new Razorpay(options);
  rzp.open();
}
