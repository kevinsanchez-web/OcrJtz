@echo off
REM Apaga la API (la que este escuchando en el puerto 8000) con un doble clic.
title Apagar API
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if (-not $c) { Write-Host 'La API no estaba encendida.'; exit 0 }; $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Start-Sleep 2; if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'No se pudo apagar la API.'; exit 1 } else { Write-Host 'API apagada.' }"
timeout /t 4 >nul
