import { useState } from "react";
import {
  confirmProposal,
  createUsualProposal,
  decideAddon,
  failPayment,
  loadRazorpayScript,
  verifyPayment,
} from "./api.js";

export default function OrderSummaryCard({
  proposal,
  setProposal,
  health,
  checkoutResult,
  onCheckoutReady,
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [localCheckout, setLocalCheckout] = useState(null);

  const activeCheckout = checkoutResult || localCheckout;

  async function startDemo() {
    setBusy(true);
    setMessage("");
    setLocalCheckout(null);
    try {
      const p = await createUsualProposal(800);
      setProposal(p);
      if (p.status === "awaiting_merchant_approval") {
        setMessage(
          `Campaign pending merchant approval: ${p.campaign_decision?.campaign_id || "—"}. ` +
            "Use Merchant Desk to Approve."
        );
      } else {
        setMessage(p.growth_offer?.offer_text || "Proposal ready");
      }
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function onAddon(decision) {
    if (!proposal?.id) return;
    setBusy(true);
    try {
      const p = await decideAddon(
        proposal.id,
        decision,
        proposal.growth_offer?.product_id
      );
      setProposal(p);
      setMessage(
        decision === "accept"
          ? `Add-on accepted. Total ₹${p.total_inr}. Confirm payment next.`
          : `Skipped add-on. Total ₹${p.total_inr}. Confirm payment next.`
      );
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmAndPay() {
    if (!proposal?.id) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await confirmProposal(proposal.id, proposal.total_inr);
      setProposal(result.proposal);
      setLocalCheckout(result);
      onCheckoutReady?.(result);

      if (result.checkout?.mock) {
        // Local mock verify path (no real Razorpay keys)
        const verified = await verifyPayment({
          payment_id: result.payment.id,
          razorpay_order_id: result.checkout.order_id,
          razorpay_payment_id: `pay_mock_${Date.now()}`,
          razorpay_signature: "mock_ok_demo",
        });
        setProposal(verified.proposal);
        setLocalCheckout(verified);
        onCheckoutReady?.(verified);
        setMessage(
          `Mock payment verified ₹${verified.payment.amount_inr}. ` +
            `Realized uplift: ₹${verified.growth_summary?.realized_paid_uplift ?? 0}. ` +
            "Add real rzp_test_ keys for dashboard transactions."
        );
      } else {
        await openRazorpayCheckout(result);
      }
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function openRazorpayCheckout(result) {
    const Razorpay = await loadRazorpayScript();
    const options = {
      key: result.checkout.key_id,
      amount: result.checkout.amount_paise,
      currency: result.checkout.currency,
      name: result.checkout.name,
      description: result.checkout.description,
      order_id: result.checkout.order_id,
      notes: result.checkout.notes,
      handler: async (response) => {
        try {
          const verified = await verifyPayment({
            payment_id: result.payment.id,
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
          });
          setProposal(verified.proposal);
          setLocalCheckout(verified);
          onCheckoutReady?.(verified);
          setMessage(
            `Paid ₹${verified.payment.amount_inr}. ` +
              `Realized uplift: ₹${verified.growth_summary?.realized_paid_uplift ?? 0}`
          );
        } catch (err) {
          setMessage(err.message);
        }
      },
      modal: {
        ondismiss: async () => {
          try {
            await failPayment(result.proposal.id, "user_cancelled");
            setMessage("Payment cancelled. Create a new proposal to retry.");
          } catch (err) {
            setMessage(err.message);
          }
        },
      },
    };
    const rzp = new Razorpay(options);
    rzp.open();
  }

  const status = proposal?.status;

  return (
    <section className="panel summary">
      <h2>Order summary</h2>

      <div className="actions">
        <button type="button" disabled={busy} onClick={startDemo}>
          Start demo: usual under ₹800
        </button>
      </div>

      {!proposal ? (
        <p className="muted">No proposal yet — use chat or the demo button.</p>
      ) : (
        <dl className="summary-grid">
          <dt>Proposal</dt>
          <dd>{proposal.id}</dd>
          <dt>Status</dt>
          <dd>{status}</dd>
          <dt>Total</dt>
          <dd>₹{proposal.total_inr}</dd>
          <dt>Baseline</dt>
          <dd>₹{proposal.baseline_total_inr ?? "—"}</dd>
          {proposal.growth_offer && (
            <>
              <dt>Offer</dt>
              <dd>
                {proposal.growth_offer.name} ₹{proposal.growth_offer.price_inr} → ₹
                {proposal.growth_offer.projected_total_inr}
              </dd>
            </>
          )}
        </dl>
      )}

      {status === "awaiting_addon_decision" && (
        <div className="actions">
          <button type="button" disabled={busy} onClick={() => onAddon("accept")}>
            Add it
          </button>
          <button type="button" disabled={busy} className="ghost" onClick={() => onAddon("skip")}>
            Skip
          </button>
        </div>
      )}

      {status === "awaiting_confirmation" && (
        <div className="actions">
          <button type="button" disabled={busy} onClick={onConfirmAndPay}>
            Confirm & pay ₹{proposal.total_inr}
          </button>
        </div>
      )}

      {message && <p className="msg">{message}</p>}

      {activeCheckout?.growth_summary && (
        <dl className="summary-grid">
          <dt>Projected uplift</dt>
          <dd>₹{activeCheckout.growth_summary.projected_uplift_inr ?? 0}</dd>
          <dt>Realized uplift</dt>
          <dd>
            {activeCheckout.growth_summary.realized_paid_uplift == null
              ? "pending verify"
              : `₹${activeCheckout.growth_summary.realized_paid_uplift}`}
          </dd>
        </dl>
      )}

      <hr />
      <h3>Backend</h3>
      <dl className="summary-grid">
        <dt>Razorpay ready</dt>
        <dd>{String(health?.razorpay_test_ready ?? false)}</dd>
        <dt>Mode</dt>
        <dd>{health?.razorpay_mode || "—"}</dd>
      </dl>
    </section>
  );
}
