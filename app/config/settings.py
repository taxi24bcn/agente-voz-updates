"""Configuracion global del asistente de voz Taxi24H."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# ── Resolucion de rutas ─────────────────────────────────────────────────────

def _resolve_base_dir() -> Path:
    """Raiz de los binarios: carpeta del .exe (frozen) o raiz del proyecto (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def _resolve_user_data_dir() -> Path:
    """Directorio de datos persistentes del usuario. Siempre escribible.

    Frozen  → %LOCALAPPDATA%\\Taxi24H\\AgenteVoz
    Dev     → raiz del proyecto  (comportamiento original, .env junto al codigo)
    """
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(local_app_data) / "Taxi24H" / "AgenteVoz"
    return Path(__file__).resolve().parents[2]


# Rutas exportadas — usadas en toda la app
BASE_DIR      = _resolve_base_dir()
USER_DATA_DIR = _resolve_user_data_dir()
CONFIG_DIR    = USER_DATA_DIR / "config"
LOGS_DIR      = USER_DATA_DIR / "logs"
SESSIONS_DIR  = LOGS_DIR / "sessions"
DOWNLOADS_DIR = USER_DATA_DIR / "downloads"

# Garantiza que todos los directorios del usuario existen antes de que arranque la app
for _d in (CONFIG_DIR, SESSIONS_DIR, DOWNLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Carga de .env ────────────────────────────────────────────────────────────
# Orden de busqueda:
#  1. CONFIG_DIR/.env  (ubicacion oficial: %LOCALAPPDATA%\Taxi24H\AgenteVoz\config\.env)
#  2. BASE_DIR/.env    (junto al .exe — fallback para instalaciones antiguas / desarrollo)

_env_primary  = CONFIG_DIR / ".env"
_env_fallback = BASE_DIR / ".env"

if _env_primary.exists():
    load_dotenv(_env_primary)
elif _env_fallback.exists():
    load_dotenv(_env_fallback)
else:
    load_dotenv()  # dotenv busca en CWD (util en tests o entornos CI)


# ── Constantes de audio ──────────────────────────────────────────────────────
SAMPLE_RATE = 16_000
CHANNELS    = 1
DTYPE       = "int16"
FRAME_MS    = 20
BLOCKSIZE   = int(SAMPLE_RATE * FRAME_MS / 1000)  # 320 samples = 20 ms @ 16 kHz

# STT chunking
SILENCE_DBFS_THRESHOLD  = -40.0
SILENCE_FRAMES_TO_FLUSH = 30    # 30 * 20 ms = 600 ms de silencio → flush
MIN_FLUSH_SECONDS       = 1.0
MAX_BUFFER_SECONDS      = 15.0

# ── Modelos ──────────────────────────────────────────────────────────────────
STT_MODEL    = "gpt-4o-transcribe"
LLM_MODEL    = "gpt-4o-mini"
LLM_LANGUAGE = "es"

# ── Extraccion (debounce) ────────────────────────────────────────────────────
EXTRACTION_MIN_INTERVAL_S       = 2.0
EXTRACTION_MIN_NEW_TOKENS       = 20
EXTRACTION_FIRST_RUN_MIN_TOKENS = 10
EXTRACTION_LONG_ELAPSED_S       = 8.0

# ── Google Maps Geocoding ────────────────────────────────────────────────────
# Feature desactivada si GOOGLE_MAPS_API_KEY esta vacia
GOOGLE_MAPS_GEOCODE_URL      = "https://maps.googleapis.com/maps/api/geocode/json"
PICKUP_GEOCODE_STABLE_SECONDS = 2.0


def has_required_config() -> bool:
    """True si ya existe un .env con OPENAI_API_KEY rellena."""
    env_path = CONFIG_DIR / ".env"
    if not env_path.exists():
        return False
    # Verificar que la clave realmente tiene valor (no solo que el archivo existe)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        return True
    # El archivo existe pero la variable aun no esta cargada — leer directamente
    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("OPENAI_API_KEY="):
                value = line.split("=", 1)[1].strip()
                return bool(value)
    except OSError:
        pass
    return False


def save_env_config(
    openai_api_key: str,
    cable_hint: str = "cable output",
    operator_mic_hint: str = "",
    google_maps_api_key: str = "",
) -> None:
    """Escribe o sobreescribe el .env en CONFIG_DIR con los valores dados."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    content = (
        f"OPENAI_API_KEY={openai_api_key.strip()}\n"
        f"GOOGLE_MAPS_API_KEY={google_maps_api_key.strip()}\n"
        f"CABLE_HINT={cable_hint.strip()}\n"
        f"OPERATOR_MIC_HINT={operator_mic_hint.strip()}\n"
    )
    (CONFIG_DIR / ".env").write_text(content, encoding="utf-8")


def reload_env_file() -> None:
    """Recarga el .env desde disco para que os.getenv refleje los nuevos valores."""
    from dotenv import load_dotenv as _load_dotenv
    env_path = CONFIG_DIR / ".env"
    if env_path.exists():
        _load_dotenv(env_path, override=True)


@dataclass
class Settings:
    openai_api_key:     str
    cable_hint:         str
    operator_mic_hint:  str
    google_maps_api_key: str = ""  # vacio → normalizacion RECOGIDA desactivada

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "Falta OPENAI_API_KEY.\n\n"
                f"Crea o edita el archivo:\n  {_env_primary}\n\n"
                "Copia .env.example como plantilla y rellena tu clave de OpenAI."
            )
        return cls(
            openai_api_key=key,
            cable_hint=os.getenv("CABLE_HINT", "cable output").strip(),
            operator_mic_hint=os.getenv("OPERATOR_MIC_HINT", "").strip(),
            google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY", "").strip(),
        )
