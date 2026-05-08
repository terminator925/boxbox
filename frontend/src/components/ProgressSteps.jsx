const ordered = [
  "uploaded",
  "load_audio",
  "analyze",
  "segment",
  "grid",
  "dtw",
  "warp",
  "metrics",
  "export",
  "done",
];

export default function ProgressSteps({ status }) {
  const currentIndex = ordered.indexOf(status.stage);
  const percent = Math.round((status.progress || 0) * 100);
  const isBusy = status.status === "processing" || status.status === "uploading";

  return (
    <div className="panel progress-panel">
      <div className="progress-head">
        <h3>Progress</h3>
        <span className={`progress-pill ${isBusy ? "busy" : ""}`}>{percent}%</span>
      </div>
      <div
        className={`progress-bar ${isBusy ? "busy" : ""} ${status.status === "completed" ? "complete" : ""}`}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        role="progressbar"
      >
        <div className="progress-fill" style={{ width: `${percent}%` }}>
          <span className="progress-glow" />
          <span className="progress-sheen" />
        </div>
      </div>
      <div className="step-row">
        {ordered.map((step, i) => (
          <div
            key={step}
            className={`step ${i < currentIndex ? "active" : ""} ${i === currentIndex ? "current" : ""}`}
          >
            <span className="step-dot" />
            {step}
          </div>
        ))}
      </div>
      <p className={`progress-message ${isBusy ? "busy" : ""}`}>{status.message}</p>
    </div>
  );
}
