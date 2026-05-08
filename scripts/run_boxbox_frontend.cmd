@echo off
cd /d H:\BoxBox
H:\BoxBox\backend\.venv\Scripts\python.exe -m http.server 5173 --bind 127.0.0.1 --directory H:\BoxBox\frontend\dist > H:\BoxBox\outputs\gui_launch\frontend_task.out.log 2> H:\BoxBox\outputs\gui_launch\frontend_task.err.log
