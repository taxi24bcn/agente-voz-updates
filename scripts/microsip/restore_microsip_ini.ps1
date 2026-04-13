# Restaura microsip.ini desde el backup .bak creado por setup_microsip_hooks.ps1.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\restore_microsip_ini.ps1
#
# IMPORTANTE: cierra MicroSIP antes de ejecutar.

$ErrorActionPreference = 'Stop'

$ini = Join-Path $env:APPDATA 'MicroSIP\microsip.ini'
$bak = "$ini.bak"

if (-not (Test-Path $bak)) {
    Write-Error "No existe backup: $bak"
    exit 1
}

Copy-Item $bak $ini -Force
Write-Host "OK. microsip.ini restaurado desde: $bak"
