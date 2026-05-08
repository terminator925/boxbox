import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
DEFAULT_TARGET_BPM = 100
DEFAULT_RESOLUTION = 8
DEFAULT_GROOVE_PRESERVE = 50
MAX_UPLOAD_MB = 200


def _local_dev_origins() -> list[str]:
    origins = {
        "null",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    for port in range(4173, 4181):
        origins.add(f"http://localhost:{port}")
        origins.add(f"http://127.0.0.1:{port}")
    for port in range(5173, 5191):
        origins.add(f"http://localhost:{port}")
        origins.add(f"http://127.0.0.1:{port}")
    return sorted(origins)


def _configured_origins() -> list[str]:
    configured = []
    raw = str(os.getenv("BOXBOX_CORS_ORIGINS", "")).strip()
    if raw:
        configured.extend(origin.strip() for origin in raw.split(",") if origin.strip())
    render_url = str(os.getenv("RENDER_EXTERNAL_URL", "")).strip()
    if render_url:
        configured.append(render_url.rstrip("/"))
    return configured


CORS_ORIGINS = sorted(set(_local_dev_origins()) | set(_configured_origins()))

for directory in [UPLOADS_DIR, OUTPUTS_DIR, MODELS_DIR, DATA_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
