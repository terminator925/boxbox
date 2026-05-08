@echo off
cd /d H:\BoxBox
set BOXBOX_INFER_ACCELERATOR=torch
set BOXBOX_INFER_CANDIDATE_STRATEGY=core4_adaptive_plus
set BOXBOX_HYBRID_SEARCH_STRATEGY=core4
H:\BoxBox\backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 > H:\BoxBox\outputs\gui_launch\backend_task.out.log 2> H:\BoxBox\outputs\gui_launch\backend_task.err.log
