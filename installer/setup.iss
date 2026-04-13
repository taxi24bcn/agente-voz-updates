; ============================================================
; Inno Setup — Instalador Agente Voz Taxi24H
;
; Para compilar:
;   ISCC.exe installer\setup.iss /DMyAppVersion=2.1.3
;
; O usar build_release.bat (proceso completo automatico).
;
; La configuracion de claves API se hace dentro de la propia app
; al primer arranque (ConfigDialog). El instalador NO pregunta claves.
;
; Rutas de datos del usuario (NO se tocan al actualizar):
;   %LOCALAPPDATA%\Taxi24H\AgenteVoz\config\.env
;   %LOCALAPPDATA%\Taxi24H\AgenteVoz\logs\
;   %LOCALAPPDATA%\Taxi24H\AgenteVoz\logs\sessions\
;   %LOCALAPPDATA%\Taxi24H\AgenteVoz\downloads\
; ============================================================

#ifndef MyAppVersion
  #define MyAppVersion "2.1.3"
#endif

#define MyAppName      "Agente Voz Taxi24H"
#define MyAppPublisher "Taxi24H"
#define MyAppURL       "https://www.taxi24bcn.com"
#define MyAppExeName   "AgenteVozTaxi24H.exe"
#define MyAppBinDir    "Taxi24H\AgenteVoz"
#define MyAppDataDir   "Taxi24H\AgenteVoz"

[Setup]
AppId={{F3A8B2C1-D4E5-4F60-9A1B-C2D3E4F5A6B7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppBinDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=AgenteVozTaxi24H-{#MyAppVersion}-Setup
; SetupIconFile=icon.ico  ; Descomentar solo cuando exista installer\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
CloseApplications=yes
RestartIfNeededByRun=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: checkedonce
; userstartup (no commonstartup): el inicio automatico es por usuario,
; coherente con que la config (.env) tambien es por usuario.
Name: "startupicon"; Description: "Iniciar automaticamente con Windows"; GroupDescription: "Inicio con Windows:"; Flags: unchecked

; ── Directorios de datos del usuario ────────────────────────────────────────
; Estructura coherente con settings.py:
;   LOGS_DIR      = USER_DATA_DIR / "logs"
;   SESSIONS_DIR  = LOGS_DIR / "sessions"   ← dentro de logs, no en la raiz
;   DOWNLOADS_DIR = USER_DATA_DIR / "downloads"
[Dirs]
Name: "{localappdata}\{#MyAppDataDir}\config";         Permissions: users-modify
Name: "{localappdata}\{#MyAppDataDir}\logs";           Permissions: users-modify
Name: "{localappdata}\{#MyAppDataDir}\logs\sessions";  Permissions: users-modify
Name: "{localappdata}\{#MyAppDataDir}\downloads";      Permissions: users-modify

; ── Archivos ─────────────────────────────────────────────────────────────────
[Files]
; Binarios generados por PyInstaller
Source: "..\dist\AgenteVozTaxi24H\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Plantilla de referencia — util si el usuario necesita recrear el .env a mano
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion

; ── Accesos directos ─────────────────────────────────────────────────────────
[Icons]
Name: "{group}\{#MyAppName}";             Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; userstartup: inicio automatico solo para el usuario que instala (coherente con config por usuario)
Name: "{userstartup}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

; ── Lanzar al terminar ───────────────────────────────────────────────────────
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName} ahora"; Flags: nowait postinstall skipifsilent

; ── Desinstalacion ───────────────────────────────────────────────────────────
[UninstallDelete]
; Los datos del usuario (.env, logs, sesiones) se conservan al desinstalar.
; Para borrarlos completamente descomenta la linea siguiente:
; Type: filesandordirs; Name: "{localappdata}\{#MyAppDataDir}"
