# BoxBox Backend

FastAPI service for upload, DTW/ML quantization, and export.

## Run

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

API docs: `http://localhost:8000/docs`
