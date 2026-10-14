@echo off
REM Inicia la API con un doble clic (Windows): carga .env.local, enciende Ollama si hace falta y arranca el servidor.
title API de analisis de documentos
cd /d "%~dp0"

if not exist ".env.local" (
  echo No existe .env.local con las variables de la API ^(API_KEYS...^).
  pause
  exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do set "%%a=%%b"

REM Si ya hay una API respondiendo en el puerto 8000, no se inicia otra
curl -s -o nul http://127.0.0.1:8000/health && (
  echo La API ya esta encendida en http://127.0.0.1:8000
  timeout /t 5 >nul
  exit /b 0
)

REM Modelo local (VLM): si esta configurado y Ollama no responde, se enciende
if defined LLM_URL (
  curl -s -o nul %LLM_URL%/api/tags || (
    echo Encendiendo Ollama...
    start "" /min ollama serve
    timeout /t 5 >nul
  )
)

echo.
echo  API en http://127.0.0.1:8000   ^(cierra esta ventana para apagarla^)
echo.
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
echo.
echo La API se detuvo.
pause
