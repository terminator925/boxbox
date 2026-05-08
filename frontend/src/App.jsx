import { useEffect, useMemo, useState } from "react";
import { downloadUrl, getApiBase, getRuntimeConfig, getStatus, quantizeAudio, uploadAudio } from "./api";
import UploadCard from "./components/UploadCard.jsx";
import Controls from "./components/Controls.jsx";
import ProgressSteps from "./components/ProgressSteps.jsx";
import WaveCompare from "./components/WaveCompare.jsx";
import ResultsPanel from "./components/ResultsPanel.jsx";
import RuntimeStrip from "./components/RuntimeStrip.jsx";

const defaultControls = {
  target_bpm: 100,
  resolution: 8,
  groove_preserve: 50,
  mode: "hybrid",
};

const buildStamp = import.meta.env.VITE_BOXBOX_BUILD_STAMP || "dev";

export default function App() {
  const [file, setFile] = useState(null);
  const [uploadMeta, setUploadMeta] = useState(null);
  const [jobId, setJobId] = useState("");
  const [controls, setControls] = useState(defaultControls);
  const [status, setStatus] = useState({ status: "idle", stage: "idle", progress: 0, message: "Upload a file" });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [isQuantizing, setIsQuantizing] = useState(false);
  const [runtimeConfig, setRuntimeConfig] = useState(null);
  const [runtimeConfigError, setRuntimeConfigError] = useState("");

  const originalUrl = useMemo(() => (file ? URL.createObjectURL(file) : ""), [file]);

  useEffect(() => {
    let cancelled = false;
    let retryTimer = null;

    const loadRuntimeConfig = async () => {
      try {
        const payload = await getRuntimeConfig();
        if (cancelled) return;
        setRuntimeConfig(payload);
        setRuntimeConfigError("");
      } catch (e) {
        if (cancelled) return;
        setRuntimeConfig(null);
        setRuntimeConfigError(e.message);
        retryTimer = setTimeout(loadRuntimeConfig, 3000);
      }
    };

    loadRuntimeConfig();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let timer = null;
    const tick = async () => {
      try {
        const s = await getStatus(jobId);
        setStatus(s);
      } catch {
        // ignore transient polling errors
      }
      timer = setTimeout(tick, 800);
    };
    tick();
    return () => timer && clearTimeout(timer);
  }, [jobId]);

  async function onUpload(selectedFile) {
    if (isQuantizing) return;
    setError("");
    setResult(null);
    setStatus({ status: "uploading", stage: "uploading", progress: 0.02, message: "Uploading..." });
    setFile(selectedFile);
    try {
      const meta = await uploadAudio(selectedFile);
      setUploadMeta(meta);
      setJobId(meta.job_id);
      if (Number.isFinite(meta.estimated_bpm) && meta.estimated_bpm > 0) {
        const estimated = Math.round(meta.estimated_bpm);
        setControls((current) => ({ ...current, target_bpm: estimated }));
      }
      setStatus({ status: "uploaded", stage: "uploaded", progress: 0, message: "Ready to quantize" });
    } catch (e) {
      setError(e.message);
      setStatus({ status: "failed", stage: "error", progress: 1, message: "Upload failed" });
    }
  }

  async function onQuantize() {
    if (isQuantizing) {
      setError("A quantization job is already running");
      return;
    }
    if (!jobId) {
      setError("Upload audio first");
      return;
    }
    setError("");
    setResult(null);
    setIsQuantizing(true);
    setStatus({ status: "processing", stage: "load_audio", progress: 0.03, message: "Starting quantization..." });
    try {
      const payload = { job_id: jobId, ...controls };
      const r = await quantizeAudio(payload);
      setResult(r);
      const finalStatus = await getStatus(jobId);
      setStatus(finalStatus);
    } catch (e) {
      setError(e.message);
      setStatus({ status: "failed", stage: "error", progress: 1, message: "Quantize failed" });
    } finally {
      setIsQuantizing(false);
    }
  }

  const quantizedFile = result?.output_files?.browser_audio || "quantized.wav";
  const quantizedUrl = result ? downloadUrl(jobId, quantizedFile) : "";
  const gridCheckFile = result?.output_files?.metronome_check_audio || "";
  const gridCheckUrl = result && gridCheckFile ? downloadUrl(jobId, gridCheckFile) : "";
  const apiBase = getApiBase();

  return (
    <div className="app-shell">
      <header className="hero">
        <p className="brand-kicker">Audio Time-Warp SaaS MVP</p>
        <h1>BoxBox</h1>
        <p>Hybrid-first beat quantization with deterministic DTW fallback when artifacts or edge cases call for it.</p>
        <p className="build-stamp">Build Stamp: {buildStamp}</p>
      </header>

      <section className="grid">
        <UploadCard onUpload={onUpload} uploadMeta={uploadMeta} disabled={isQuantizing} />
        <Controls
          controls={controls}
          setControls={setControls}
          onRun={onQuantize}
          disabled={!jobId || isQuantizing}
          running={isQuantizing}
        />
      </section>

      <section className="panel runtime-panel">
        <div>
          <h3>Active Engine</h3>
          <p>
            The backend currently reports the promoted fast-quality path before this run starts.
          </p>
        </div>
        <RuntimeStrip config={runtimeConfig?.runtime_config} label="Active backend runtime configuration" />
        {runtimeConfig?.defaults && (
          <p className="runtime-note">
            GUI defaults: {runtimeConfig.defaults.mode} mode, groove {runtimeConfig.defaults.groove_preserve}, whole-number BPM.
            Model {runtimeConfig.model_available ? "ready" : "not found"}.
          </p>
        )}
        {runtimeConfigError && (
          <p className="runtime-note warn">
            Runtime config unavailable until the backend is reachable.
          </p>
        )}
      </section>

      <ProgressSteps status={status} />

      {error && <div className="error-box">{error}</div>}
      {error && (
        <div className="error-box">
          Expected backend: <strong>{apiBase}</strong>
        </div>
      )}

      <section className="grid lower">
        <WaveCompare originalUrl={originalUrl} quantizedUrl={quantizedUrl} gridCheckUrl={gridCheckUrl} />
        <ResultsPanel jobId={jobId} result={result} setControls={setControls} />
      </section>
    </div>
  );
}

