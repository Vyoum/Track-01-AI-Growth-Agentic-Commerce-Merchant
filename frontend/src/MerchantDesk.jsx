import { useState } from "react";
import { decideCampaign } from "./api.js";

export default function MerchantDesk({ proposal, setProposal }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const pending =
    proposal?.status === "awaiting_merchant_approval" &&
    proposal?.campaign_decision;

  if (!pending) return null;

  const camp = proposal.campaign_decision;
  const offer = camp.offer;

  async function onDecide(decision) {
    if (!proposal?.id) return;
    setBusy(true);
    setMessage("");
    try {
      const updated = await decideCampaign(proposal.id, decision);
      setProposal(updated);
      setMessage(
        decision === "approve"
          ? `Approved ${camp.campaign_id}. Customer can now accept/skip the add-on.`
          : `Rejected ${camp.campaign_id}. Baseline cart only.`
      );
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel merchant-desk" aria-label="Merchant campaign desk">
      <h2>Merchant Desk</h2>
      <p className="merchant-eyebrow">Campaign approval required</p>

      <dl className="summary-grid">
        <dt>Opportunity</dt>
        <dd>{camp.opportunity}</dd>
        <dt>Campaign</dt>
        <dd>
          {camp.campaign_name}
          <span className="dc-mono"> ({camp.campaign_id})</span>
        </dd>
        <dt>Segment</dt>
        <dd>{camp.target_segment}</dd>
        <dt>Offer</dt>
        <dd>
          {offer.name} · ₹{offer.price_inr} → ₹{offer.projected_total_inr}
        </dd>
        <dt>Copy key</dt>
        <dd>{camp.copy_key}</dd>
      </dl>

      {camp.rationale?.length > 0 && (
        <ul className="dc-why">
          {camp.rationale.slice(0, 4).map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}

      <p className="merchant-copy">{camp.customer_copy}</p>

      <div className="actions">
        <button type="button" disabled={busy} onClick={() => onDecide("approve")}>
          Approve campaign
        </button>
        <button
          type="button"
          disabled={busy}
          className="ghost"
          onClick={() => onDecide("reject")}
        >
          Reject
        </button>
      </div>

      {message && <p className="msg">{message}</p>}
    </section>
  );
}
