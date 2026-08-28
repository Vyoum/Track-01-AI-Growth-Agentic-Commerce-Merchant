import { useCallback, useEffect, useState } from "react";
import ChatWindow from "./ChatWindow.jsx";
import DecisionCenter from "./DecisionCenter.jsx";
import OrderSummaryCard from "./OrderSummaryCard.jsx";
import { fetchMeta, fetchHealth, fetchProposalAudit } from "./api.js";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [checkoutResult, setCheckoutResult] = useState(null);
  const [audit, setAudit] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [lastUserRequest, setLastUserRequest] = useState(
    'Order my usual, under ₹800'
  );

  const loadAudit = useCallback(async (proposalId) => {
    if (!proposalId) return;
    setAuditLoading(true);
    try {
      const data = await fetchProposalAudit(proposalId);
      setAudit(data);
    } catch {
      setAudit(null);
    } finally {
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    Promise.all([fetchMeta(), fetchHealth()])
      .then(([m, h]) => {
        setMeta(m);
        setHealth(h);
      })
      .catch((err) => setError(err.message || "Backend unreachable"));
  }, []);

  useEffect(() => {
    if (proposal?.id) {
      loadAudit(proposal.id);
    }
  }, [proposal?.id, proposal?.status, checkoutResult?.payment?.status, loadAudit]);

  const showDecisionCenter =
    proposal &&
    (checkoutResult?.payment ||
      proposal.status === "awaiting_addon_decision" ||
      proposal.status === "awaiting_confirmation" ||
      proposal.growth_offer);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Razorpay Buildathon · Track 01</p>
          <h1>{meta?.merchant || "Checkout Agent"}</h1>
          <p className="sub">
            {meta?.message ||
              "Chat to order, get growth upsell, and pay via Razorpay test mode."}
          </p>
        </div>
        <div className="status-pill" data-ok={health?.status === "ok"}>
          {health?.status === "ok"
            ? meta?.features?.agent
              ? "Agent online (Groq)"
              : "API online"
            : error || "Checking API…"}
        </div>
      </header>

      <main className="layout">
        <ChatWindow
          sessionId={sessionId}
          setSessionId={setSessionId}
          onUserMessage={setLastUserRequest}
          onProposalHint={setProposal}
          onCheckoutReady={(result) => {
            setCheckoutResult(result);
            if (result.proposal) setProposal(result.proposal);
          }}
        />
        <div className="side-stack">
          <OrderSummaryCard
            proposal={proposal}
            setProposal={setProposal}
            health={health}
            checkoutResult={checkoutResult}
            onCheckoutReady={(result) => {
              setCheckoutResult(result);
              if (result.proposal) setProposal(result.proposal);
            }}
          />
          {showDecisionCenter && (
            <DecisionCenter
              proposal={proposal}
              checkoutResult={checkoutResult}
              audit={audit}
              userRequest={lastUserRequest}
              loading={auditLoading}
            />
          )}
        </div>
      </main>
    </div>
  );
}
