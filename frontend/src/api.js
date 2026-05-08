function resolveApiBase() {
  const configured = import.meta.env.VITE_API_BASE;
  if (configured) return configured;

  if (typeof window === "undefined") {
    return "http://127.0.0.1:8000";
  }

  const { protocol, hostname } = window.location;
  if (protocol === "file:") {
    return "http://127.0.0.1:8000";
  }

  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return `${protocol}//${hostname}:8000`;
  }

  return window.location.origin;
}

const API_BASE = resolveApiBase();

function backendStartupHint() {
  return "Start the backend with .\\backend\\.venv\\Scripts\\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000";
}

async function fetchJson(path, options = {}) {
  try {
    const response = await fetch(`${API_BASE}${path}`, options);
    return await parseResponse(response);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(`Backend unreachable at ${API_BASE}. ${backendStartupHint()}.`);
    }
    throw error;
  }
}

async function parseResponse(response) {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function uploadAudio(file) {
  const form = new FormData();
  form.append("file", file);
  return fetchJson("/api/upload", {
    method: "POST",
    body: form,
  });
}

export async function quantizeAudio(payload) {
  return fetchJson("/api/quantize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getStatus(jobId) {
  return fetchJson(`/api/status/${jobId}`);
}

export async function getRuntimeConfig() {
  return fetchJson("/api/runtime-config");
}

export async function submitFeedback(payload) {
  return fetchJson("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function recordRecommendationSelection(payload) {
  return fetchJson("/api/recommendation-selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function downloadUrl(jobId, fileName) {
  return `${API_BASE}/api/download/${jobId}/${fileName}`;
}

export function getApiBase() {
  return API_BASE;
}
