"""Exporta sesion completa (datos + transcripcion) a logs/sessions/<ts>.txt."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config.settings import SESSIONS_DIR
from app.output.clipboard import format_service_text
from app.parser.service_extractor import ServiceData


def _geo_metrics_block(data: ServiceData) -> str:
    """Genera el bloque de métricas de geocoding para el TXT exportado.

    Solo se incluye si la normalización se ejecutó (status != 'skipped').
    Los campos con valor None se omiten para no añadir ruido.
    """
    status = getattr(data, "_recogida_status", "skipped")
    if status == "skipped":
        return ""

    lines = ["", "# --- MÉTRICAS GEOCODING RECOGIDA ---"]

    raw = getattr(data, "_recogida_raw", None)
    if raw:
        lines.append(f"# geo_recogida_raw: {raw}")

    lines.append(f"# geo_recogida_final: {data.recogida}")
    lines.append(f"# geo_recogida_status: {status}")

    muni = getattr(data, "_recogida_municipio", None)
    if muni:
        lines.append(f"# geo_recogida_municipio: {muni}")

    latlon = getattr(data, "_recogida_latlon", None)
    if latlon:
        lines.append(f"# geo_recogida_coord: {latlon[0]:.6f}, {latlon[1]:.6f}")

    place_id = getattr(data, "_recogida_place_id", None)
    if place_id:
        lines.append(f"# geo_recogida_place_id: {place_id}")

    lines.append(f"# geo_google_called: {str(getattr(data, '_geo_google_called', False)).lower()}")
    lines.append(f"# geo_google_retry_called: {str(getattr(data, '_geo_retry_called', False)).lower()}")
    lines.append(f"# geo_cache_hit: {str(getattr(data, '_geo_cache_hit', False)).lower()}")
    lines.append(f"# geo_operator_edited_pickup: {str(getattr(data, '_geo_operator_edited_pickup', False)).lower()}")

    return "\n".join(lines) + "\n"


def save_session(transcript: str, service_data: ServiceData, session_id: str | None = None) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{session_id}.txt" if session_id else f"session_{ts}.txt"
    path = SESSIONS_DIR / filename
    sep = "=" * 60
    geo_block = _geo_metrics_block(service_data)
    label = session_id or ts
    content = (
        f"{sep}\nSESION TAXI24H - {label}\n{sep}\n\n"
        "DATOS DEL SERVICIO:\n"
        f"{format_service_text(service_data)}\n"
        f"{geo_block}\n"
        f"{sep}\nTRANSCRIPCION COMPLETA:\n{sep}\n\n"
        f"{transcript}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path
