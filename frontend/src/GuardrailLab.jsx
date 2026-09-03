import { useEffect, useState } from "react";
import NavBar from "./NavBar.jsx";
import {
  decideCampaign,
  fetchHealth,
  fetchMeta,
  fetchProposal,
  inspectApiRequest,
  searchProducts,
} from "./api.js";

const jsonHeaders = { "Content-Type": "application/json" };

function Result({ value }) {
  if (!value) return <p className="muted">Not run yet.</p>;
  return (
    <pre className="a2a-json guardrail-result">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function GuardrailLab() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [busy, setBusy] = useState("");
  const [results, setResults] = useState({});

  useEffect(() => {
    Promise.all([fetchMeta(), fetchHealth()]).then(([m, h]) => {
      setMeta(m);
      setHealth(h);
    });
  }, []);

  function record(id, value) {
    setResults((prev) => ({ ...prev, [id]: value }));
  }

  async function runInvalidBudget() {
    setBusy("budget");
    const response = await inspectApiRequest("/api/proposals", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        user_id: meta?.demo_user_id || "demo_user_01",
        product_ids: [],
        stated_budget_inr: -1,
        with_growth: false,
      }),
    });
    record("budget", {
      expected: "HTTP 422 before proposal creation",
      observed_status: response.status,
      pass: response.status === 422,
      response: response.data,
    });
    setBusy("");
  }

  async function runWrongAddon() {
    setBusy("addon");
    try {
      const created = await inspectApiRequest("/api/proposals", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({
          user_id: meta?.demo_user_id || "demo_user_01",
          use_usual: true,
          stated_budget_inr: 800,
          with_growth: true,
          session_id: `guardrail_ui_${Date.now()}`,
        }),
      });
      if (!created.ok) throw new Error(JSON.stringify(created.data));

      let proposal = created.data;
      if (proposal.status === "awaiting_merchant_approval") {
        proposal = await decideCampaign(
          proposal.id,
          "approve",
          "Guardrail Lab setup"
        );
      }
      if (proposal.status !== "awaiting_addon_decision") {
        throw new Error(`No add-on decision available (status=${proposal.status})`);
      }

      const attacked = await inspectApiRequest(
        `/api/proposals/${proposal.id}/growth/decide`,
        {
          method: "POST",
          headers: jsonHeaders,
          body: JSON.stringify({
            decision: "accept",
            product_id: "invented_product_id",
          }),
        }
      );
      const unchanged = await fetchProposal(proposal.id);
      record("addon", {
        attack: "Accept invented_product_id",
        expected: "HTTP 409; stored offer unchanged",
        observed_status: attacked.status,
        proposal_status_after: unchanged.status,
        stored_offer_after: unchanged.growth_offer?.product_id,
        pass:
          attacked.status === 409 &&
          unchanged.status === "awaiting_addon_decision",
        response: attacked.data,
      });
    } catch (error) {
      record("addon", { pass: false, setup_error: error.message });
    } finally {
      setBusy("");
    }
  }

  async function runDoubleConfirm() {
    setBusy("concurrency");
    try {
      const products = (await searchProducts("")).products || [];
      const chosen = products.find(
        (p) => Number(p.stock) > 0 && Number(p.price_inr) > 0 && Number(p.price_inr) <= 5000
      );
      if (!chosen) throw new Error("No eligible in-stock product available");

      const budget = Math.min(5000, Math.max(800, Number(chosen.price_inr)));
      const created = await inspectApiRequest("/api/proposals", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({
          user_id: meta?.demo_user_id || "demo_user_01",
          product_ids: [chosen.id],
          stated_budget_inr: budget,
          with_growth: false,
          session_id: `concurrency_ui_${Date.now()}`,
        }),
      });
      if (!created.ok) throw new Error(JSON.stringify(created.data));

      const proposal = created.data;
      const confirmBody = (key) =>
        JSON.stringify({
          expected_total_inr: proposal.total_inr,
          user_id: proposal.user_id,
          idempotency_key: key,
        });

      const [a, b] = await Promise.all([
        inspectApiRequest(`/api/proposals/${proposal.id}/confirm`, {
          method: "POST",
          headers: jsonHeaders,
          body: confirmBody(`race_a_${Date.now()}`),
        }),
        inspectApiRequest(`/api/proposals/${proposal.id}/confirm`, {
          method: "POST",
          headers: jsonHeaders,
          body: confirmBody(`race_b_${Date.now()}`),
        }),
      ]);

      const statuses = [a.status, b.status];
      const payments = [a.data?.payment?.id, b.data?.payment?.id].filter(Boolean);
      const oneWinner =
        statuses.filter((s) => s === 200).length === 1 &&
        statuses.filter((s) => s === 409).length === 1;
      const replaySamePayment =
        statuses.every((s) => s === 200) &&
        payments.length === 2 &&
        payments[0] === payments[1];

      record("concurrency", {
        attack: "Two simultaneous confirms with different idempotency keys",
        expected: "One winner + clean 409, or same payment replay",
        observed_statuses: statuses,
        payment_ids: payments,
        pass: oneWinner || replaySamePayment,
        response_a: a.data,
        response_b: b.data,
        note:
          health?.razorpay_test_ready
            ? "Real Razorpay test keys are configured; network failures may appear as 502."
            : "Mock Razorpay mode.",
      });
    } catch (error) {
      record("concurrency", { pass: false, setup_error: error.message });
    } finally {
      setBusy("");
    }
  }

  const scenarios = [
    {
      id: "budget",
      title: "Negative budget",
      description:
        "Sends stated_budget_inr=-1. Pydantic must reject it at the API boundary.",
      damage:
        "An unbounded budget makes every guardrail downstream meaningless — the agent could assemble a cart of any value and call it 'within budget'.",
      defence: "Typed bounds (1 ≤ budget ≤ 5000) rejected before a cart exists.",
      run: runInvalidBudget,
    },
    {
      id: "addon",
      title: "Invented add-on SKU",
      description: "Attempts to accept a product ID that was never offered.",
      damage:
        "The customer is charged for an item the merchant never offered and the agent invented. This is the classic hallucinated-SKU failure.",
      defence:
        "The accepted product_id must match the stored offer on the server; the offer is never taken from the request.",
      run: runWrongAddon,
    },
    {
      id: "concurrency",
      title: "Concurrent double-confirm",
      description:
        "Fires two confirmations together. Atomic CAS permits only one payment creator.",
      damage:
        "Two Razorpay orders for one cart — the customer is charged twice for the same order and someone has to refund it by hand.",
      defence:
        "BEGIN IMMEDIATE + a status compare-and-swap lets exactly one request claim the proposal, backed by a unique index on payments.proposal_id.",
      run: runDoubleConfirm,
    },
  ];

  return (
    <div className="app guardrail-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Adversarial Demo · Checkout Safety</p>
          <h1>Guardrail Break Lab</h1>
          <p className="sub">
            Deliberately attack money-action boundaries and show the exact rejection.
          </p>
        </div>
        <div className="merchant-header-actions">
          <div className="status-pill" data-ok={health?.status === "ok"}>
            {health?.status === "ok" ? "API online" : "Checking…"}
          </div>
        </div>
      </header>

      <NavBar current="/guardrails" />

      <div className="guardrail-grid">
        {scenarios.map((scenario) => (
          <section className="panel guardrail-card" key={scenario.id}>
            <h2>{scenario.title}</h2>
            <p className="muted">{scenario.description}</p>

            <div className="counterfactual">
              <p className="counterfactual-head">Without this guardrail</p>
              <p className="counterfactual-damage">{scenario.damage}</p>
              <p className="counterfactual-head defence">What actually stops it</p>
              <p className="counterfactual-defence">{scenario.defence}</p>
            </div>

            <div className="actions">
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={scenario.run}
              >
                {busy === scenario.id ? "Attacking…" : "Run attack"}
              </button>
            </div>
            {results[scenario.id] && (
              <p
                className={
                  results[scenario.id].pass ? "dc-pass" : "dc-fail"
                }
              >
                {results[scenario.id].pass
                  ? "✓ Guardrail held"
                  : "✗ Scenario did not meet expectation"}
              </p>
            )}
            <Result value={results[scenario.id]} />
          </section>
        ))}
      </div>

      <p className="muted guardrail-footnote">
        Database invariant: <code>CREATE UNIQUE INDEX ux_payments_proposal_id
        ON payments(proposal_id)</code>.
      </p>
    </div>
  );
}
