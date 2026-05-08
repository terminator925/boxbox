import { useEffect, useRef } from "react";
import WaveSurfer from "wavesurfer.js";

function Player({ url, title }) {
  const containerRef = useRef(null);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !url) return;
    wsRef.current?.destroy();
    wsRef.current = WaveSurfer.create({
      container: containerRef.current,
      waveColor: "#99d5c9",
      progressColor: "#0f766e",
      cursorColor: "#114240",
      barWidth: 2,
      height: 84,
      dragToSeek: true,
    });
    wsRef.current.load(url);
    return () => wsRef.current?.destroy();
  }, [url]);

  return (
    <div className="wave-slot">
      <div className="wave-head">
        <strong>{title}</strong>
        <button className="btn secondary" onClick={() => wsRef.current?.playPause()} disabled={!url}>
          Play / Pause
        </button>
      </div>
      <div ref={containerRef} className="waveform" />
    </div>
  );
}

export default function WaveCompare({ originalUrl, quantizedUrl, gridCheckUrl }) {
  return (
    <div className="panel">
      <h3>A/B Playback</h3>
      <Player url={originalUrl} title="Original" />
      <Player url={quantizedUrl} title="Quantized" />
      {gridCheckUrl && (
        <>
          <Player url={gridCheckUrl} title="Grid Check" />
          <p className="wave-note">Quantized output mixed with the fixed BPM click for quick DAW-lock confidence.</p>
        </>
      )}
    </div>
  );
}
