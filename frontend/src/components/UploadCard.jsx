export default function UploadCard({ onUpload, uploadMeta, disabled = false }) {
  function handleFile(e) {
    if (disabled) return;
    const file = e.target.files?.[0];
    if (file) onUpload(file);
  }

  function handleDrop(e) {
    e.preventDefault();
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (file) onUpload(file);
  }

  return (
    <div className="panel upload-panel" onDragOver={(e) => e.preventDefault()} onDrop={handleDrop}>
      <h3>Upload</h3>
      <p>Drag/drop any audio file ffmpeg can decode, or choose a file.</p>
      <label className="btn secondary">
        {disabled ? "Processing..." : "Select Audio"}
        <input type="file" accept="audio/*,.wav,.mp3,.flac,.aiff,.aif,.m4a,.mp4,.aac,.ogg,.opus,.wma" onChange={handleFile} hidden disabled={disabled} />
      </label>
      {uploadMeta && (
        <div className="meta-list">
          <div>Job: {uploadMeta.job_id}</div>
          <div>Duration: {uploadMeta.duration_sec.toFixed(2)} s</div>
          <div>Sample Rate: {uploadMeta.sr} Hz</div>
          <div>Channels: {uploadMeta.channels}</div>
          <div>Source: {uploadMeta.source_extension || "unknown"} / {uploadMeta.source_codec || "unknown"}</div>
          <div>Est. BPM: {uploadMeta.estimated_bpm?.toFixed?.(1) ?? "n/a"}</div>
        </div>
      )}
    </div>
  );
}
