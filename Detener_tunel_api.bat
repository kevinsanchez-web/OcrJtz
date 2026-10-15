@echo off
REM Cierra el tunel de la API y devuelve la API a "sin limites por minuto".
title Detener tunel de la API
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\registrar_puente.ps1" -Accion clear
powershell -NoProfile -Command "Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force; Write-Host 'Tunel cerrado: la direccion publica ya no funciona.'"
powershell -NoProfile -Command "$f = Join-Path '%~dp0' '.env.local'; if (Test-Path $f) { $t = [IO.File]::ReadAllText($f).TrimStart([char]0xFEFF); $t = $t -replace '(?m)^RATE_LIMIT_PER_MIN=.*$','RATE_LIMIT_PER_MIN=0' -replace '(?m)^RATE_LIMIT_READS_PER_MIN=.*$','RATE_LIMIT_READS_PER_MIN=0'; [IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding $false)) }"
call "%~dp0Apagar_API.bat"
start "" /min cmd /c "%~dp0Iniciar_API.bat"
echo API reiniciada sin limites por minuto.
timeout /t 4 >nul
