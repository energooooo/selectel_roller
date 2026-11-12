@echo off
setlocal
cd /d "%~dp0backend" || exit /b 1
".venv\Scripts\python.exe" -m watchfiles --filter python --target-type function app.uvicorn_loop.serve_dev app
