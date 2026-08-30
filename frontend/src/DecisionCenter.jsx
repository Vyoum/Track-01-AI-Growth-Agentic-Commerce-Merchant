import { buildDecisionCenterView } from "./decisionCenterUtils.js";

function StatusMark({ status }) {
  if (status === "pass") return <span className="dc-pass">✓ PASS</span>;
  if (status === "fail") return <span className="dc-fail">✗ FAIL</span>;
  return <span className="dc-pending">… pending</span>;
}

export default function DecisionCenter({
  proposal,
  checkoutResult,
  audit,
  userRequest,
  loading,
}) {
  if (!proposal) return null;

  const view = buildDecisionCenterView({
    proposal,
    checkoutResult,
    audit,
    userRequest,
  });

  return (
    <section className="decision-center" aria-label="AI Commerce Trace">
      <div className="dc-banner">
        <span className="dc-line" />
        <h2>AI COMMERCE TRACE</h2>
        <span className="dc-line" />
      </div>

      {loading && <p className="dc-loading">Loading audit trace…</p>}

      <div className="dc-block">
        <h3>USER REQUEST</h3>
        <p className="dc-quote">&ldquo;{view.userRequest}&rdquo;</p>
      </div>

      <div className="dc-block">
        <h3>BASE CART</h3>
        <p className="dc-amount">₹{view.baseCart.total}</p>
        <p className="dc-meta">Source: {view.baseCart.source}</p>
        {view.baseCart.items?.length > 0 && (
          <ul className="dc-items">
            {view.baseCart.items.slice(0, 3).map((item) => (
              <li key={item.product_id}>
                {item.name} · ₹{item.line_total_inr}
              </li>
            ))}
          </ul>
        )}
      </div>

      {view.campaign && (
        <div className="dc-block">
          <h3>OPPORTUNITY</h3>
          <p className="dc-amount">{view.campaign.opportunity}</p>
          <p className="dc-meta">
            Campaign: {view.campaign.campaignName} ({view.campaign.campaignId})
          </p>
          <p className="dc-meta">Segment: {view.campaign.segment}</p>
          <p className="dc-meta">Copy key: {view.campaign.copyKey}</p>
        </div>
      )}

      {view.growth && (
        <div className="dc-block">
          <h3>GROWTH</h3>
          <p className="dc-amount">₹{view.growth.amount} add-on</p>
          {view.growth.name && (
            <p className="dc-meta">{view.growth.name}</p>
          )}
          <p className="dc-meta">Source: {view.growth.source}</p>
        </div>
      )}

      {view.why.length > 0 && (
        <div className="dc-block">
          <h3>WHY?</h3>
          <ul className="dc-why">
            {view.why.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="dc-block">
        <h3>GUARDRAILS</h3>
        <dl className="dc-guardrails">
          {view.guardrails.map((row) => (
            <div key={row.label} className="dc-guard-row">
              <dt>{row.label}</dt>
              <dd>
                <StatusMark status={row.status} />
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {view.merchantApproval && (
        <div className="dc-block">
          <h3>MERCHANT APPROVAL</h3>
          <p className="dc-approval">
            {view.merchantApproval.startsWith("Explicitly") ? "✓ " : ""}
            {view.merchantApproval}
          </p>
        </div>
      )}

      <div className="dc-block">
        <h3>USER APPROVAL</h3>
        <p className="dc-approval">
          {view.userApproval.startsWith("Explicitly") ? "✓ " : ""}
          {view.userApproval}
        </p>
      </div>

      {view.payment && (
        <div className="dc-block">
          <h3>PAYMENT</h3>
          <p className="dc-amount">₹{view.payment.amount}</p>
          <p className="dc-meta">{view.payment.mode}</p>
          {view.payment.status === "pending" && (
            <p className="dc-pass">✓ Order created</p>
          )}
          {view.payment.status === "paid" && (
            <p className="dc-pass">✓ Payment verified</p>
          )}
          {view.payment.orderId && (
            <p className="dc-meta dc-mono">{view.payment.orderId}</p>
          )}
        </div>
      )}

      {view.summary && (
        <p className="dc-summary">
          {view.allPassed
            ? "All required checks passed"
            : `${view.summary.failed} check(s) failed`}
          {" · "}
          {view.summary.passed} passed
          {view.summary.warned ? ` · ${view.summary.warned} warned` : ""}
        </p>
      )}

      <div className="dc-banner dc-banner-foot">
        <span className="dc-line" />
      </div>
    </section>
  );
}
