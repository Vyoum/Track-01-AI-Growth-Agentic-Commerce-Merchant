import { useCallback, useEffect, useState } from "react";
import {
  decideCampaign,
  fetchCampaignCatalog,
  fetchHealth,
  fetchMeta,
  fetchPendingCampaigns,
} from "./api.js";

function formatRupee(n) {
  if (n == null) return "—";
  return `₹${n}`;
}

function PendingCard({ proposal, busyId, onDecide }) {
  const camp = proposal.campaign_decision;
  const offer = camp?.offer;
  const busy = busyId === proposal.id;

  if (!camp || !offer) return null;

  return (
    <article className="merchant-card">
      <header className="merchant-card-head">
        <div>
          <p className="merchant-eyebrow">Pending approval</p>
          <h3>{camp.campaign_name}</h3>
        </div>
        <span className="merchant-badge pending">Awaiting click</span>
      </header>

      <dl className="summary-grid">
        <dt>Proposal</dt>
        <dd className="dc-mono">{proposal.id}</dd>
        <dt>Opportunity</dt>
        <dd>{camp.opportunity}</dd>
        <dt>Campaign ID</dt>
        <dd className="dc-mono">{camp.campaign_id}</dd>
        <dt>Segment</dt>
        <dd>{camp.target_segment}</dd>
        <dt>Base cart</dt>
        <dd>{formatRupee(proposal.baseline_total_inr ?? proposal.total_inr)}</dd>
        <dt>Offer</dt>
        <dd>
          {offer.name} · {formatRupee(offer.price_inr)} →{" "}
          {formatRupee(offer.projected_total_inr)}
        </dd>
        <dt>Source</dt>
        <dd>{offer.source}</dd>
        <dt>Copy key</dt>
        <dd>{camp.copy_key}</dd>
        <dt>Budget</dt>
        <dd>{formatRupee(proposal.stated_budget_inr)}</dd>
      </dl>

      {camp.rationale?.length > 0 && (
        <ul className="dc-why">
          {camp.rationale.slice(0, 6).map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}

      {camp.guardrail_notes?.length > 0 && (
        <div className="merchant-notes">
          <p className="merchant-eyebrow">Campaign guardrails</p>
          <ul className="dc-why">
            {camp.guardrail_notes.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="merchant-copy">{camp.customer_copy}</p>

      <div className="actions">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide(proposal.id, "approve")}
        >
          Approve
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy}
          onClick={() => onDecide(proposal.id, "reject")}
        >
          Reject
        </button>
      </div>
    </article>
  );
}

function CatalogCard({ campaign, copyTemplates }) {
  const template = copyTemplates?.[campaign.copy_key];
  return (
    <article className="merchant-card catalog">
      <header className="merchant-card-head">
        <div>
          <p className="merchant-eyebrow">Template</p>
          <h3>{campaign.name}</h3>
        </div>
        <span
          className={`merchant-badge ${campaign.enabled ? "enabled" : "disabled"}`}
        >
          {campaign.enabled ? "Enabled" : "Disabled"}
        </span>
      </header>
      <dl className="summary-grid">
        <dt>ID</dt>
        <dd className="dc-mono">{campaign.id}</dd>
        <dt>Opportunity</dt>
        <dd>{campaign.opportunity}</dd>
        <dt>Segment</dt>
        <dd>{campaign.target_segment}</dd>
        <dt>Strategy</dt>
        <dd>{campaign.strategy}</dd>
        <dt>Priority</dt>
        <dd>{campaign.priority}</dd>
        <dt>Max discount</dt>
        <dd>{campaign.max_discount_pct}%</dd>
        <dt>Copy key</dt>
        <dd>{campaign.copy_key}</dd>
        <dt>Variants</dt>
        <dd>{(campaign.copy_variants || []).join(", ") || "—"}</dd>
      </dl>
      {template && <p className="merchant-copy">{template}</p>}
    </article>
  );
}

export default function MerchantPage() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [pending, setPending] = useState([]);
  const [catalog, setCatalog] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [m, h, pendingRes, catalogRes] = await Promise.all([
        fetchMeta(),
        fetchHealth(),
        fetchPendingCampaigns(),
        fetchCampaignCatalog(),
      ]);
      setMeta(m);
      setHealth(h);
      setPending(pendingRes.proposals || []);
      setCatalog(catalogRes);
    } catch (err) {
      setError(err.message || "Failed to load merchant desk");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  async function onDecide(proposalId, decision) {
    setBusyId(proposalId);
    setMessage("");
    try {
      const updated = await decideCampaign(proposalId, decision);
      setMessage(
        decision === "approve"
          ? `Approved ${updated.campaign_decision?.campaign_id || proposalId}. Customer can accept/skip on checkout.`
          : `Rejected ${updated.campaign_decision?.campaign_id || proposalId}. Baseline cart only.`
      );
      await refresh();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusyId(null);
    }
  }

  const campaigns = catalog?.campaigns || [];
  const guardrails = catalog?.campaign_guardrails || {};
  const segments = catalog?.known_segments || [];

  return (
    <div className="app merchant-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Merchant · Campaign Orchestrator</p>
          <h1>Campaign Desk</h1>
          <p className="sub">
            Approve growth campaigns before they reach the customer. Catalog is
            read from <code>campaigns.json</code> — no invented SKUs or discounts.
          </p>
        </div>
        <div className="merchant-header-actions">
          <a className="nav-link" href="/">
            ← Checkout
          </a>
          <button type="button" className="ghost" onClick={refresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <div className="status-pill" data-ok={health?.status === "ok"}>
            {health?.status === "ok"
              ? meta?.features?.campaigns
                ? "Campaigns online"
                : "API online"
              : error || "Checking API…"}
          </div>
        </div>
      </header>

      {message && <p className="msg merchant-toast">{message}</p>}
      {error && <p className="msg merchant-toast error">{error}</p>}

      <section className="merchant-section">
        <div className="merchant-section-head">
          <h2>Pending approvals</h2>
          <span className="merchant-count">{pending.length}</span>
        </div>
        {pending.length === 0 ? (
          <p className="muted">
            No campaigns waiting. Run the checkout demo and a proposal will appear
            here when status is <code>awaiting_merchant_approval</code>.
          </p>
        ) : (
          <div className="merchant-grid">
            {pending.map((p) => (
              <PendingCard
                key={p.id}
                proposal={p}
                busyId={busyId}
                onDecide={onDecide}
              />
            ))}
          </div>
        )}
      </section>

      <section className="merchant-section">
        <div className="merchant-section-head">
          <h2>Campaign catalog</h2>
          <span className="merchant-count">{campaigns.length}</span>
        </div>
        <dl className="summary-grid merchant-policy">
          <dt>Require approval</dt>
          <dd>{String(guardrails.require_merchant_approval ?? true)}</dd>
          <dt>Max discount</dt>
          <dd>{guardrails.max_discount_pct ?? 0}%</dd>
          <dt>Denied categories</dt>
          <dd>{(guardrails.denied_categories || []).join(", ") || "—"}</dd>
          <dt>Known segments</dt>
          <dd>{segments.join(", ") || "—"}</dd>
        </dl>
        <div className="merchant-grid">
          {campaigns.map((c) => (
            <CatalogCard
              key={c.id}
              campaign={c}
              copyTemplates={catalog?.copy_templates}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
