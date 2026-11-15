export default function ModelBadge({ llm }) {
  if (!llm) {
    return (
      <span className="model-badge" data-mode="pending">
        Checking model…
      </span>
    );
  }
  return (
    <span className="model-badge" data-mode={llm.mode} title={llm.configured_model || ""}>
      {llm.label}
    </span>
  );
}
