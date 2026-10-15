# Avisa al Worker de Cloudflare (direccion fija *.workers.dev) cual es el tunel vigente, o lo borra.
#   registrar_puente.ps1 -Accion set -Url https://xxx.trycloudflare.com
#   registrar_puente.ps1 -Accion clear
# Lee TUNNEL_REGISTER_URL y TUNNEL_REGISTER_KEY de .env.local; si faltan, no hace nada (se usa la direccion del tunel tal cual).
param([ValidateSet("set", "clear")][string]$Accion, [string]$Url = "")
$root = Split-Path -Parent $PSScriptRoot
$envf = Join-Path $root ".env.local"
if (-not (Test-Path $envf)) { exit 0 }
$txt = [IO.File]::ReadAllText($envf)
$wu = [regex]::Match($txt, "(?m)^TUNNEL_REGISTER_URL=(.+)$").Groups[1].Value.Trim()
$rk = [regex]::Match($txt, "(?m)^TUNNEL_REGISTER_KEY=(.+)$").Groups[1].Value.Trim()
if (-not $wu -or -not $rk) { exit 0 }

$tmp = Join-Path $env:TEMP "puente_body.json"
try {
    if ($Accion -eq "set") {
        [IO.File]::WriteAllText($tmp, ('{"url":"' + $Url + '"}'), (New-Object Text.UTF8Encoding $false))
        $out = & curl.exe -sS -m 20 -X POST -H "X-Register-Key: $rk" -H "Content-Type: application/json" --data-binary "@$tmp" -w " [HTTP %{http_code}]" "$wu/__tunnel" 2>&1
    } else {
        $out = & curl.exe -sS -m 20 -X DELETE -H "X-Register-Key: $rk" -w " [HTTP %{http_code}]" "$wu/__tunnel" 2>&1
    }
    $ok = ($out | Out-String) -match "HTTP 200"
    if ($ok) { Write-Host " Direccion fija actualizada: $wu" -ForegroundColor Green }
    else { Write-Host " No se pudo avisar a $wu (el tunel funciona, pero la direccion fija no apunta a el): $out" -ForegroundColor Yellow }
} finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
