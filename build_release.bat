@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

:: ============================================================
:: BUILD RELEASE — Agente Voz Taxi24H
::
:: Uso:
::   build_release.bat              (lee la version de version.txt)
::   build_release.bat 2.2.0        (fuerza una version concreta)
::
:: Requisitos:
::   - .venv activado con PyInstaller instalado
::   - Inno Setup 6 instalado (para el instalador .exe)
:: ============================================================

:: --- Version ---
if "%~1"=="" (
    set /p VERSION=<version.txt
    set VERSION=!VERSION: =!
) else (
    set VERSION=%~1
    echo !VERSION!>version.txt
)

echo.
echo ============================================================
echo   BUILD RELEASE  v!VERSION!
echo ============================================================
echo.

:: --- 1. Verificar entorno Python ---
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo [ERROR] PySide6 no encontrado.
    echo         Activa el entorno virtual primero:
    echo           .venv\Scripts\activate
    exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller no encontrado. Instalar con:
    echo           pip install pyinstaller
    exit /b 1
)

:: --- 2. Limpiar builds anteriores ---
echo [1/5] Limpiando builds anteriores...
if exist "dist\AgenteVozTaxi24H" (
    rmdir /s /q "dist\AgenteVozTaxi24H"
)
if exist "build" (
    rmdir /s /q "build"
)
echo       OK

:: --- 3. PyInstaller ---
echo [2/5] Ejecutando PyInstaller (esto puede tardar 1-3 minutos)...
python -m PyInstaller agente_voz.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller fallo. Revisa los mensajes anteriores.
    exit /b 1
)
echo       OK

:: --- 4. Verificar el ejecutable ---
echo [3/5] Verificando ejecutable...
if not exist "dist\AgenteVozTaxi24H\AgenteVozTaxi24H.exe" (
    echo [ERROR] No se genero el ejecutable. Algo fallo en PyInstaller.
    exit /b 1
)
echo       OK — dist\AgenteVozTaxi24H\AgenteVozTaxi24H.exe

:: --- 5. Crear carpeta del instalador ---
if not exist "dist\installer" mkdir "dist\installer"

:: --- 6. Inno Setup ---
echo [4/5] Generando instalador con Inno Setup...
set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)
if not exist %ISCC% (
    set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
)
if not exist %ISCC% (
    echo [AVISO] Inno Setup 6 no encontrado.
    echo         Descargalo en: https://jrsoftware.org/isinfo.php
    echo         O ajusta la ruta ISCC en este script.
    echo         Continuando sin instalador...
    goto :skip_installer
)

%ISCC% "installer\setup.iss" /DMyAppVersion=!VERSION!
if errorlevel 1 (
    echo [ERROR] Inno Setup fallo. Revisa installer\setup.iss.
    exit /b 1
)

:skip_installer

:: --- 7. Resumen ---
echo [5/5] Build completado.
echo.
echo ============================================================
echo   RESULTADO FINAL
echo ============================================================
echo.
echo   Version:      v!VERSION!
echo   Ejecutable:   dist\AgenteVozTaxi24H\AgenteVozTaxi24H.exe
if exist "dist\installer\AgenteVozTaxi24H-!VERSION!-Setup.exe" (
    echo   Instalador:   dist\installer\AgenteVozTaxi24H-!VERSION!-Setup.exe
) else (
    echo   Instalador:   NO generado ^(falta Inno Setup^)
)
echo.
echo   Siguiente paso: probar el instalador en un PC limpio.
echo ============================================================
echo.

endlocal
