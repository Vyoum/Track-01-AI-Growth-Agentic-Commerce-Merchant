/** Map backend audit + proposal data into judge-facing Decision Center view. */

const SOURCE_LABELS = {
  bestsellers: "Bestsellers",
  completed_order_history: "Order History",
  requested_products: "Requested Products",
  popular_budget_fit: "Popular Budget Fit",
  catalog_complements: "Product Relationships",
  "catalog_complements+co_purchase_history": "Product Relationships + Co-purchase",
};

function formatSource(raw) {
  if (!raw) return "—";
  return SOURCE_LABELS[raw] || raw.replace(/_/g, " ");
}

function findCheck(checks, id) {
  return checks?.find((c) => c.id === id) || null;
}

function checksByPhase(checks, phase) {
  return (checks || []).filter((c) => c.phase === phase);
}

function gateCheck(gateTraces, checkId) {
  for (const trace of gateTraces || []) {
    const hit = trace.checks?.find((c) => c.id === checkId);
    if (hit) return hit;
  }
  return null;
}

export function buildDecisionCenterView({
  proposal,
  checkoutResult,
  audit,
  userRequest,
}) {
  const trace = audit?.decision_trace;
  const checks = trace?.checks || audit?.checks || [];
  const gateTraces = audit?.gate_traces || [];

  const baseline =
    proposal?.baseline_total_inr ??
    findCheck(checks, "projected_total_within_budget")?.data?.baseline_inr ??
    proposal?.total_inr;

  const growthOffer = proposal?.growth_offer;
  const addonAccepted =
    proposal?.growth_metrics?.recommendation_accepted === true ||
    gateCheck(gateTraces, "addon_accepted")?.status === "pass";
  const addonSkipped =
    proposal?.growth_metrics?.recommendation_declined === true ||
    gateCheck(gateTraces, "addon_skipped")?.status === "pass";

  let addonInr = 0;
  if (addonAccepted && proposal?.total_inr && baseline) {
    addonInr = proposal.total_inr - baseline;
  } else if (growthOffer && !addonSkipped) {
    addonInr = growthOffer.price_inr;
  }

  const growthSource =
    growthOffer?.source ||
    findCheck(checks, "growth_reason_calculated")?.data?.source ||
    (findCheck(checks, "growth_fallback") ? "popular_budget_fit" : null);

  const whyBullets = [];

  const history = findCheck(checks, "history_checked");
  if (history?.status === "info" && history.reason.includes("No completed")) {
    whyBullets.push("No purchase history.");
  } else if (history?.status === "pass") {
    whyBullets.push("Used last completed order.");
  }

  const rel = findCheck(checks, "relationship_data");
  if (rel && !rel.data?.has_relationship_data) {
    whyBullets.push("No product relationship data.");
  } else if (rel?.data?.has_relationship_data) {
    whyBullets.push("Used catalog complement relationships.");
  }

  if (growthSource === "popular_budget_fit") {
    whyBullets.push("Selected eligible popular product within remaining budget.");
  } else if (findCheck(checks, "growth_reason_calculated")) {
    const rationale =
      findCheck(checks, "growth_reason_calculated")?.data?.rationale || [];
    rationale.forEach((r) => whyBullets.push(r.replace(/^./, (c) => c.toUpperCase()) + "."));
  }

  if (whyBullets.length === 0 && trace?.user_request) {
    whyBullets.push("Deterministic server-side growth and guardrail rules applied.");
  }

  const guardrailRows = [
    { label: "Budget", check: findCheck(checks, "budget_check_passed") },
    { label: "Stock", check: findCheck(checks, "stock_check_passed") },
    { label: "Category", check: findCheck(checks, "denied_category_check") },
    {
      label: "Addon gate",
      check:
        gateCheck(gateTraces, "addon_gate") ||
        findCheck(checks, "addon_gate") ||
        (addonAccepted || addonSkipped
          ? { status: "pass", reason: "Resolved before payment" }
          : null),
    },
  ].map(({ label, check }) => ({
    label,
    status:
      check?.status === "fail"
        ? "fail"
        : check && ["pass", "warn", "info"].includes(check.status)
          ? "pass"
          : "pending",
    reason: check?.reason || (check ? "" : "Not reached yet"),
  }));

  const payment = checkoutResult?.payment;
  const paid = payment?.status === "paid";
  const orderCreated = payment?.status === "pending";

  const a2aEvent = (audit?.events || []).find(
    (e) => e.event_type === "a2a_purchase_completed"
  );
  const isExternalAgent =
    proposal?.buyer_type === "external_agent" || Boolean(a2aEvent);

  let userApproval = "Pending";
  if (addonAccepted) userApproval = "Explicitly accepted";
  else if (addonSkipped) userApproval = "Explicitly skipped add-on";
  else if (proposal?.status === "awaiting_merchant_approval") {
    userApproval = "Waiting for merchant approval";
  } else if (proposal?.status === "awaiting_addon_decision") {
    userApproval = "Awaiting add-on decision";
  } else if (paid || orderCreated) {
    userApproval = "Confirmed for payment";
  }

  const camp = proposal?.campaign_decision;
  let merchantApproval = null;
  if (camp) {
    if (camp.merchant_approval_status === "approved") {
      merchantApproval =
        camp.approval_mode === "template_policy"
          ? "Template enabled — auto-applied"
          : "Explicitly approved";
    } else if (camp.merchant_approval_status === "paused") {
      merchantApproval = "Template paused — enable it on /merchant";
    } else if (camp.merchant_approval_status === "rejected") {
      merchantApproval = "Explicitly rejected";
    } else {
      merchantApproval = "Pending — enable the template on /merchant";
    }
  }

  const offerHeld =
    proposal?.status === "awaiting_merchant_approval" ||
    camp?.merchant_approval_status === "paused" ||
    camp?.merchant_approval_status === "pending";

  return {
    userRequest:
      userRequest ||
      trace?.user_request ||
      'Order my usual, under ₹800',
    baseCart: {
      total: baseline,
      source: formatSource(proposal?.proposal_source),
      items: proposal?.items?.filter(
        (i) => !addonAccepted || i.reason?.includes("growth add-on") === false
      ) || proposal?.items,
    },
    campaign:
      camp && !offerHeld
        ? {
            opportunity: camp.opportunity,
            campaignId: camp.campaign_id,
            campaignName: camp.campaign_name,
            segment: camp.target_segment,
            copyKey: camp.copy_key,
            rationale: camp.rationale || [],
          }
        : null,
    growth:
      !offerHeld && (addonInr > 0 || growthOffer)
        ? {
            amount: addonAccepted ? addonInr : growthOffer?.price_inr || addonInr,
            source: formatSource(growthSource || camp?.offer?.source),
            name: growthOffer?.name || camp?.offer?.name,
          }
        : null,
    why: offerHeld
      ? ["Optional add-on held until store releases it"]
      : whyBullets.length
        ? whyBullets
        : camp?.rationale?.slice(0, 4) || [],
    guardrails: guardrailRows,
    merchantApproval,
    userApproval,
    payment: payment
      ? {
          amount: payment.amount_inr ?? proposal?.total_inr,
          mode: payment.mock ? "Razorpay Mock" : "Razorpay Test Mode",
          status: paid
            ? "paid"
            : orderCreated
              ? "pending"
              : payment.status,
          orderId: payment.razorpay_order_id,
        }
      : null,
    a2a: isExternalAgent
      ? {
          buyerType: "external_agent",
          totalInr: a2aEvent?.payload?.total_inr ?? payment?.amount_inr,
          upliftInr: a2aEvent?.payload?.uplift_inr ?? addonInr,
          message:
            "Purchase completed by an autonomous AI buyer agent — same gates and guardrails as a human checkout.",
        }
      : null,
    summary: trace?.summary,
    allPassed: trace?.all_required_passed ?? trace?.summary?.all_required_passed,
  };
}
