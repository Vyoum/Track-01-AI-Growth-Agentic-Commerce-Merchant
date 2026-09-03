/**
 * Money-safety strip. Every figure is a fold over the audit trail served by
 * GET /api/metrics/growth — no client-side counters.
 */
export default function Scoreboard({ metrics, title = "Money safety" }) {
  const safety = metrics?.safety;

  const tiles = [
    {
      label: "Money actions executed",
      value: safety ? safety.money_actions : "—",
      tone: "neutral",
    },
    {
      label: "Unauthorized charges",
      value: safety ? safety.unauthorized_charges : "—",
      tone: safety && safety.unauthorized_charges === 0 ? "good" : "bad",
    },
    {
      label: "Gated by explicit approval",
      value: safety ? `${safety.explicitly_gated_pct}%` : "—",
      tone: safety && safety.explicitly_gated_pct === 100 ? "good" : "warn",
    },
    {
      label: "Duplicate payments prevented",
      value: safety ? safety.duplicate_payments_prevented : "—",
      tone: "neutral",
    },
    {
      label: "Guardrail blocks",
      value: safety ? safety.guardrail_blocks : "—",
      tone: "neutral",
    },
    {
      label: "Failures surfaced, not swallowed",
      value: safety ? safety.payment_failures_surfaced : "—",
      tone: "neutral",
    },
  ];

  return (
    <section className="scoreboard" aria-label={title}>
      <p className="scoreboard-title">{title}</p>
      <div className="scoreboard-tiles">
        {tiles.map((tile) => (
          <div className="scoreboard-tile" key={tile.label} data-tone={tile.tone}>
            <span className="scoreboard-value">{tile.value}</span>
            <span className="scoreboard-label">{tile.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
