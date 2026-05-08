const labels = {
  4: "Quarter",
  8: "Eighth",
  16: "Sixteenth",
};

export default function Controls({ controls, setControls, onRun, disabled, running }) {
  return (
    <div className="panel controls-panel">
      <h3>Controls</h3>
      <div className="form-row">
        <label>Target BPM</label>
        <input
          type="number"
          min="40"
          max="240"
          step="1"
          value={controls.target_bpm}
          onChange={(e) => setControls({ ...controls, target_bpm: Math.round(Number(e.target.value)) })}
        />
      </div>
      <div className="form-row">
        <label>Grid Resolution</label>
        <select
          value={controls.resolution}
          onChange={(e) => setControls({ ...controls, resolution: Number(e.target.value) })}
        >
          {[4, 8, 16].map((v) => (
            <option key={v} value={v}>
              {labels[v]}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Mode (Hybrid Default)</label>
        <select value={controls.mode} onChange={(e) => setControls({ ...controls, mode: e.target.value })}>
          <option value="hybrid">Hybrid</option>
          <option value="dtw">DTW</option>
          <option value="ml">ML</option>
        </select>
      </div>
      <div className="form-row">
        <label>Groove Preserve: {controls.groove_preserve}</label>
        <input
          type="range"
          min="0"
          max="100"
          value={controls.groove_preserve}
          onChange={(e) => setControls({ ...controls, groove_preserve: Number(e.target.value) })}
        />
      </div>
      <button className="btn" onClick={onRun} disabled={disabled}>
        {running ? "Quantizing..." : "Quantize"}
      </button>
    </div>
  );
}
