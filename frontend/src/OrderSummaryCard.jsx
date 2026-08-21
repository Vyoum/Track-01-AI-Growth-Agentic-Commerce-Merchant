export default function OrderSummaryCard({ proposal, health }) {
  return (
    <section className="panel summary">
      <h2>Order summary</h2>
      {!proposal ? (
        <p className="muted">No proposal yet. Send a chat message to preview the shell.</p>
      ) : (
        <dl className="summary-grid">
          <dt>Status</dt>
          <dd>{proposal.status}</dd>
          <dt>Last message</dt>
          <dd>{proposal.lastUserMessage || "—"}</dd>
          <dt>Note</dt>
          <dd>{proposal.note}</dd>
        </dl>
      )}
      <hr />
      <h3>Backend</h3>
      <dl className="summary-grid">
        <dt>Env</dt>
        <dd>{health?.app_env || "—"}</dd>
        <dt>Razorpay mode</dt>
        <dd>{health?.razorpay_mode || "—"}</dd>
        <dt>Mock catalog</dt>
        <dd>{String(health?.use_mock_catalog ?? "—")}</dd>
      </dl>
    </section>
  );
}
