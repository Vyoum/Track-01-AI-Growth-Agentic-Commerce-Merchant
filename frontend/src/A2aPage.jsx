import { useCallback, useEffect, useState } from "react";
import DecisionCenter from "./DecisionCenter.jsx";
import NavBar from "./NavBar.jsx";
import {
  fetchA2aSummary,
  fetchAgentManifest,
  fetchHealth,
  fetchProposalAudit,
} from "./api.js";
import { runBuyerPurchase } from "./buyerAgent.js";

export default function A2aPage() {
  const [manifest, setManifest] = useState(null);
  const [summary, setSummary] = useState(null);
  const [health, setHealth] = useState(null);
  const [want, setWant] = useState("protein");
  const [budgetInr, setBudgetInr] = useState(800);
  const [maxAddonInr, setMaxAddonInr] = useState(150);
  const [autoMerchantApprove, setAutoMerchantApprove] = useState(true);
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState(null);
  const [checkoutResult, setCheckoutResult] = useState(null);
  const [audit, setAudit] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);

  const refreshMeta = useCallback(async () => {
    try {
      const [m, s, h] = await Promise.all([
        fetchAgentManifest(),
        fetchA2aSummary(),
        fetchHealth(),
      ]);
      setManifest(m);
      setSummary(s);
      setHealth(h);
      setError("");
    } catch (err) {
      setError(err.message || "Failed to load A2A desk");
    }
  }, []);

  useEffect(() => {
    refreshMeta();
    const id = setInterval(refreshMeta, 12000);
    return () => clearInterval(id);
  }, [refreshMeta]);

  useEffect(() => {
    if (!proposal?.id) return;
    setAuditLoading(true);
    fetchProposalAudit(proposal.id)
      .then(setAudit)
      .catch(() => setAudit(null))
      .finally(() => setAuditLoading(false));
  }, [proposal?.id, result?.status]);

  async function onRun() {
    setBusy(true);
    setError("");
    setSteps([]);
    setResult(null);
    setProposal(null);
    setCheckoutResult(null);
    setAudit(null);

    try {
      const out = await runBuyerPurchase({
        want,
        budgetInr,
        maxAddonInr,
        autoMerchantApprove,
        onStep: (msg) => setSteps((prev) => [...prev, msg]),
      });
      setResult(out);
      setProposal(out.proposal);
      setCheckoutResult(out.checkoutResult);
      await refreshMeta();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app a2a-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Agent-to-Agent · Demo Desk</p>
          <h1>A2A Buyer Agent</h1>
          <p className="sub">
            Autonomous buyer discovers{" "}
            <code>/.well-known/agent-catalog.json</code>, purchases via public
            APIs only — same gates as human checkout.
          </p>
        </div>
        <div className="merchant-header-actions">
          <button type="button" className="ghost" onClick={refreshMeta}>
            Refresh
          </button>
          <div className="status-pill" data-ok={health?.status === "ok"}>
            {health?.status === "ok" ? "API online" : error || "Checking…"}
          </div>
        </div>
      </header>

      <NavBar current="/a2a" />

      {error && !busy && (
        <p className="msg merchant-toast error">{error}</p>
      )}

      <div className="a2a-layout">
        <section className="panel a2a-panel">
          <h2>Agent manifest</h2>
          {manifest ? (
            <>
              <dl className="summary-grid a2a-manifest-summary">
                <dt>Protocol</dt>
                <dd>{manifest.checkout_protocol}</dd>
                <dt>Merchant</dt>
                <dd>{manifest.merchant}</dd>
                <dt>Explicit confirm</dt>
                <dd>
                  {String(manifest.policies?.requires_explicit_confirmation)}
                </dd>
                <dt>Merchant approval</dt>
                <dd>
                  {String(
                    manifest.policies?.requires_merchant_campaign_approval
                  )}
                </dd>
                <dt>Identify as</dt>
                <dd className="dc-mono">
                  {manifest.buyer_agent_hints?.identify_as}
                </dd>
              </dl>
              <pre className="a2a-json">
                {JSON.stringify(manifest, null, 2)}
              </pre>
            </>
          ) : (
            <p className="muted">Loading manifest…</p>
          )}
        </section>

        <section className="panel a2a-panel">
          <h2>Run buyer agent</h2>
          <p className="muted">
            Simulates <code>buyer_agent/run_purchase.py</code> in the browser.
          </p>

          <div className="a2a-form">
            <label>
              Search want
              <input
                value={want}
                onChange={(e) => setWant(e.target.value)}
                disabled={busy}
                placeholder="protein"
              />
            </label>
            <label>
              Budget (₹)
              <input
                type="number"
                value={budgetInr}
                onChange={(e) => setBudgetInr(Number(e.target.value))}
                disabled={busy}
                min={1}
              />
            </label>
            <label>
              Max add-on (₹)
              <input
                type="number"
                value={maxAddonInr}
                onChange={(e) => setMaxAddonInr(Number(e.target.value))}
                disabled={busy}
                min={0}
              />
            </label>
            <label className="a2a-checkbox">
              <input
                type="checkbox"
                checked={autoMerchantApprove}
                onChange={(e) => setAutoMerchantApprove(e.target.checked)}
                disabled={busy}
              />
              Auto merchant approve (via public API)
            </label>
          </div>

          <div className="actions">
            <button type="button" disabled={busy} onClick={onRun}>
              {busy ? "Running…" : "Run A2A purchase"}
            </button>
          </div>

          {steps.length > 0 && (
            <div className="a2a-trace">
              <p className="merchant-eyebrow">Live trace</p>
              <ol>
                {steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            </div>
          )}

          {result && (
            <dl className="summary-grid a2a-result">
              <dt>Status</dt>
              <dd className="dc-mono">{result.status}</dd>
              <dt>Proposal</dt>
              <dd className="dc-mono">{result.proposal_id}</dd>
              <dt>Total</dt>
              <dd>₹{result.total_inr}</dd>
              <dt>Uplift</dt>
              <dd>₹{result.uplift_inr}</dd>
            </dl>
          )}
        </section>

        <section className="panel a2a-panel">
          <h2>A2A summary</h2>
          {summary ? (
            <>
              <dl className="summary-grid">
                <dt>Completed purchases</dt>
                <dd>{summary.external_agent_purchases}</dd>
                <dt>Total uplift</dt>
                <dd>₹{summary.total_uplift_inr}</dd>
              </dl>
              {summary.recent?.length > 0 && (
                <ul className="dc-why">
                  {summary.recent.map((e, i) => (
                    <li key={i}>
                      ₹{e.payload?.total_inr} uplift ₹{e.payload?.uplift_inr}{" "}
                      <span className="dc-mono">
                        {e.payload?.proposal_id?.slice(0, 8)}…
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="muted">No A2A purchases yet.</p>
          )}
        </section>
      </div>

      {proposal && (
        <div className="a2a-decision-wrap">
          <DecisionCenter
            proposal={proposal}
            checkoutResult={checkoutResult}
            audit={audit}
            userRequest={`A2A buyer: want "${want}" under ₹${budgetInr}`}
            loading={auditLoading}
          />
        </div>
      )}
    </div>
  );
}
