import { useCallback, useEffect, useState } from "react";
import ChatWindow from "./ChatWindow.jsx";
import DecisionCenter from "./DecisionCenter.jsx";
import NavBar from "./NavBar.jsx";
import OrderSummaryCard from "./OrderSummaryCard.jsx";
import { fetchMeta, fetchHealth, fetchProposal, fetchProposalAudit, fetchDemoCustomers } from "./api.js";
import ModelBadge from "./ModelBadge.jsx";

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
  const [customers, setCustomers] = useState([]);
  const [userId, setUserId] = useState(
    () => window.localStorage.getItem("atelier_demo_user") || ""
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
    Promise.all([fetchMeta(), fetchHealth(), fetchDemoCustomers()])
      .then(([m, h, book]) => {
        setMeta(m);
        setHealth(h);
        const list = book.customers || [];
        setCustomers(list);
        setUserId((current) => {
          if (current && list.some((c) => c.user_id === current)) return current;
          return book.demo_user_id || list[0]?.user_id || "demo_user_01";
        });
      })
      .catch((err) => setError(err.message || "Backend unreachable"));
  }, []);

  const customer = customers.find((c) => c.user_id === userId) || null;
  const budget = customer?.preferred_budget_inr || 800;

  function selectCustomer(nextId) {
    setUserId(nextId);
    window.localStorage.setItem("atelier_demo_user", nextId);
    setProposal(null);
    setSessionId(null);
    setCheckoutResult(null);
    setAudit(null);
    const next = customers.find((c) => c.user_id === nextId);
    const nextBudget = next?.preferred_budget_inr || 800;
    setLastUserRequest(
      next?.completed_orders
        ? `Order my usual, under ₹${nextBudget}`
        : `I'm new here, something good under ₹${nextBudget}`
    );
  }

  useEffect(() => {
    if (proposal?.id) {
      loadAudit(proposal.id);
    }
  }, [proposal?.id, proposal?.status, checkoutResult?.payment?.status, loadAudit]);

  useEffect(() => {
    if (
      !proposal?.id ||
      !["awaiting_addon_decision", "awaiting_merchant_approval"].includes(
        proposal.status
      )
    ) {
      return undefined;
    }
    const id = setInterval(async () => {
      try {
        const latest = await fetchProposal(proposal.id);
        setProposal((prev) => {
          if (!prev || prev.id !== latest.id) return prev;
          if (prev.status === latest.status && !prev.growth_offer === !latest.growth_offer) {
            return prev;
          }
          return latest;
        });
      } catch {
        /* keep local snapshot */
      }
    }, 3000);
    return () => clearInterval(id);
  }, [proposal?.id, proposal?.status]);

  const showDecisionCenter =
    proposal &&
    (checkoutResult?.payment ||
      proposal.status === "awaiting_merchant_approval" ||
      proposal.status === "awaiting_addon_decision" ||
      proposal.status === "awaiting_confirmation" ||
      proposal.growth_offer ||
      proposal.campaign_decision);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <p className="eyebrow">Atelier · Agentic Commerce</p>
          <h1>{meta?.merchant || "The checkout, composed"}</h1>
          <p className="sub">
            {meta?.message ||
              "Speak an order. One optional add-on. Razorpay test checkout — never a second charge."}
          </p>
        </div>
        <div className="header-status">
          <div className="status-pill" data-ok={health?.status === "ok"}>
            {health?.status === "ok"
              ? meta?.features?.agent
                ? "Agent online (Groq)"
                : "API online · demo fallback"
              : error || "Checking API…"}
          </div>
          <ModelBadge llm={meta?.llm} />
        </div>
      </header>

      <div className="customer-switch">
        <label>
          <span>Demo customer</span>
          <select
            value={userId}
            onChange={(e) => selectCustomer(e.target.value)}
            disabled={!customers.length}
          >
            {customers.map((c) => (
              <option key={c.user_id} value={c.user_id}>
                {c.name} · {c.city || "India"}
              </option>
            ))}
          </select>
        </label>
        {customer && (
          <p className="muted">
            {customer.email}
            {customer.city ? ` · ${customer.city}` : ""}
            {` · budget ₹${budget}`}
            {customer.completed_orders
              ? ` · ${customer.completed_orders} past orders · usual ₹${customer.usual_total_inr}`
              : " · no order history yet · bestsellers"}
          </p>
        )}
      </div>

      <NavBar current="/">
        <a className="nav-cta" href="/demo">
          Judge Mode
        </a>
      </NavBar>

      <main className="layout">
        <ChatWindow
          key={userId || "demo"}
          sessionId={sessionId}
          setSessionId={setSessionId}
          userId={userId || "demo_user_01"}
          customer={customer}
          modelInfo={meta?.llm}
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
            userId={userId || "demo_user_01"}
            budget={budget}
            customer={customer}
            sessionId={sessionId}
            modelInfo={meta?.llm}
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
