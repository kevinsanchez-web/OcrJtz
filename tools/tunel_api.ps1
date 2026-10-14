# Abre un tunel temporal de Cloudflare SOLO hacia la API (puerto 8000) y muestra la direccion para JETSON_API_URL.
# No expone la app de transito ni nada mas: unicamente la API, que sigue exigiendo su clave (X-API-Key).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "tools\bin\cloudflared.exe"
$log = Join-Path $root "logs\tunnel_api.log"
New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null

if (-not (Test-Path $exe)) { Write-Host "Falta cloudflared en $exe (se descarga de github.com/cloudflare/cloudflared/releases)." -ForegroundColor Red; exit 1 }
function Test-Api { try { (Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }
if (-not (Test-Api)) { Write-Host "La API no esta encendida en el puerto 8000. Enciendela (Iniciar API o el panel) y vuelve a intentar." -ForegroundColor Red; exit 1 }

# Con la API a la vista de internet se activan los limites por minuto (30 subidas y 600 consultas)
$envf = Join-Path $root ".env.local"
$txt = [IO.File]::ReadAllText($envf).TrimStart([char]0xFEFF)
$new = $txt
foreach ($pair in @(@("RATE_LIMIT_PER_MIN", "30"), @("RATE_LIMIT_READS_PER_MIN", "600"))) {
    if ($new -match "(?m)^$($pair[0])=") { $new = $new -replace "(?m)^$($pair[0])=.*$", "$($pair[0])=$($pair[1])" } else { $new = $new.TrimEnd() + "`n$($pair[0])=$($pair[1])`n" }
}
if ($new -ne $txt) {
    [IO.File]::WriteAllText($envf, $new, (New-Object Text.UTF8Encoding $false))
    Write-Host "Activando limites por minuto y reiniciando la API..."
    & cmd /c "`"$root\Apagar_API.bat`"" | Out-Null
    Start-Sleep 2
    Start-Process -FilePath cmd.exe -ArgumentList "/c", "`"$root\Iniciar_API.bat`"" -WorkingDirectory $root -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 40 -and -not (Test-Api); $i++) { Start-Sleep 2 }
    if (-not (Test-Api)) { Write-Host "La API no volvio a arrancar." -ForegroundColor Red; exit 1 }
}

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
# --http-host-header: la API ve la peticion como local (127.0.0.1:8000) y no hace falta tocar ALLOWED_HOSTS
$p = Start-Process -FilePath $exe -ArgumentList "tunnel", "--url", "http://127.0.0.1:8000", "--http-host-header", "127.0.0.1:8000", "--no-autoupdate" `
    -NoNewWindow -PassThru -RedirectStandardError $log -RedirectStandardOutput "$log.out"
try {
    $url = $null
    for ($i = 0; $i -lt 40 -and -not $url; $i++) {
        Start-Sleep 2
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Where-Object { $_.Line -notmatch "api\.trycloudflare" } | Select-Object -First 1
        if ($m) { $url = [regex]::Match($m.Line, "https://[a-z0-9-]+\.trycloudflare\.com").Value }
    }
    if (-not $url) { Write-Host "El tunel no dio una direccion (revisa tu conexion a internet)." -ForegroundColor Red; exit 1 }
    $analyze = "$url/api/v1/analyze-document"
    Set-Clipboard -Value $analyze
    Write-Host ""
    Write-Host "==============================================================" -ForegroundColor Green
    Write-Host " TUNEL ABIERTO: la API es accesible desde internet" -ForegroundColor Green
    Write-Host "==============================================================" -ForegroundColor Green
    Write-Host " En el .env de tu app de transito (si corre en OTRO equipo o en Firebase):"
    Write-Host "   JETSON_API_URL=`"$analyze`""
    Write-Host "   JETSON_API_KEY=`"<la clave de tu app: en API_KEYS de .env.local (la que no lleva nombre)>`""
    Write-Host " (la direccion ya esta copiada al portapapeles)"
    Write-Host ""
    Write-Host " * Si tu app corre en ESTE PC, NO cambies su JETSON_API_URL: sigue con http://127.0.0.1:8000"
    Write-Host "   (este PC no resuelve las direcciones trycloudflare.com)."
    Write-Host " * La direccion CAMBIA cada vez que se abre el tunel y deja de funcionar al cerrar esta ventana."
    Write-Host " * Para cerrar: Ctrl+C o Detener_tunel_api.bat (quita tambien los limites por minuto)."
    Write-Host "==============================================================" -ForegroundColor Green
    Wait-Process -Id $p.Id
} finally {
    if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
}
