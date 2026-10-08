@echo off
REM Ejecuta la API en esta maquina (Windows). Lee API_KEYS de .env.local
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do set "%%a=%%b"
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
