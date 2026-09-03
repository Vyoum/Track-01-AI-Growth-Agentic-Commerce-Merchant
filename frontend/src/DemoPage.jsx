import { useCallback, useEffect, useRef, useState } from "react";
import DecisionCenter from "./DecisionCenter.jsx";
import NavBar from "./NavBar.jsx";
import Scoreboard from "./Scoreboard.jsx";
import {
  confirmProposalForUser,
  createProposal,
  decideAddon,
  decideCampaign,
  fetchGrowthMetrics,
  fetchHealth,
  fetchMeta,
  fetchProposalAudit,
  openRazorpayCheckout,
  verifyPayment,
} from "./api.js";

const PACES = [
  { id: "presentation", label: "Presentation", ms: 900 },
  { id: "brisk", label: "Brisk", ms: 350 },
  { id: "instant", label: "Instant", ms: 0 },
];

const SCENARIOS = [
  {
    id: "returning_800",
    label: "Returning customer · under ₹800",
    request: "Order my usual, under ₹800",
    build: (demoUserId) => ({
      user_id: demoUserId,
      use_usual: true,
      stated_budget_inr: 800,
      with_growth: true,
    }),
  },
  {
    id: "returning_1000",
    label: "Returning customer · under ₹1000",
    request: "Order my usual, keep it under ₹1000",
    build: (demoUserId) => ({
      user_id: demoUserId,
      use_usual: true,
      stated_budget_inr: 1000,
      with_growth: true,
    }),
  },
  {
    id: "new_customer",
    label: "New customer · no history · under ₹1500",
    request: "First time here — get me something good under ₹1500",
    build: () => ({
      user_id: `judge_new_${Math.random().toString(36).slice(2, 8)}`,
      use_usual: true,
      stated_budget_inr: 1500,
      with_growth: true,
    }),
  },
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const inr = (value) =>
  typeof value === "number" ? `₹${value.toLocaleString("en-IN")}` : "—";

function StepRow({ step }) {
  return (
    <li className="tl-step" data-status={step.status}>
      <span className="tl-marker" aria-hidden="true" />
      <div className="tl-body">
        <div className="tl-head">
          <span className="tl-actor" data-actor={step.actor}>
            {step.actor}
          </span>
          <span className="tl-title">{step.title}</span>
          {typeof step.ms === "number" && (
            <span className="tl-ms">{step.ms} ms</span>
          )}
        </div>
        {step.detail && <p className="tl-detail">{step.detail}</p>}
        {step.notes?.length > 0 && (
          <ul className="tl-notes">
            {step.notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        )}
        {step.status === "blocked" && (
          <p className="tl-blocked">Blocked — no money moved.</p>
        )}
      </div>
    </li>
  );
}

export default function DemoPage() {
  const [health, setHealth] = useState(null);
  const [meta, setMeta] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const [paceId, setPaceId] = useState("presentation");
  const [autoApprove, setAutoApprove] = useState(true);
  const [steps, setSteps] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState(null);
  const [checkoutResult, setCheckoutResult] = useState(null);
  const [audit, setAudit] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [awaitingMerchant, setAwaitingMerchant] = useState(false);
  const [totalMs, setTotalMs] = useState(null);

  const stepSeq = useRef(0);
  const merchantResolver = useRef(null);

  const scenario = SCENARIOS.find((s) => s.id === scenarioId) || SCENARIOS[0];
  const pace = PACES.find((p) => p.id === paceId)?.ms ?? 900;

  const refreshMetrics = useCallback(() => {
    fetchGrowthMetrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
    fetchMeta().then(setMeta).catch(() => setMeta(null));
    refreshMetrics();
  }, [refreshMetrics]);

  function beginStep(step) {
    const key = `step_${(stepSeq.current += 1)}`;
    setSteps((prev) => [...prev, { key, status: "running", ...step }]);
    return key;
  }

  function endStep(key, patch = {}) {
    setSteps((prev) =>
      prev.map((s) => (s.key === key ? { ...s, status: "done", ...patch } : s))
    );
  }

  function addStep(step) {
    endStep(beginStep(step), step);
  }

  function waitForMerchant() {
    setAwaitingMerchant(true);
    return new Promise((resolve) => {
      merchantResolver.current = resolve;
    });
  }

  function resolveMerchant(decision) {
    setAwaitingMerchant(false);
    const resolve = merchantResolver.current;
    merchantResolver.current = null;
    resolve?.(decision);
  }

  async function run() {
    setRunning(true);
    setError("");
    setSteps([]);
    setProposal(null);
    setCheckoutResult(null);
    setAudit(null);
    setOutcome(null);
    setTotalMs(null);
    stepSeq.current = 0;
    const runStart = performance.now();

    try {
      addStep({
        actor: "CUSTOMER",
        title: "Request received",
        detail: `"${scenario.request}"`,
        notes: ["Budget is a hard constraint, not a hint."],
      });
      await sleep(pace);

      // 1. Baseline cart
      let key = beginStep({
        actor: "ORCHESTRATOR",
        title: "Assembling baseline cart",
      });
      let t0 = performance.now();
      let current = await createProposal({
        ...scenario.build(meta?.demo_user_id || "demo_user_01"),
        session_id: `judge_${Date.now()}`,
      });
      endStep(key, {
        ms: Math.round(performance.now() - t0),
        detail: `${current.items
          .map((i) => `${i.name} ${inr(i.line_total_inr)}`)
          .join(" + ")} = ${inr(current.total_inr)}`,
        notes: [
          `Source: ${current.source_reason}`,
          `Guardrails: ${current.items.length} line(s) priced from live catalog, stock checked`,
        ],
      });
      setProposal(current);
      await sleep(pace);

      // 2. Opportunity + campaign
      const campaign = current.campaign_decision;
      if (campaign) {
        addStep({
          actor: "GROWTH",
          title: `Opportunity detected: ${campaign.opportunity}`,
          detail: campaign.rationale?.[0] || "Deterministic signal from cart state",
          notes: [`Segment resolved: ${campaign.target_segment}`],
        });
        await sleep(pace);

        addStep({
          actor: "GROWTH",
          title: `Campaign matched: ${campaign.campaign_name}`,
          detail: `${campaign.offer.name} at ${inr(campaign.offer.price_inr)} → projected ${inr(
            campaign.offer.projected_total_inr
          )}`,
          notes: [
            `Selected from campaigns.json (id ${campaign.campaign_id}) — not generated by the model`,
            `Copy template: ${campaign.copy_key}`,
          ],
        });
        await sleep(pace);

        addStep({
          actor: "GUARDRAILS",
          title: "Campaign guardrails evaluated",
          detail: campaign.guardrail_passed
            ? "Passed: known segment, allowed category, discount within policy"
            : "Failed campaign guardrails",
          notes: [
            `Discount applied: ${campaign.discount_pct}% (policy max 0%)`,
            ...(campaign.guardrail_notes || []),
          ],
        });
        await sleep(pace);
      } else if (current.growth_offer) {
        addStep({
          actor: "GROWTH",
          title: "Add-on found without campaign wrapper",
          detail: current.growth_offer.reason,
        });
        await sleep(pace);
      } else {
        addStep({
          actor: "GROWTH",
          title: "No eligible add-on",
          detail: "Budget headroom too small — baseline cart only.",
        });
        await sleep(pace);
      }

      // 3. Merchant gate
      if (current.status === "awaiting_merchant_approval") {
        let decision = "approve";
        if (autoApprove) {
          addStep({
            actor: "MERCHANT",
            title: "Approval gate reached",
            detail:
              "Offer is withheld from the customer until the merchant decides.",
            notes: ["Auto-approving for this run"],
          });
        } else {
          addStep({
            actor: "MERCHANT",
            title: "Approval gate reached — waiting for merchant",
            detail: campaign?.customer_copy || "Campaign pending approval",
            notes: ["The customer cannot see this offer yet."],
          });
          decision = await waitForMerchant();
        }

        key = beginStep({
          actor: "MERCHANT",
          title: `Merchant ${decision === "approve" ? "approved" : "rejected"} the campaign`,
        });
        t0 = performance.now();
        current = await decideCampaign(current.id, decision, "Judge Mode run");
        endStep(key, {
          ms: Math.round(performance.now() - t0),
          detail:
            decision === "approve"
              ? "Offer released to the customer."
              : "Offer discarded — customer sees the baseline cart only.",
        });
        setProposal(current);
        await sleep(pace);
      }

      // 4. Customer add-on decision
      if (current.status === "awaiting_addon_decision" && current.growth_offer) {
        const offer = current.growth_offer;
        addStep({
          actor: "CUSTOMER",
          title: "Offer shown to customer",
          detail: offer.offer_text,
        });
        await sleep(pace);

        key = beginStep({
          actor: "CUSTOMER",
          title: "Customer accepted the add-on",
        });
        t0 = performance.now();
        current = await decideAddon(current.id, "accept", offer.product_id);
        endStep(key, {
          ms: Math.round(performance.now() - t0),
          detail: `New total ${inr(current.total_inr)} (was ${inr(
            current.baseline_total_inr
          )})`,
          notes: ["Accepting an add-on is NOT payment authorization."],
        });
        setProposal(current);
        await sleep(pace);
      }

      // 5. Payment gate
      addStep({
        actor: "GUARDRAILS",
        title: "Payment confirmation gate",
        detail: `Server will only charge ${inr(
          current.total_inr
        )} for proposal ${current.id}`,
        notes: [
          "Exact total + proposal id + owning user must all match",
          "Atomic compare-and-swap claims the proposal so only one payment can be created",
        ],
      });
      await sleep(pace);

      key = beginStep({ actor: "RAZORPAY", title: "Creating order" });
      t0 = performance.now();
      const confirmed = await confirmProposalForUser(
        current.id,
        current.total_inr,
        current.user_id,
        `judge_${current.id}`
      );
      const payment = confirmed.payment;
      endStep(key, {
        ms: Math.round(performance.now() - t0),
        detail: `${payment.razorpay_order_id} · ${inr(payment.amount_inr)}`,
        notes: [
          payment.mock
            ? "Mock order (no Razorpay keys configured)"
            : "Razorpay test mode order",
        ],
      });
      setCheckoutResult(confirmed);
      setProposal(confirmed.proposal);
      await sleep(pace);

      // 6. Verify payment
      let verified;
      if (payment.mock) {
        key = beginStep({ actor: "RAZORPAY", title: "Verifying payment" });
        t0 = performance.now();
        verified = await verifyPayment({
          payment_id: payment.id,
          razorpay_order_id: payment.razorpay_order_id,
          razorpay_payment_id: `pay_judge_${Date.now()}`,
          razorpay_signature: "mock_ok_judge",
        });
        endStep(key, {
          ms: Math.round(performance.now() - t0),
          detail: `Payment ${verified.payment.status} · ${inr(
            verified.payment.amount_inr
          )}`,
        });
      } else {
        key = beginStep({
          actor: "RAZORPAY",
          title: "Awaiting Razorpay test checkout",
          detail: "Complete the payment in the Razorpay modal.",
        });
        verified = await new Promise((resolve, reject) => {
          openRazorpayCheckout(
            confirmed,
            async (response) => {
              try {
                resolve(
                  await verifyPayment({
                    payment_id: payment.id,
                    razorpay_order_id: response.razorpay_order_id,
                    razorpay_payment_id: response.razorpay_payment_id,
                    razorpay_signature: response.razorpay_signature,
                  })
                );
              } catch (err) {
                reject(err);
              }
            },
            () => reject(new Error("Razorpay checkout dismissed"))
          ).catch(reject);
        });
        endStep(key, {
          title: "Payment verified by signature",
          detail: `Payment ${verified.payment.status} · ${inr(
            verified.payment.amount_inr
          )}`,
        });
      }

      setCheckoutResult(verified);
      setProposal(verified.proposal);
      await sleep(pace);

      const growth = verified.growth_summary || {};
      addStep({
        actor: "RESULT",
        title: "Uplift realized",
        detail: `Baseline ${inr(growth.baseline_total_inr)} → paid ${inr(
          growth.paid_total_inr
        )} · realized uplift ${inr(growth.realized_paid_uplift ?? 0)}`,
        notes: ["Uplift counts only after a verified payment."],
      });

      setOutcome({
        baseline: growth.baseline_total_inr,
        paid: growth.paid_total_inr,
        uplift: growth.realized_paid_uplift ?? 0,
        orderId: verified.payment?.razorpay_order_id,
      });
      setTotalMs(Math.round(performance.now() - runStart));

      const auditData = await fetchProposalAudit(verified.proposal.id);
      setAudit(auditData);
      refreshMetrics();
    } catch (err) {
      setError(err.message || "Run failed");
      setSteps((prev) =>
        prev.map((s) =>
          s.status === "running" ? { ...s, status: "blocked" } : s
        )
      );
    } finally {
      setAwaitingMerchant(false);
      merchantResolver.current = null;
      setRunning(false);
    }
  }

  return (
    <div className="app demo-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Razorpay Buildathon · Track 01 · Judge Mode</p>
          <h1>One purchase, every decision visible</h1>
          <p className="sub">
            A single customer request runs end to end. Each stage below is a real
            API call against the checkout core — actor, reasoning and latency as
            it happens.
          </p>
        </div>
        <div className="status-pill" data-ok={health?.status === "ok"}>
          {health?.status === "ok"
            ? health.razorpay_test_ready
              ? "Razorpay test keys live"
              : "API online · mock payments"
            : "Checking API…"}
        </div>
      </header>

      <NavBar current="/demo" />

      <section className="panel demo-controls">
        <div className="demo-control-grid">
          <label>
            Customer scenario
            <select
              value={scenarioId}
              onChange={(e) => setScenarioId(e.target.value)}
              disabled={running}
            >
              {SCENARIOS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Pace
            <select
              value={paceId}
              onChange={(e) => setPaceId(e.target.value)}
              disabled={running}
            >
              {PACES.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="a2a-checkbox">
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={(e) => setAutoApprove(e.target.checked)}
              disabled={running}
            />
            Auto-approve merchant gate
          </label>
          <button type="button" onClick={run} disabled={running}>
            {running ? "Running…" : "Run the full demo"}
          </button>
        </div>
        {!autoApprove && (
          <p className="muted demo-hint">
            The run will pause at the merchant gate so you can approve it live.
          </p>
        )}
      </section>

      {error && <p className="msg merchant-toast error">{error}</p>}

      <div className="demo-layout">
        <section className="panel demo-timeline-panel">
          <div className="merchant-section-head">
            <h2>Agent timeline</h2>
            {typeof totalMs === "number" && (
              <span className="merchant-count">{totalMs} ms end to end</span>
            )}
          </div>

          {steps.length === 0 ? (
            <p className="muted">
              Press <strong>Run the full demo</strong>. Nothing is pre-recorded —
              the timeline is built from live responses.
            </p>
          ) : (
            <ol className="tl">
              {steps.map((step) => (
                <StepRow key={step.key} step={step} />
              ))}
            </ol>
          )}

          {awaitingMerchant && (
            <div className="demo-gate">
              <p className="merchant-eyebrow">Merchant decision required</p>
              <p className="merchant-copy">
                {proposal?.campaign_decision?.customer_copy}
              </p>
              <div className="actions">
                <button type="button" onClick={() => resolveMerchant("approve")}>
                  Approve offer
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => resolveMerchant("reject")}
                >
                  Reject offer
                </button>
              </div>
            </div>
          )}
        </section>

        <div className="demo-side">
          <section className="panel demo-outcome">
            <h2>Outcome</h2>
            {outcome ? (
              <>
                <dl className="summary-grid">
                  <dt>Baseline</dt>
                  <dd>{inr(outcome.baseline)}</dd>
                  <dt>Paid</dt>
                  <dd className="demo-paid">{inr(outcome.paid)}</dd>
                  <dt>Realized uplift</dt>
                  <dd className="dc-pass">{inr(outcome.uplift)}</dd>
                  <dt>Order</dt>
                  <dd className="dc-mono">{outcome.orderId}</dd>
                </dl>
                <p className="muted demo-hint">
                  Uplift is recorded only after signature-verified payment.
                </p>
              </>
            ) : (
              <p className="muted">No completed run yet.</p>
            )}
          </section>

          {proposal && (
            <DecisionCenter
              proposal={proposal}
              checkoutResult={checkoutResult}
              audit={audit}
              userRequest={scenario.request}
              loading={running}
            />
          )}
        </div>
      </div>

      <Scoreboard metrics={metrics} title="Money safety · all sessions to date" />

      <p className="muted guardrail-footnote">
        Want the aggregate numbers across many customers?{" "}
        <a className="inline-link" href="/growth">
          Growth Results
        </a>{" "}
        replays batches of sessions. Want to see the guardrails refuse a money
        action?{" "}
        <a className="inline-link" href="/guardrails">
          Guardrail Break Lab
        </a>
        .
      </p>
    </div>
  );
}
