import { useEffect, useRef, useState } from "react";
import {
  failPayment,
  openRazorpayCheckout,
  sendChat,
  verifyPayment,
} from "./api.js";
import ModelBadge from "./ModelBadge.jsx";

function starterFor(customer) {
  const name = customer?.name?.split(" ")[0];
  const budget = customer?.preferred_budget_inr;
  const greeting = name ? `Welcome in, ${name}.` : "Welcome in.";
  const usual = customer?.completed_orders
    ? `your usual${budget ? ` under ₹${budget}` : ""}`
    : "a popular starter cart";
  return [
    {
      role: "assistant",
      text: `${greeting} I can assemble ${usual}, surface a single optional add-on, and take you through Razorpay test checkout.`,
    },
  ];
}

function traceChips(trace) {
  if (!trace) return [];
  const chips = [];
  if (trace.mode === "groq") {
    chips.push(trace.model ? `Powered by ${trace.model} via Groq` : "Groq");
  } else if (trace.handled_by === "checkout") {
    chips.push("Checkout");
  } else {
    chips.push("Demo fallback · no Groq key");
  }
  if (trace.tools?.length) {
    chips.push(trace.tools.join(" → "));
  } else if (trace.mode === "groq") {
    chips.push("No tool call");
  }
  if (typeof trace.latency_ms === "number") chips.push(`${trace.latency_ms} ms`);
  if (trace.usage?.total_tokens) {
    chips.push(`${trace.usage.total_tokens} tokens`);
  } else if (trace.mode === "groq" || trace.mode === "demo_mock") {
    chips.push("tokens n/a");
  }
  return chips;
}

const CHIPS = [
  "Order my usual, under ₹800",
  "Something protein-rich under ₹600",
  "Skip add-ons this time",
];

export default function ChatWindow({
  onProposalHint,
  onCheckoutReady,
  onUserMessage,
  sessionId,
  setSessionId,
  userId = "demo_user_01",
  customer,
  modelInfo,
}) {
  const [messages, setMessages] = useState(() => starterFor(customer));
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const messagesRef = useRef(null);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  async function send(preset) {
    const text = (preset ?? input).trim();
    if (!text || busy) return;

    setBusy(true);
    setInput("");
    onUserMessage?.(text);
    setMessages((prev) => [...prev, { role: "user", text }]);

    try {
      const res = await sendChat(text, sessionId, userId);
      if (res.session_id && setSessionId) {
        setSessionId(res.session_id);
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: res.reply,
          trace: {
            handled_by: res.handled_by,
            model: res.model,
            mode: res.mode || (res.handled_by === "groq" ? "groq" : "demo_mock"),
            tools: Array.isArray(res.tool_trace) ? res.tool_trace : [],
            latency_ms: res.latency_ms,
            usage: res.usage,
          },
        },
      ]);

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
                trace: { handled_by: "checkout", mode: "checkout" },
              },
            ]);
          },
          async () => {
            if (res.proposal?.id) {
              try {
                await failPayment(res.proposal.id, "user_cancelled");
              } catch {
                /* already paid or already failed */
              }
            }
            setMessages((prev) => [
              ...prev,
              {
                role: "assistant",
                text:
                  "Checkout dismissed. This proposal is terminal — no realized uplift. Start a new order to retry.",
              },
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
            trace: { handled_by: "checkout", mode: "checkout" },
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
      <div className="chat-head">
        <h2>Concierge</h2>
        <div className="chat-head-meta">
          <ModelBadge llm={modelInfo} />
          <span className="chat-live">{busy ? "Thinking" : "Live"}</span>
        </div>
      </div>
      <div className="messages" ref={messagesRef}>
        {messages.map((m, i) => (
          <div key={i} className={`bubble-wrap ${m.role}`}>
            <div className={`bubble ${m.role}`}>{m.text}</div>
            {m.trace && (
              <div className="bubble-trace" aria-label="Model trace">
                {traceChips(m.trace).map((chip) => (
                  <span key={chip}>{chip}</span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="bubble assistant typing" aria-label="Assistant is typing">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>
      <div className="chat-chips">
        {CHIPS.map((chip) => (
          <button
            key={chip}
            type="button"
            className="chip"
            disabled={busy}
            onClick={() => send(chip)}
          >
            {chip}
          </button>
        ))}
      </div>
      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
          placeholder="Tell me what to order…"
        />
        <button type="button" onClick={() => send()} disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </section>
  );
}
