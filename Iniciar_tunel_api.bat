@echo off
REM Abre un tunel temporal SOLO hacia la API y muestra la direccion para JETSON_API_URL. Dejar esta ventana abierta.
title Tunel de la API
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tunel_api.ps1"
echo.
echo El tunel se cerro.
pause
