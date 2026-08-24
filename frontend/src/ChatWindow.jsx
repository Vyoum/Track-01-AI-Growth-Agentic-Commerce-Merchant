import { useState } from "react";
import {
  failPayment,
  openRazorpayCheckout,
  sendChat,
  verifyPayment,
} from "./api.js";

const STARTER = [
  {
    role: "assistant",
    text: 'Hi! I can help you checkout. Try: "Order my usual, under ₹800"',
  },
];

export default function ChatWindow({
  onProposalHint,
  onCheckoutReady,
  sessionId,
  setSessionId,
}) {
  const [messages, setMessages] = useState(STARTER);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;

    setBusy(true);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);

    try {
      const res = await sendChat(text, sessionId);
      if (res.session_id && setSessionId) {
        setSessionId(res.session_id);
      }
      setMessages((prev) => [...prev, { role: "assistant", text: res.reply }]);

      if (res.proposal) {
        onProposalHint?.(res.proposal);
      }

      if (res.checkout && !res.checkout.mock && res.checkout.key_id) {
        await openRazorpayCheckout(
          res,
          async (response) => {
            const verified = await verifyPayment({
              payment_id: res.payment?.id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            });
            onCheckoutReady?.(verified);
            setMessages((prev) => [
              ...prev,
              {
                role: "assistant",
                text: `Payment successful — ₹${verified.payment?.amount_inr}. Realized uplift: ₹${verified.growth_summary?.realized_paid_uplift ?? 0}`,
              },
            ]);
          },
          async () => {
            if (res.proposal?.id) {
              await failPayment(res.proposal.id, "user_cancelled");
            }
            setMessages((prev) => [
              ...prev,
              { role: "assistant", text: "Payment cancelled. Say 'confirm payment' to try again." },
            ]);
          }
        );
      } else if (res.checkout?.mock && res.payment) {
        const verified = await verifyPayment({
          payment_id: res.payment.id,
          razorpay_order_id: res.checkout.order_id,
          razorpay_payment_id: `pay_mock_${Date.now()}`,
          razorpay_signature: "mock_ok_chat",
        });
        onCheckoutReady?.(verified);
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: `Mock payment verified — ₹${verified.payment?.amount_inr}. Realized uplift: ₹${verified.growth_summary?.realized_paid_uplift ?? 0}`,
          },
        ]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: `Sorry — ${err.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel chat">
      <h2>Chat</h2>
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.text}
          </div>
        ))}
      </div>
      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
          placeholder='e.g. "Order my usual, under ₹800"'
        />
        <button type="button" onClick={send} disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </section>
  );
}
