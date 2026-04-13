"""Evalúa grabaciones MP3 históricas contra el pipeline completo de extracción.

Uso:
    python scripts/eval_recordings.py [--folder RUTA] [--limit N] [--no-geo]

Opciones:
    --folder   Carpeta con los MP3 (default: C:/Users/mari_/Downloads/llamadas)
    --limit    Procesar solo los primeros N archivos (default: todos)
    --no-geo   Desactivar normalización Google Maps (más rápido, sin coste)
    --cdr      Ruta al CSV del CDR para cruzar datos (default: informe_cdr.csv)

Salida:
    logs/eval/eval_<timestamp>.csv  — resultados de extracción
    logs/eval/eval_<timestamp>.txt  — resumen estadístico
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Añadir raíz del proyecto al path para los imports de app.*
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI

from app.config.settings import Settings
from app.geo.address_normalizer import AddressNormalizer
from app.geo.maps_client import MapsClient
from app.parser.service_extractor import ServiceData, ServiceExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("eval")

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_FOLDER = Path(os.environ.get("EVAL_FOLDER", str(Path.home() / "Downloads" / "llamadas")))
EVAL_DIR = PROJECT_ROOT / "logs" / "eval"
CDR_DEFAULT = PROJECT_ROOT / "informe_cdr.csv"

STT_MODEL = "gpt-4o-transcribe"
STT_LANGUAGE = "es"

# Columnas del CSV de salida
CSV_COLUMNS = [
    "archivo",
    "clid",
    "fecha_hora",
    "duracion_s",
    "transcript_chars",
    "cliente",
    "telefono",
    "recogida",
    "destino",
    "fecha",
    "hora",
    "tipo_servicio",
    "observaciones",
    "geo_status",
    "geo_recogida_raw",
    "geo_recogida_final",
    "geo_municipio",
    "geo_google_called",
    "geo_retry_called",
    "geo_cache_hit",
    "transcript",
    "error",
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_filename(filename: str) -> dict:
    """Extrae CLID y timestamp del nombre del archivo.

    Formato: bhappysip_<CLID>_<YYYY-MM-DD>_<HH>_<MM>_<SS>.mp3
    """
    stem = Path(filename).stem  # bhappysip_639701397_2026-04-11_15_11_57
    parts = stem.split("_")
    result = {"clid": "", "fecha_hora": ""}
    if len(parts) >= 6:
        result["clid"] = parts[1]
        try:
            date_part = parts[2]      # 2026-04-11
            hh = parts[3]
            mm = parts[4]
            ss = parts[5]
            result["fecha_hora"] = f"{date_part} {hh}:{mm}:{ss}"
        except IndexError:
            pass
    return result


def load_cdr(cdr_path: Path) -> dict[str, dict]:
    """Carga el CDR como dict indexado por CLID."""
    if not cdr_path.exists():
        log.warning("CDR no encontrado en %s — se omitirá el cruce", cdr_path)
        return {}
    cdr: dict[str, dict] = {}
    with cdr_path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=",")
        for row in reader:
            # Los campos del CDR pueden variar; intentamos las claves más comunes
            clid = (row.get("Clid") or row.get("CLID") or row.get("clid") or "").strip()
            duracion = (row.get("Duración") or row.get("Duracion") or row.get("duracion") or "").strip()
            estado = (row.get("Estado") or row.get("estado") or "").strip()
            if clid:
                cdr[clid] = {"duracion": duracion, "estado": estado}
    log.info("CDR cargado: %d registros", len(cdr))
    return cdr


def transcribe_mp3(client: OpenAI, mp3_path: Path) -> str:
    """Transcribe un MP3 completo con gpt-4o-transcribe."""
    with mp3_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(
            model=STT_MODEL,
            file=audio_file,
            language=STT_LANGUAGE,
            response_format="text",
        )
    # response puede ser str o un objeto con .text
    if isinstance(response, str):
        return response
    return getattr(response, "text", str(response))


def extract_from_transcript(
    extractor: ServiceExtractor,
    transcript: str,
) -> ServiceData:
    """Extrae datos de servicio desde la transcripción completa."""
    # Para evaluación batch usamos extract() directamente con locked_fields vacío.
    # Forzamos should_extract a True reseteando el estado interno.
    extractor._last_run = 0.0
    extractor._last_word_count = 0

    return extractor.extract(
        transcript=transcript,
        current_data=ServiceData.empty(),
        locked_fields=[],
    )


def normalize_pickup_batch(
    normalizer: AddressNormalizer,
    data: ServiceData,
    transcript: str,
) -> ServiceData:
    """Normaliza la RECOGIDA sin tracker de estabilidad (batch mode)."""
    return normalizer.normalize_pickup_now(
        data,
        transcript=transcript,
        current_data=data,
        locked_fields=set(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa MP3 históricos")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--limit", type=int, default=0, help="0 = todos")
    parser.add_argument("--no-geo", action="store_true", help="Desactivar normalización Google Maps")
    parser.add_argument("--cdr", type=Path, default=CDR_DEFAULT)
    args = parser.parse_args()

    # Cargar settings desde .env
    settings = Settings.from_env()

    if not settings.openai_api_key:
        log.error("OPENAI_API_KEY no configurada en .env")
        sys.exit(1)

    # Preparar clientes
    openai_client = OpenAI(api_key=settings.openai_api_key)
    extractor = ServiceExtractor(settings.openai_api_key)

    normalizer: AddressNormalizer | None = None
    if not args.no_geo and settings.google_maps_api_key:
        maps_client = MapsClient(settings.google_maps_api_key)
        normalizer = AddressNormalizer(maps_client)
        log.info("Normalización Google Maps: ACTIVADA")
    else:
        log.info("Normalización Google Maps: desactivada")

    # Cargar CDR
    cdr = load_cdr(args.cdr)

    # Listar MP3
    folder: Path = args.folder
    if not folder.exists():
        log.error("Carpeta no encontrada: %s", folder)
        sys.exit(1)

    mp3_files = sorted(folder.glob("*.mp3"))
    if not mp3_files:
        log.error("No se encontraron MP3 en %s", folder)
        sys.exit(1)

    if args.limit > 0:
        mp3_files = mp3_files[: args.limit]

    log.info("Archivos a procesar: %d", len(mp3_files))

    # Preparar salida
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR = EVAL_DIR / "transcripts"
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = EVAL_DIR / f"eval_{ts}.csv"
    txt_path = EVAL_DIR / f"eval_{ts}.txt"

    results: list[dict] = []
    stats = {
        "total": len(mp3_files),
        "ok": 0,
        "error": 0,
        "geo_validated": 0,
        "geo_partial": 0,
        "geo_usable_review": 0,
        "geo_outside_amb": 0,
        "geo_no_result": 0,
        "geo_skipped": 0,
        "geo_unknown_incomplete": 0,
        "pending_recogida": 0,
    }

    # ── Procesar archivos ──────────────────────────────────────────────────────
    for i, mp3_path in enumerate(mp3_files, 1):
        meta = parse_filename(mp3_path.name)
        clid = meta["clid"]
        cdr_row = cdr.get(clid, {})

        log.info("[%d/%d] %s (CLID=%s)", i, len(mp3_files), mp3_path.name, clid)

        row: dict = {
            "archivo": mp3_path.name,
            "clid": clid,
            "fecha_hora": meta["fecha_hora"],
            "duracion_s": cdr_row.get("duracion", ""),
            "transcript_chars": "",
            "cliente": "",
            "telefono": "",
            "recogida": "",
            "destino": "",
            "fecha": "",
            "hora": "",
            "tipo_servicio": "",
            "observaciones": "",
            "geo_status": "",
            "geo_recogida_raw": "",
            "geo_recogida_final": "",
            "geo_municipio": "",
            "geo_google_called": "",
            "geo_retry_called": "",
            "geo_cache_hit": "",
            "transcript": "",
            "error": "",
        }

        try:
            # 1. Transcribir
            t0 = time.time()
            transcript = transcribe_mp3(openai_client, mp3_path)
            transcript = "".join(ch for ch in transcript if ch.isprintable())
            t_stt = time.time() - t0
            log.info("  STT: %.1fs — %d chars", t_stt, len(transcript))
            row["transcript_chars"] = len(transcript)

            # Guardar transcripción completa en archivo individual
            transcript_file = TRANSCRIPTS_DIR / f"{mp3_path.stem}.txt"
            transcript_file.write_text(transcript, encoding="utf-8")
            row["transcript"] = transcript

            if not transcript.strip():
                row["error"] = "transcripcion_vacia"
                stats["error"] += 1
                results.append(row)
                continue

            # 2. Extraer campos
            t0 = time.time()
            data = extract_from_transcript(extractor, transcript)
            t_ext = time.time() - t0
            log.info("  Extracción: %.1fs — recogida=%r", t_ext, data.recogida)

            row["cliente"] = data.cliente
            row["telefono"] = data.telefono
            row["recogida"] = data.recogida
            row["destino"] = data.destino
            row["fecha"] = data.fecha
            row["hora"] = data.hora
            row["tipo_servicio"] = data.tipo_servicio
            row["observaciones"] = data.observaciones

            # 3. Normalización Google Maps (si activa)
            if normalizer is not None and data.recogida not in ("PENDIENTE", ""):
                t0 = time.time()
                data = normalize_pickup_batch(normalizer, data, transcript)
                t_geo = time.time() - t0
                log.info(
                    "  Geo: %.1fs — status=%s final=%r",
                    t_geo,
                    getattr(data, "_recogida_status", "?"),
                    data.recogida,
                )
                row["recogida"] = data.recogida  # actualizar con valor normalizado

            row["geo_status"] = getattr(data, "_recogida_status", "")
            row["geo_recogida_raw"] = getattr(data, "_recogida_raw", "")
            row["geo_recogida_final"] = data.recogida
            row["geo_municipio"] = getattr(data, "_recogida_municipio", "") or ""
            row["geo_google_called"] = str(getattr(data, "_geo_google_called", False)).lower()
            row["geo_retry_called"] = str(getattr(data, "_geo_retry_called", False)).lower()
            row["geo_cache_hit"] = str(getattr(data, "_geo_cache_hit", False)).lower()

            # Estadísticas
            stats["ok"] += 1
            geo_status = row["geo_status"]
            if geo_status == "validated":
                stats["geo_validated"] += 1
            elif geo_status == "partial_match":
                stats["geo_partial"] += 1
            elif geo_status == "usable_review":
                stats["geo_usable_review"] += 1
            elif geo_status == "outside_amb":
                stats["geo_outside_amb"] += 1
            elif geo_status == "no_result":
                stats["geo_no_result"] += 1
            elif geo_status == "unknown_or_incomplete":
                stats["geo_unknown_incomplete"] += 1
            else:
                stats["geo_skipped"] += 1

            if data.recogida == "PENDIENTE":
                stats["pending_recogida"] += 1

        except Exception as exc:
            log.exception("  ERROR procesando %s: %s", mp3_path.name, exc)
            row["error"] = str(exc)[:200]
            stats["error"] += 1

        results.append(row)

        # Pequeña pausa para no saturar la API
        if i < len(mp3_files):
            time.sleep(1)

    # ── Escribir CSV ──────────────────────────────────────────────────────────
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(results)

    log.info("CSV guardado: %s", csv_path)

    # ── Escribir resumen TXT ──────────────────────────────────────────────────
    sep = "=" * 60
    lines = [
        sep,
        f"EVALUACIÓN TAXI24H — {ts}",
        sep,
        f"Carpeta MP3: {args.folder}",
        f"Archivos procesados: {stats['total']}",
        f"  OK:     {stats['ok']}",
        f"  Error:  {stats['error']}",
        "",
        "EXTRACCIÓN:",
        f"  RECOGIDA=PENDIENTE: {stats['pending_recogida']} / {stats['ok']}",
        "",
    ]

    if normalizer is not None:
        ok = stats["ok"]
        lines += [
            "GEOCODING (sobre llamadas OK):",
            f"  validated:         {stats['geo_validated']} ({_pct(stats['geo_validated'], ok)})",
            f"  usable_review:     {stats['geo_usable_review']} ({_pct(stats['geo_usable_review'], ok)})",
            f"  partial_match:     {stats['geo_partial']} ({_pct(stats['geo_partial'], ok)})",
            f"  outside_amb:       {stats['geo_outside_amb']} ({_pct(stats['geo_outside_amb'], ok)})",
            f"  no_result:         {stats['geo_no_result']} ({_pct(stats['geo_no_result'], ok)})",
            f"  unknown_incomplete:{stats['geo_unknown_incomplete']} ({_pct(stats['geo_unknown_incomplete'], ok)})",
            f"  skipped:           {stats['geo_skipped']} ({_pct(stats['geo_skipped'], ok)})",
            "",
        ]

    lines += [
        "DETALLE POR LLAMADA:",
        sep,
    ]
    for r in results:
        transcript_text = r.get("transcript", "")
        lines.append(
            f"[{r['archivo']}]\n"
            f"  CLID={r['clid']}  Duracion={r['duracion_s']}  Chars={r['transcript_chars']}\n"
            f"  Cliente:    {r['cliente']}\n"
            f"  Recogida:   {r['recogida']}\n"
            f"  Destino:    {r['destino']}\n"
            f"  Fecha/Hora: {r['fecha']} {r['hora']}\n"
            f"  Tipo:       {r['tipo_servicio']}\n"
            f"  Observ.:    {r['observaciones']}\n"
            f"  GeoStatus:  {r['geo_status']}  Municipio: {r['geo_municipio']}\n"
            f"  GeoRaw:     {r['geo_recogida_raw']}\n"
            + (f"  ERROR: {r['error']}\n" if r["error"] else "")
            + f"\n  TRANSCRIPCION COMPLETA:\n  {'-'*50}\n"
            + "\n".join(f"  {line}" for line in transcript_text.splitlines())
            + f"\n  {'-'*50}\n"
        )

    summary = "\n".join(lines)
    txt_path.write_text(summary, encoding="utf-8")
    log.info("Resumen guardado: %s", txt_path)

    # Imprimir resumen en consola
    print("\n" + summary)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100*n/total:.1f}%"


if __name__ == "__main__":
    main()
