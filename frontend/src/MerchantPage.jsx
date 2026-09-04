import { useCallback, useEffect, useState } from "react";
import NavBar from "./NavBar.jsx";
import {
  decideCampaign,
  fetchCampaignCatalog,
  fetchHealth,
  fetchMeta,
  fetchPendingCampaigns,
  setCampaignPolicy,
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

function CatalogCard({ campaign, copyTemplates, busyId, onPolicy }) {
  const template = copyTemplates?.[campaign.copy_key];
  const live = campaign.live_status || (campaign.enabled ? "enabled" : "paused");
  const busy = busyId === campaign.id;
  return (
    <article className="merchant-card catalog">
      <header className="merchant-card-head">
        <div>
          <p className="merchant-eyebrow">Growth template</p>
          <h3>{campaign.name}</h3>
        </div>
        <span
          className={`merchant-badge ${live === "enabled" ? "enabled" : "disabled"}`}
        >
          {live === "enabled" ? "Enabled" : "Paused"}
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
      <p className="muted">
        Enable once. Matching checkouts then auto-release this add-on. Pause to
        stop new offers — no per-order click.
      </p>
      <div className="actions">
        {live !== "enabled" ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onPolicy(campaign.id, "enabled")}
          >
            Enable template
          </button>
        ) : (
          <button
            type="button"
            className="ghost"
            disabled={busy}
            onClick={() => onPolicy(campaign.id, "paused")}
          >
            Pause template
          </button>
        )}
      </div>
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

  async function onPolicy(campaignId, status) {
    setBusyId(campaignId);
    setMessage("");
    try {
      const result = await setCampaignPolicy(campaignId, status);
      let extra = "";
      if (status === "enabled" && result.pending_released) {
        extra = ` Released ${result.pending_released} leftover checkout(s).`;
      }
      if (status === "paused" && result.offers_retracted) {
        extra = ` Pulled ${result.offers_retracted} open add-on(s) back to the baseline cart.`;
      }
      setMessage(
        (status === "enabled"
          ? `Enabled ${campaignId}. Matching checkouts will auto-release this add-on.`
          : `Paused ${campaignId}. This add-on will not be shown. Place a new order (or refresh checkout) to see the stall.`) + extra
      );
      await refresh();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function onDecide(proposalId, decision) {
    setBusyId(proposalId);
    setMessage("");
    try {
      const updated = await decideCampaign(proposalId, decision);
      setMessage(
        decision === "approve"
          ? `Approved leftover checkout ${updated.campaign_decision?.campaign_id || proposalId} and enabled the template.`
          : `Rejected leftover checkout. Baseline cart only.`
      );
      await refresh();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusyId(null);
    }
  }

  const campaigns = [];
  const seen = new Set();
  for (const campaign of catalog?.campaigns || []) {
    if (!campaign?.id || seen.has(campaign.id)) continue;
    seen.add(campaign.id);
    campaigns.push(campaign);
  }
  const guardrails = catalog?.campaign_guardrails || {};
  const segments = catalog?.known_segments || [];

  return (
    <div className="app merchant-app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Merchant · Campaign Orchestrator</p>
          <h1>Growth templates</h1>
          <p className="sub">
            Enable a template once. Matching add-ons then auto-release to the
            customer. Pause to stop new offers. No per-checkout click.
          </p>
        </div>
        <div className="merchant-header-actions">
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

      <NavBar current="/merchant" />

      {message && <p className="msg merchant-toast">{message}</p>}
      {error && <p className="msg merchant-toast error">{error}</p>}

      {pending.length > 0 && (
        <section className="merchant-section">
          <div className="merchant-section-head">
            <h2>Leftover per-checkout reviews</h2>
            <span className="merchant-count">{pending.length}</span>
          </div>
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
        </section>
      )}

      <section className="merchant-section">
        <div className="merchant-section-head">
          <h2>Growth templates</h2>
          <span className="merchant-count">{campaigns.length}</span>
        </div>
        <dl className="summary-grid merchant-policy">
          <dt>Approval model</dt>
          <dd>Enable template once, then auto-apply</dd>
          <dt>Per-checkout click</dt>
          <dd>{String(guardrails.require_merchant_approval ?? false)}</dd>
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
              busyId={busyId}
              onPolicy={onPolicy}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
