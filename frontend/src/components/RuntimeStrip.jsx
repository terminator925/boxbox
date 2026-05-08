export function runtimeConfigItems(config) {
  if (!config) return [];
  return [
    ["Accelerator", config.inference_accelerator],
    ["Inference", config.inference_candidate_strategy],
    ["Hybrid Search", config.hybrid_search_strategy],
    ["Verify Top-K", config.hybrid_verify_top_k],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
}

export default function RuntimeStrip({ config, label = "Runtime configuration" }) {
  const items = runtimeConfigItems(config);
  if (items.length === 0) return null;

  return (
    <div className="runtime-strip" aria-label={label}>
      {items.map(([itemLabel, value]) => (
        <div key={itemLabel}>
          <span>{itemLabel}</span>
          <strong>{String(value)}</strong>
        </div>
      ))}
    </div>
  );
}
