"""Construccion del JSON de sesion (schema_version 2) con trazabilidad geo completa."""
from __future__ import annotations

import platform
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.geo.address_normalizer import _NormalizeResult
    from app.geo.maps_client import GeoResult
    from app.parser.service_extractor import ServiceData

# Campos que se comparan para detectar ediciones manuales del operador
_EDITABLE_FIELDS = ("cliente", "telefono", "recogida", "destino",
                    "fecha", "hora", "tipo_servicio", "observaciones")

# Catálogo cerrado de PickupStatus (coincide con PickupStatus.value)
_NEEDS_GEO_REVIEW_STATUSES = {
    "partial_match", "usable_review", "outside_amb", "no_result",
}


def generate_session_id() -> str:
    """Genera un session_id único con formato YYYYMMDD_HHMMSS_<pc>_<uuid8>."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pc = _sanitize_pc_name(socket.gethostname())
    uid = uuid.uuid4().hex[:8]
    return f"{ts}_{pc}_{uid}"


def _sanitize_pc_name(name: str) -> str:
    """Convierte nombre de PC a formato seguro para nombres de archivo."""
    name = re.sub(r"[^A-Za-z0-9_-]", "", name.replace(" ", "_"))
    return name[:20] or "PC"


def mask_phone(phone: str) -> str:
    """Enmascara el teléfono: conserva primeros 3 y últimos 2 dígitos."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) >= 6:
        return digits[:3] + "*" * (len(digits) - 5) + digits[-2:]
    return "***"


def compute_manual_edits(
    extracted: "ServiceData",
    final: "ServiceData",
) -> dict[str, bool]:
    """Detecta qué campos fueron editados manualmente por el operador."""
    edits: dict[str, bool] = {}
    for key in _EDITABLE_FIELDS:
        v_extracted = (getattr(extracted, key, None) or "").strip()
        v_final = (getattr(final, key, None) or "").strip()
        edits[key] = v_extracted != v_final
    return edits


def _build_geo_block(data: "ServiceData") -> dict[str, Any]:
    """Construye el bloque geo compacto (resumen de estado)."""
    status = getattr(data, "_recogida_status", "skipped")
    latlon = getattr(data, "_recogida_latlon", None)
    return {
        "recogida_raw": getattr(data, "_recogida_raw", None),
        "recogida_final": data.recogida,
        "status": status,
        "municipio": getattr(data, "_recogida_municipio", None),
        "latlon": list(latlon) if latlon else None,
        "place_id": getattr(data, "_recogida_place_id", None),
        "google_called": bool(getattr(data, "_geo_google_called", False)),
        "retry_called": bool(getattr(data, "_geo_retry_called", False)),
        "cache_hit": bool(getattr(data, "_geo_cache_hit", False)),
        "operator_edited": bool(getattr(data, "_geo_operator_edited_pickup", False)),
    }


def _build_geo_diagnostics(
    data: "ServiceData",
    norm_result: Optional["_NormalizeResult"],
) -> dict[str, Any]:
    """Construye geo_diagnostics completo desde _NormalizeResult."""
    if norm_result is None:
        return {
            "pickup_original_user_text": getattr(data, "_recogida_raw", None),
            "pickup_extracted_text": getattr(data, "_recogida_raw", None),
            "pickup_preprocessed_text": None,
            "pickup_repaired_text": None,
            "pickup_query_sent_to_google": None,
            "pickup_query_retry": None,
            "stage_before_google": None,
            "final_pickup_status": getattr(data, "_recogida_status", "skipped"),
            "decision_reason": None,
            "accepted_candidate_index": None,
            "accepted_place_id": None,
            "accepted_formatted_address": None,
            "google_result_count": 0,
            "cache_hit": bool(getattr(data, "_geo_cache_hit", False)),
            "operator_locked": bool(getattr(data, "_geo_operator_edited_pickup", False)),
            "was_retry_used": False,
        }

    return {
        "pickup_original_user_text": getattr(data, "_recogida_raw", None),
        "pickup_extracted_text": getattr(data, "_recogida_raw", None),
        "pickup_preprocessed_text": norm_result.pickup_preprocessed_text,
        "pickup_repaired_text": norm_result.pickup_repaired_text,
        "pickup_query_sent_to_google": norm_result.pickup_query_primary,
        "pickup_query_retry": norm_result.pickup_query_retry,
        "stage_before_google": norm_result.stage_before_google,
        "final_pickup_status": norm_result.status.value,
        "decision_reason": norm_result.decision_reason,
        "accepted_candidate_index": norm_result.accepted_candidate_index,
        "accepted_place_id": norm_result.accepted_place_id,
        "accepted_formatted_address": norm_result.accepted_formatted_address,
        "google_result_count": norm_result.google_result_count,
        "cache_hit": norm_result.cache_hit,
        "operator_locked": bool(getattr(data, "_geo_operator_edited_pickup", False)),
        "was_retry_used": norm_result.was_retry_used,
    }


def _build_google_candidates(
    norm_result: Optional["_NormalizeResult"],
) -> list[dict[str, Any]]:
    """Construye la lista de candidatos Google (máx top 3, con candidato aceptado garantizado)."""
    if norm_result is None:
        return []

    raw_candidates: list["GeoResult"] = getattr(norm_result, "_raw_candidates", []) or []
    rejection_by_idx: dict = getattr(norm_result, "_candidate_rejection_reasons", {}) or {}
    accepted_result: Optional["GeoResult"] = getattr(norm_result, "_accepted_result", None)
    accepted_idx = norm_result.accepted_candidate_index

    from app.geo.amb_municipalities import is_amb_municipality

    built: list[dict[str, Any]] = []
    for idx, cand in enumerate(raw_candidates[:3]):
        is_accepted = (idx == accepted_idx)
        built.append({
            "index": idx,
            "formatted_address": cand.formatted_address,
            "place_id": cand.place_id,
            "partial_match": cand.partial_match,
            "types": [],  # GeoResult no guarda types — campo informativo omitido
            "location": {"lat": cand.lat, "lng": cand.lon},
            "inside_amb": is_amb_municipality(cand.municipality or ""),
            "accepted": is_accepted,
            "rejection_reason": None if is_accepted else rejection_by_idx.get(idx),
        })

    # Garantía: si el candidato aceptado no está en los top 3, incluirlo
    if accepted_result is not None and accepted_idx is not None and accepted_idx >= 3:
        built.append({
            "index": accepted_idx,
            "formatted_address": accepted_result.formatted_address,
            "place_id": accepted_result.place_id,
            "partial_match": accepted_result.partial_match,
            "types": [],
            "location": {"lat": accepted_result.lat, "lng": accepted_result.lon},
            "inside_amb": is_amb_municipality(accepted_result.municipality or ""),
            "accepted": True,
            "rejection_reason": None,
        })

    return built


def _build_geo_trace(
    data: "ServiceData",
    norm_result: Optional["_NormalizeResult"],
) -> list[dict[str, Any]]:
    """Construye geo_trace: secuencia de pasos del pipeline."""
    trace: list[dict[str, Any]] = []
    raw = getattr(data, "_recogida_raw", None) or ""

    trace.append({"step": "input_original", "value": raw})
    trace.append({"step": "extracted_pickup", "value": raw})

    if norm_result is None:
        return trace

    preprocessed = norm_result.pickup_preprocessed_text
    repaired = norm_result.pickup_repaired_text

    if repaired and repaired != raw:
        trace.append({"step": "repair_pickup", "value": repaired, "reason": "repair_applied"})

    if preprocessed and preprocessed != (repaired or raw):
        trace.append({"step": "preprocess_pickup", "value": preprocessed, "reason": "query_preprocessed"})

    if norm_result.pickup_query_primary:
        trace.append({"step": "google_query_primary", "value": norm_result.pickup_query_primary})

    if norm_result.google_result_count > 0:
        trace.append({"step": "google_candidates_received", "value": norm_result.google_result_count})

    # Rechazos de candidatos
    rejection_by_idx: dict = getattr(norm_result, "_candidate_rejection_reasons", {}) or {}
    raw_candidates: list = getattr(norm_result, "_raw_candidates", []) or []
    for idx, reason in rejection_by_idx.items():
        if idx < len(raw_candidates):
            cand_addr = raw_candidates[idx].formatted_address
            step: dict[str, Any] = {"step": "candidate_rejected", "value": cand_addr}
            if reason:
                step["reason"] = reason
            trace.append(step)

    # Candidato aceptado
    if norm_result.accepted_formatted_address is not None:
        trace.append({
            "step": "candidate_accepted",
            "value": norm_result.accepted_formatted_address,
            "reason": "accepted_high_confidence",
        })

    if norm_result.was_retry_used and norm_result.pickup_query_retry:
        trace.append({"step": "google_query_retry", "value": norm_result.pickup_query_retry, "reason": "query_retry_used"})

    # Estado final
    final_status = norm_result.status.value
    step_final: dict[str, Any] = {"step": "final_status_assigned", "value": final_status}
    if norm_result.decision_reason:
        step_final["reason"] = norm_result.decision_reason
    trace.append(step_final)

    return trace


def compute_geo_review(data: "ServiceData") -> tuple[bool, list[str]]:
    """Determina si la sesion necesita revisión geo y los motivos."""
    reasons: list[str] = []
    status = getattr(data, "_recogida_status", "skipped")

    recogida_val = (data.recogida or "").upper()
    if "(REVISAR)" in recogida_val:
        reasons.append("recogida_revisar")

    if status in _NEEDS_GEO_REVIEW_STATUSES:
        reasons.append(f"geo_{status}")

    norm_result = getattr(data, "_geo_norm_result", None)
    if norm_result is not None and norm_result.decision_reason:
        dr = norm_result.decision_reason
        if dr not in ("accepted_high_confidence",) and dr not in reasons:
            reasons.append(dr)

    return bool(reasons), reasons


def compute_quality_review(data: "ServiceData") -> tuple[bool, list[str]]:
    """Detecta problemas de calidad en los datos extraídos."""
    reasons: list[str] = []

    required = ("cliente", "recogida", "destino", "fecha", "hora")
    for field in required:
        val = (getattr(data, field, None) or "").strip().upper()
        if not val or val == "PENDIENTE":
            reasons.append(f"missing_{field}")

    return bool(reasons), reasons


def _app_version() -> str:
    try:
        from app.config.settings import BASE_DIR
        vf = BASE_DIR / "version.txt"
        if vf.exists():
            return vf.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "unknown"


def build_session_json(
    session_id: str,
    transcript: str,
    extracted_data: "ServiceData",
    final_data: "ServiceData",
    upload_status: str = "pending",
) -> dict[str, Any]:
    """Construye el dict completo de sesion (schema_version 2).

    Args:
        session_id: ID único de sesion (generado con generate_session_id())
        transcript: texto de transcripcion completo
        extracted_data: snapshot de ServiceData al finalizar la extracción
        final_data: ServiceData con los valores finales de la UI
        upload_status: estado inicial de subida (default "pending")
    """
    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    norm_result = getattr(final_data, "_geo_norm_result", None)
    needs_geo, geo_reasons = compute_geo_review(final_data)
    needs_quality, quality_reasons = compute_quality_review(final_data)
    manual_edits = compute_manual_edits(extracted_data, final_data)

    fields_extracted = {k: getattr(extracted_data, k, None) for k in _EDITABLE_FIELDS}
    fields_final = {k: getattr(final_data, k, None) for k in _EDITABLE_FIELDS}

    return {
        "schema_version": 2,
        "session_id": session_id,
        "timestamp": now_iso,
        "saved_at": now_iso,
        "timezone": "Europe/Madrid",
        "app_version": _app_version(),
        "pc_name": socket.gethostname(),

        "fields_extracted": fields_extracted,
        "fields_final": fields_final,
        "manual_edits": manual_edits,

        "geo": _build_geo_block(final_data),
        "geo_diagnostics": _build_geo_diagnostics(final_data, norm_result),
        "google_candidates": _build_google_candidates(norm_result),
        "geo_trace": _build_geo_trace(final_data, norm_result),

        "needs_geo_review": needs_geo,
        "geo_review_reasons": geo_reasons,
        "needs_quality_review": needs_quality,
        "quality_review_reasons": quality_reasons,

        "transcripcion": transcript,

        "upload_status": upload_status,
        "last_upload_at": None,
        "remote_json_file_id": None,
        "remote_txt_file_id": None,
    }
