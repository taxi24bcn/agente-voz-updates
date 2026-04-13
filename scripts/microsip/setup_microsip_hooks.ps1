# Setup completo de la integración MicroSIP -> Asistente de Voz Taxi24H.
#
# Este script:
#  1. Crea C:\Taxi24H\microsip-hooks\ con los 3 .bat (incoming/answered/ended).
#  2. Hace backup de microsip.ini.
#  3. Escribe en microsip.ini las claves cmdIncomingCall, cmdCallAnswer y
#     cmdCallEnd preservando codificación Unicode / UTF-16 LE.
#
# IMPORTANTE: cierra MicroSIP COMPLETAMENTE antes de ejecutar (clic derecho
# en el icono de la bandeja -> Salir). Si no, MicroSIP sobreescribirá el .ini
# al cerrar y perderás los cambios.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\setup_microsip_hooks.ps1
#
# Rollback:
#   powershell -ExecutionPolicy Bypass -File .\restore_microsip_ini.ps1

$ErrorActionPreference = 'Stop'

# --- 1. Crear carpeta y .bat de hooks ------------------------------------

$hooksDir = 'C:\Taxi24H\microsip-hooks'
New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null

$incomingBat = @'
@echo off
C:\Windows\System32\curl.exe -fsS -o NUL "http://127.0.0.1:8733/call/incoming?number=%~1"
'@

$answeredBat = @'
@echo off
C:\Windows\System32\curl.exe -fsS -o NUL "http://127.0.0.1:8733/call/answered?number=%~1"
'@

$endedBat = @'
@echo off
C:\Windows\System32\curl.exe -fsS -o NUL "http://127.0.0.1:8733/call/ended?number=%~1"
'@

Set-Content -Path (Join-Path $hooksDir 'incoming.bat') -Value $incomingBat -Encoding ASCII
Set-Content -Path (Join-Path $hooksDir 'answered.bat') -Value $answeredBat -Encoding ASCII
Set-Content -Path (Join-Path $hooksDir 'ended.bat')    -Value $endedBat    -Encoding ASCII

Write-Host "[1/3] Hooks creados en: $hooksDir"
Write-Host "      - incoming.bat"
Write-Host "      - answered.bat"
Write-Host "      - ended.bat"

# --- 2. Backup de microsip.ini -------------------------------------------

$ini = Join-Path $env:APPDATA 'MicroSIP\microsip.ini'

if (-not (Test-Path $ini)) {
    Write-Error "No se encontró microsip.ini en: $ini"
    exit 1
}

$bak = "$ini.bak"
Copy-Item $ini $bak -Force
Write-Host "[2/3] Backup creado en: $bak"

# --- 3. Editar microsip.ini preservando UTF-16 ---------------------------

$incomingPath = Join-Path $hooksDir 'incoming.bat'
$answeredPath = Join-Path $hooksDir 'answered.bat'
$endedPath    = Join-Path $hooksDir 'ended.bat'

# Lee como Unicode (UTF-16 LE) — es como MicroSIP guarda el .ini.
$lines = Get-Content $ini -Encoding Unicode

function Set-IniKey {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )

    $pattern = '^(?i)' + [regex]::Escape($Key) + '='
    $found = $false

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $pattern) {
            $Lines[$i] = "$Key=$Value"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $Lines += "$Key=$Value"
    }

    return ,$Lines
}

$lines = Set-IniKey -Lines $lines -Key 'cmdIncomingCall' -Value $incomingPath
$lines = Set-IniKey -Lines $lines -Key 'cmdCallAnswer'   -Value $answeredPath
$lines = Set-IniKey -Lines $lines -Key 'cmdCallEnd'      -Value $endedPath

Set-Content -Path $ini -Value $lines -Encoding Unicode
Write-Host "[3/3] microsip.ini actualizado con claves cmdIncomingCall / cmdCallAnswer / cmdCallEnd"
Write-Host ""
Write-Host "Valores aplicados:"
Write-Host "  cmdIncomingCall=$incomingPath"
Write-Host "  cmdCallAnswer=$answeredPath"
Write-Host "  cmdCallEnd=$endedPath"
Write-Host ""
Write-Host "OK. Ahora abre MicroSIP y haz una llamada de prueba."
