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

export function fetchProposalAudit(proposalId) {
  return getJson(`/api/proposals/${proposalId}/audit`);
}

export function fetchHealth() {
  return getJson("/health");
}

export function fetchMeta() {
  return getJson("/api/meta");
}

export function sendChat(message, sessionId) {
  return postJson("/api/chat", {
    message,
    session_id: sessionId || undefined,
    user_id: "demo_user_01",
  });
}

export function createUsualProposal(budget = 800) {
  return postJson("/api/proposals", {
    user_id: "demo_user_01",
    use_usual: true,
    stated_budget_inr: budget,
    with_growth: true,
    session_id: `ui_${Date.now()}`,
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

export function confirmProposal(proposalId, expectedTotalInr) {
  return postJson(`/api/proposals/${proposalId}/confirm`, {
    expected_total_inr: expectedTotalInr,
    user_id: "demo_user_01",
    idempotency_key: `ui_${proposalId}_${expectedTotalInr}`,
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
  const Razorpay = await loadRazorpayScript();
  const options = {
    key: result.checkout.key_id,
    amount: result.checkout.amount_paise,
    currency: result.checkout.currency,
    name: result.checkout.name,
    description: result.checkout.description,
    order_id: result.checkout.order_id,
    notes: result.checkout.notes,
    handler: onSuccess,
    modal: { ondismiss: onDismiss },
  };
  const rzp = new Razorpay(options);
  rzp.open();
}
