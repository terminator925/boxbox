# BoxBox Frontend

React + Vite UI for upload, quantize controls, progress polling, and A/B playback.

```powershell
cd ..
.\backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Then run the frontend:

```powershell
cd frontend
npm install
npm run dev
```

If the UI says `Backend unreachable at ...`, the frontend is running but the API at port `8000` is not reachable yet.
