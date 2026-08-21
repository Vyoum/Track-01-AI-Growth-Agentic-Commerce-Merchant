import { useEffect, useState } from "react";
import ChatWindow from "./ChatWindow.jsx";
import OrderSummaryCard from "./OrderSummaryCard.jsx";
import { fetchMeta, fetchHealth } from "./api.js";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState(null);

  useEffect(() => {
    Promise.all([fetchMeta(), fetchHealth()])
      .then(([m, h]) => {
        setMeta(m);
        setHealth(h);
      })
      .catch((err) => setError(err.message || "Backend unreachable"));
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Razorpay Buildathon · Track 01</p>
          <h1>{meta?.merchant || "Checkout Agent"}</h1>
          <p className="sub">
            Conversational in-app checkout (scaffold). Agent + growth + payments come next.
          </p>
        </div>
        <div className="status-pill" data-ok={health?.status === "ok"}>
          {health?.status === "ok" ? "API online" : error || "Checking API…"}
        </div>
      </header>

      <main className="layout">
        <ChatWindow
          onProposalHint={(hint) => setProposal(hint)}
          backendMessage={meta?.message}
        />
        <OrderSummaryCard proposal={proposal} health={health} />
      </main>
    </div>
  );
}
