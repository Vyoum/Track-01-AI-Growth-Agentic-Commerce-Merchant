import { useCallback, useEffect, useState } from "react";
import NavBar from "./NavBar.jsx";
import Scoreboard from "./Scoreboard.jsx";
import { fetchGrowthMetrics, fetchHealth, runDemoReplay } from "./api.js";

const SESSION_CHOICES = [10, 25, 50];

const inr = (value) =>
  typeof value === "number" ? `₹${value.toLocaleString("en-IN")}` : "—";

function Kpi({ label, value, sub, tone }) {
  return (
    <div className="kpi" data-tone={tone}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

function FunnelBar({ label, value, max, tone }) {
  const width = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="funnel-row">
      <span className="funnel-label">{label}</span>
      <div className="funnel-track">
        <div
          className="funnel-fill"
          style={{ width: `${width}%` }}
          data-tone={tone}
        />
      </div>
      <span className="funnel-value">{value}</span>
    </div>
  );
}

export default function GrowthResultsPage() {
  const [health, setHealth] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [batch, setBatch] = useState(null);
  const [sessions, setSessions] = useState(25);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const data = await fetchGrowthMetrics();
      setMetrics(data);
      setError("");
    } catch (err) {
      setError(err.message || "Failed to load metrics");
    }
  }, []);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
    refresh();
  }, [refresh]);

  async function onReplay() {
    setBusy(true);
    setError("");
    try {
      const result = await runDemoReplay(sessions, 7);
      setBatch(result);
      setMetrics(result.metrics);
    } catch (err) {
      setError(err.message || "Replay failed");
    } finally {
      setBusy(false);
    }
  }

  const funnel = metrics?.funnel;
  const revenue = metrics?.revenue;
  const funnelMax = funnel
    ? Math.max(
        funnel.proposals_created,
        funnel.offers_shown,
        funnel.offers_reaching_customer,
        funnel.offers_accepted,
        funnel.paid_orders,
        1
      )
    : 1;

  return (
    <div className="app growth-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Razorpay Buildathon · Track 01 · Measured proof</p>
          <h1>Growth Results</h1>
          <p className="sub">
            Aggregates folded from the audit trail. Replay a batch of synthetic
            customers through the real pipeline and watch the numbers move.
          </p>
        </div>
        <div className="status-pill" data-ok={health?.status === "ok"}>
          {health?.status === "ok" ? "API online" : "Checking API…"}
        </div>
      </header>

      <NavBar current="/growth" />

      <section className="panel demo-controls">
        <div className="demo-control-grid">
          <label>
            Batch size
            <select
              value={sessions}
              onChange={(e) => setSessions(Number(e.target.value))}
              disabled={busy}
            >
              {SESSION_CHOICES.map((n) => (
                <option key={n} value={n}>
                  {n} sessions
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={onReplay} disabled={busy}>
            {busy ? `Running ${sessions} sessions…` : `Run ${sessions} sessions`}
          </button>
          <button
            type="button"
            className="ghost"
            onClick={refresh}
            disabled={busy}
          >
            Refresh metrics
          </button>
        </div>
        <p className="muted demo-hint">
          Synthetic customers with simulated accept/decline behaviour. The
          guardrails, campaign orchestrator, merchant gate, payment state
          machine and audit trail they run through are the real code path;
          Razorpay orders are forced to mock so a batch never bills the test API.
        </p>
      </section>

      {error && <p className="msg merchant-toast error">{error}</p>}

      <section className="kpi-grid">
        <Kpi
          label="Paid orders"
          value={funnel ? funnel.paid_orders : "—"}
          sub="signature-verified"
        />
        <Kpi
          label="Baseline GMV"
          value={inr(revenue?.baseline_gmv_inr)}
          sub="cart before growth"
        />
        <Kpi
          label="Paid GMV"
          value={inr(revenue?.paid_gmv_inr)}
          sub="actually collected"
          tone="good"
        />
        <Kpi
          label="Realized uplift"
          value={inr(revenue?.realized_uplift_inr)}
          sub={
            revenue ? `${revenue.uplift_pct_of_baseline}% over baseline` : undefined
          }
          tone="good"
        />
        <Kpi
          label="Add-on acceptance"
          value={funnel ? `${funnel.acceptance_rate_pct}%` : "—"}
          sub={
            funnel
              ? `${funnel.offers_accepted} accepted · ${funnel.offers_declined} declined`
              : undefined
          }
        />
        <Kpi
          label="Offers blocked"
          value={funnel ? funnel.offers_blocked : "—"}
          sub="merchant gate + guardrails"
          tone="warn"
        />
        <Kpi
          label="Avg order value"
          value={inr(revenue?.avg_order_value_inr)}
        />
        <Kpi
          label="Avg uplift / order"
          value={inr(revenue?.avg_uplift_per_paid_order_inr)}
        />
      </section>

      <div className="growth-layout">
        <section className="panel">
          <h2>Funnel</h2>
          {funnel ? (
            <div className="funnel">
              <FunnelBar
                label="Proposals created"
                value={funnel.proposals_created}
                max={funnelMax}
              />
              <FunnelBar
                label="Campaigns proposed"
                value={funnel.campaigns_proposed}
                max={funnelMax}
              />
              <FunnelBar
                label="Offers cleared merchant gate"
                value={funnel.offers_reaching_customer}
                max={funnelMax}
              />
              <FunnelBar
                label="Add-ons accepted"
                value={funnel.offers_accepted}
                max={funnelMax}
                tone="good"
              />
              <FunnelBar
                label="Paid orders"
                value={funnel.paid_orders}
                max={funnelMax}
                tone="good"
              />
              <FunnelBar
                label="Blocked by guardrails"
                value={funnel.proposals_blocked_by_guardrail + funnel.offers_blocked}
                max={funnelMax}
                tone="warn"
              />
            </div>
          ) : (
            <p className="muted">Loading…</p>
          )}
          {funnel && (
            <p className="muted demo-hint">
              {funnel.merchant_rejected} campaign(s) were rejected at the merchant
              gate and never reached a customer.{" "}
              {funnel.offers_with_no_eligible_addon} session(s) had no add-on that
              fit the stated budget.
            </p>
          )}
        </section>

        <section className="panel">
          <h2>Channels</h2>
          {metrics ? (
            <dl className="summary-grid">
              <dt>Human checkout</dt>
              <dd>{metrics.channels.paid_by_human} paid</dd>
              <dt>Buyer agents</dt>
              <dd>{metrics.channels.paid_by_external_agent} paid</dd>
              <dt>A2A completed</dt>
              <dd>{metrics.channels.a2a_purchases_completed}</dd>
              <dt>Events scanned</dt>
              <dd className="dc-mono">{metrics.events_scanned}</dd>
            </dl>
          ) : (
            <p className="muted">Loading…</p>
          )}

          {metrics?.recent_paid?.length > 0 && (
            <>
              <p className="merchant-eyebrow growth-recent-head">Recent paid orders</p>
              <ul className="dc-why">
                {metrics.recent_paid.map((row, i) => (
                  <li key={i}>
                    {inr(row.amount_inr)} · uplift {inr(row.realized_uplift_inr)} ·{" "}
                    {row.buyer_type}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>

      <Scoreboard metrics={metrics} title="Money safety · all sessions to date" />

      {batch && (
        <section className="panel growth-batch">
          <div className="merchant-section-head">
            <h2>Last batch</h2>
            <span className="merchant-count">
              {batch.sessions_run} sessions in {batch.elapsed_ms} ms
            </span>
            <span className="merchant-count">seed {batch.seed}</span>
          </div>

          <dl className="summary-grid growth-batch-totals">
            <dt>Paid</dt>
            <dd>{batch.totals.paid_sessions}</dd>
            <dt>Blocked</dt>
            <dd>{batch.totals.blocked_sessions}</dd>
            <dt>Attacks that succeeded</dt>
            <dd
              className={
                batch.totals.attacks_that_succeeded === 0 ? "dc-pass" : "dc-fail"
              }
            >
              {batch.totals.attacks_that_succeeded}
            </dd>
            <dt>Batch uplift</dt>
            <dd className="dc-pass">{inr(batch.totals.realized_uplift_inr)}</dd>
          </dl>

          <table className="growth-table">
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Sessions</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(batch.scenario_counts).map(([id, count]) => {
                const outcomes = batch.sessions
                  .filter((s) => s.scenario === id)
                  .reduce((acc, s) => {
                    acc[s.outcome] = (acc[s.outcome] || 0) + 1;
                    return acc;
                  }, {});
                return (
                  <tr key={id}>
                    <td>{batch.scenario_labels[id] || id}</td>
                    <td>{count}</td>
                    <td>
                      {Object.entries(outcomes)
                        .map(([outcome, n]) => `${n} ${outcome}`)
                        .join(", ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <p className="merchant-eyebrow growth-recent-head">Blocked sessions</p>
          {batch.sessions.filter((s) => s.block).length === 0 ? (
            <p className="muted">Nothing was blocked in this batch.</p>
          ) : (
            <ul className="dc-why">
              {batch.sessions
                .filter((s) => s.block)
                .map((s, i) => (
                  <li key={i}>
                    <strong>{s.scenario}</strong> — {s.block.stage}:{" "}
                    {s.block.message || s.block.code} (HTTP {s.block.status})
                  </li>
                ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
