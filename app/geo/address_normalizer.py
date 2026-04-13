"""Normalizador de dirección de recogida usando Google Maps Geocoding API.

V2.1.2:
- matching bilingüe real (frase completa + token canonizado)
- fuzzy match SOLO como último recurso
- fuzzy SOLO sobre núcleo de calle / POI
- no fuzzy en INTERSECTION
- guardarraíles duros:
  - municipio en AMB
  - número coincide si existe en source
"""
from __future__ import annotations

import copy
import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING

from app.geo.amb_municipalities import is_amb_municipality, normalize_municipality
from app.geo.maps_client import GeoResult, GeoQueryResult, MapsClient
from app.geo.pickup_preprocessor import PickupQueryType, PreprocessedPickup, preprocess
from app.geo.pickup_repair import extract_best_pickup_from_transcript

if TYPE_CHECKING:
    from app.geo.pickup_stability import PickupStabilityTracker
    from app.parser.service_extractor import ServiceData

log = logging.getLogger(__name__)


class PickupStatus(Enum):
    VALIDATED = "validated"
    PARTIAL_MATCH = "partial_match"
    USABLE_REVIEW = "usable_review"
    OUTSIDE_AMB = "outside_amb"
    NO_RESULT = "no_result"
    OPERATOR_LOCKED = "operator_locked"
    SKIPPED = "skipped"


CacheKey = tuple[str, str, str]


_COMPARE_STOPWORDS = {
    "calle", "carrer", "carre", "av", "avda", "avenida", "avinguda",
    "paseo", "passeig", "pg", "plaza", "placa", "plaça", "pasaje", "passatge",
    "carretera", "ctra", "ronda", "via", "cami", "camí", "travessera",
    "numero", "número", "num", "n", "no",
    "barcelona", "espana", "españa", "hospitalet", "llobregat",
    "del", "de", "d", "la", "el", "els", "les", "en", "con", "entre",
    "esquina", "junto", "frente", "lado", "mismo", "misma",
}


# ---------------------------------------------------------------------------
# Capa 1: equivalencias de frase completa
# Se aplican ANTES del token matching y del fuzzy.
# Clave y valor en formato "normalizado" (lower + sin acentos).
# ---------------------------------------------------------------------------
_PHRASE_EQUIV = {
    "paseo de gracia": "passeig de gracia",
    "paseo gracia": "passeig de gracia",
    "passeig de gracia": "passeig de gracia",
    "paseo de graci a": "passeig de gracia",
    "consell de cent": "consell de cent",
    "consejo de ciento": "consell de cent",
    "consell de cien": "consell de cent",
    "pueblo nuevo": "poblenou",
    "poble nou": "poblenou",
    "poblenou": "poblenou",
    "ensanche": "eixample",
    "ensanche izquierdo": "eixample",
    "ensanche derecho": "eixample",
    "eixample": "eixample",
    "via laietana": "via laietana",
    "vialayetana": "via laietana",
    "vialaietana": "via laietana",
    "laietana": "via laietana",
    "hospital del mar": "hospital del mar",
    "la maquinista": "la maquinista",
    "gran via 2": "gran via 2",
    "vall d hebron": "vall d hebron",
    "vall de hebron": "vall d hebron",
    "hospital clinic": "hospital clinic",
    "hospital clinic barcelona": "hospital clinic",
    "santa anna": "santa anna",
    "santa ana": "santa anna",
    "plaza libertad": "placa llibertat",
    "placa llibertat": "placa llibertat",
    "plaza libertad hospitalet": "placa llibertat hospitalet",
    "carrer mestre nicolau": "mestre nicolau",
    "calle mestre nicolau": "mestre nicolau",
    "mestre nicolau": "mestre nicolau",
}


# ---------------------------------------------------------------------------
# Capa 2: equivalencias de tokens
# ---------------------------------------------------------------------------
_TOKEN_EQUIV = {
    "san": "sant",
    "sant": "sant",
    "santo": "sant",
    "santa": "santa",
    "ana": "anna",
    "anna": "anna",
    "iglesia": "esglesia",
    "esglesia": "esglesia",
    "libertad": "llibertat",
    "llibertat": "llibertat",
    "levante": "llevant",
    "llevant": "llevant",
    "poniente": "ponent",
    "ponent": "ponent",
    "mayor": "major",
    "major": "major",
    "nuevo": "nou",
    "nueva": "nova",
    "nou": "nou",
    "nova": "nova",
    "viejo": "vell",
    "vieja": "vella",
    "vell": "vell",
    "vella": "vella",
    "aragon": "arago",
    "arago": "arago",
    "cataluna": "catalunya",
    "catalunya": "catalunya",
    "gracia": "gracia",
    "graciaa": "gracia",
    "ensanche": "eixample",
    "eixample": "eixample",
    "diputacion": "diputacio",
    "diputacio": "diputacio",
    "consejo": "consell",
    "consell": "consell",
    "ciento": "cent",
    "cent": "cent",
    "corcega": "corsega",
    "corsega": "corsega",
    "valencia": "valencia",
    "valenciaa": "valencia",
    "mallorca": "mallorca",
    "provenza": "provenca",
    "provenca": "provenca",
    "rosellon": "rossello",
    "rossello": "rossello",
    "pedralbs": "pedralbes",
    "pedralbes": "pedralbes",
    "sarria": "sarria",
    "saria": "sarria",
    "montjuic": "montjuic",
    "monjuic": "montjuic",
    "orta": "horta",
    "horta": "horta",
    "poblenou": "poblenou",
    # "poble": "poblenou" eliminado — "poble" solo es ambiguo: "Poble Sec",
    # "Poble Nou", "Poblenou" son barrios distintos. Mapear "poble" a "poblenou"
    # rompía cualquier dirección del Poble Sec al canonizar sus tokens.
    # "poblenou" completo sí se mantiene porque es inequívoco.
    "nou": "nou",  # se filtra por longitud/stopwords según contexto
    "laietana": "laietana",
    "llayetana": "laietana",
    "lalletana": "laietana",
    "llibertad": "llibertat",
    "llibertat": "llibertat",
    # tipos de vía neutralizados
    "calle": "",
    "carrer": "",
    "carre": "",
    "avenida": "",
    "avinguda": "",
    "paseo": "",
    "passeig": "",
    "plaza": "",
    "placa": "",
    "plaça": "",
    "pasaje": "",
    "passatge": "",
    "carretera": "",
    "ctra": "",
    "via": "",
    "travessera": "",
}


@dataclass
class _NormalizeResult:
    status: PickupStatus
    formatted_address: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    place_id: Optional[str]
    partial_match: Optional[bool]
    municipality: Optional[str]
    google_called: bool
    retry_called: bool
    cache_hit: bool
    # --- campos de trazabilidad geo (schema_version 2) ---
    decision_reason: Optional[str] = None
    pickup_preprocessed_text: Optional[str] = None
    pickup_repaired_text: Optional[str] = None
    pickup_query_primary: Optional[str] = None
    pickup_query_retry: Optional[str] = None
    stage_before_google: Optional[str] = None  # step cerrado del catálogo
    google_result_count: int = 0
    was_retry_used: bool = False
    accepted_candidate_index: Optional[int] = None
    accepted_place_id: Optional[str] = None
    accepted_formatted_address: Optional[str] = None
    # candidatos raw (GeoResult) para construir google_candidates en session_builder
    _raw_candidates: list = None  # type: ignore[assignment]
    _accepted_result: Optional[GeoResult] = None
    # rejection_reason por índice de candidato
    _candidate_rejection_reasons: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._raw_candidates is None:
            self._raw_candidates = []
        if self._candidate_rejection_reasons is None:
            self._candidate_rejection_reasons = {}


_STREET_TYPE_ES_TO_CA: list[tuple[str, str]] = [
    # Orden importante: primero los más largos para evitar sustituciones parciales.
    # Se añade "de " tras el tipo de vía para generar "Carrer de Lepant",
    # que es la forma que Google Maps reconoce correctamente.
    (r"\bAvenida\s+(?:de\s+)?",    "Avinguda de "),
    (r"\bPaseo\s+(?:de\s+)?",      "Passeig de "),
    (r"\bPasaje\s+(?:de\s+)?",     "Passatge de "),
    (r"\bPlaza\s+(?:de\s+)?",      "Plaça de "),
    (r"\bCalle\s+(?:de\s+)?",      "Carrer de "),
    (r"\bCarretera\s+(?:de\s+)?",  "Carretera de "),
    (r"\bRonda\s+(?:de\s+)?",      "Ronda de "),
    (r"\bTraversera\s+(?:de\s+)?", "Travessera de "),
]

# Nombres de calles con terminación castellana → catalán oficial.
# Solo los más frecuentes y donde el cambio es relevante para Google Maps.
_STREET_NAME_ES_TO_CA: list[tuple[str, str]] = [
    (r"\bLepanto\b",    "Lepant"),
    (r"\bAragon\b",     "Aragó"),
    (r"\bRosellon\b",   "Rosselló"),
    (r"\bProvenza\b",   "Provença"),
    (r"\bCorcega\b",    "Còrsega"),
    (r"\bMallorca\b",   "Mallorca"),   # igual en ambos
    (r"\bValencia\b",   "València"),
    (r"\bDiputacion\b", "Diputació"),
    (r"\bConsejo\s+de\s+Ciento\b", "Consell de Cent"),
    (r"\bConsell\s+de\s+Cien\b",   "Consell de Cent"),
    (r"\bMuntaner\b",   "Muntaner"),   # igual
    (r"\bBalmes\b",     "Balmes"),     # igual
    (r"\bPellaires\b",  "Pellaires"),  # igual
]


def _translate_to_catalan(text: str) -> str:
    """Traduce tipo de vía y nombres de calle frecuentes al catalán oficial.

    'Calle Lepanto 35, Barcelona' → 'Carrer de Lepant 35, Barcelona'
    Si el texto ya está en catalán o no hay cambios, lo devuelve igual.
    """
    import re as _re
    result = text
    for pattern, replacement in _STREET_TYPE_ES_TO_CA:
        result = _re.sub(pattern, replacement, result, flags=_re.IGNORECASE)
    for pattern, replacement in _STREET_NAME_ES_TO_CA:
        result = _re.sub(pattern, replacement, result, flags=_re.IGNORECASE)
    return result


# Alias para compatibilidad con el código existente
_translate_street_type_to_catalan = _translate_to_catalan


def _build_enriched_query(
    cleaned: str,
    query_type: PickupQueryType,
    probable_muni: Optional[str],
) -> Optional[str]:
    if probable_muni:
        return f"{cleaned}, {probable_muni}, España"
    # Sin municipio explícito: añadir "Barcelona" como contexto AMB por defecto.
    # Evita que Google geocodifique "Calle Valencia 35" en Valencia ciudad.
    if query_type in (PickupQueryType.ADDRESS, PickupQueryType.INTERSECTION, PickupQueryType.AMBIGUOUS):
        return f"{cleaned}, Barcelona, España"
    return None


def _build_catalan_query(
    cleaned: str,
    query_type: PickupQueryType,
    probable_muni: Optional[str],
) -> Optional[str]:
    """Construye una query con el tipo de vía en catalán.

    Se usa como tercer intento cuando español y enriquecida fallan el guardarraíl
    de municipio. Google Maps a veces resuelve mejor con el nombre catalán oficial.
    """
    if query_type not in (PickupQueryType.ADDRESS, PickupQueryType.INTERSECTION, PickupQueryType.AMBIGUOUS):
        return None
    ca = _translate_street_type_to_catalan(cleaned)
    if ca == cleaned:
        # Ya estaba en catalán o no hay tipo de vía → no tiene sentido reintentar
        return None
    # Si el municipio ya está en la query traducida, no añadirlo de nuevo.
    # "Carrer de Lepant 35, Barcelona, Barcelona, España" confunde a Google.
    muni_suffix = probable_muni or "Barcelona"
    ca_norm = _strip_accents(ca).lower()
    muni_norm = _strip_accents(muni_suffix).lower()
    if muni_norm in ca_norm:
        return f"{ca}, España"
    return f"{ca}, {muni_suffix}, España"


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()


def _normalize_phrase(text: str) -> str:
    norm = _strip_accents(text)
    norm = re.sub(r"[,\.;:/\-]+", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()

    # aplicar equivalencias largas primero
    for src, dst in sorted(_PHRASE_EQUIV.items(), key=lambda x: -len(x[0])):
        norm = re.sub(rf"\b{re.escape(src)}\b", dst, norm)

    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def _compare_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-záéíóúüñç0-9']+", text.lower())
        if len(tok) >= 4 and tok not in _COMPARE_STOPWORDS
    }


def _canonical_tokens(text: str) -> set[str]:
    norm = _normalize_phrase(text)
    raw_tokens = re.findall(r"[a-z0-9']+", norm)

    out: set[str] = set()
    for tok in raw_tokens:
        mapped = _TOKEN_EQUIV.get(tok, tok)
        if not mapped:
            continue
        if mapped in _COMPARE_STOPWORDS:
            continue
        if len(mapped) < 4 and not mapped.isdigit():
            continue
        out.add(mapped)
    return out


def _extract_number(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,4})\b", text)
    return m.group(1) if m else None


def _extract_core_name(text: str) -> str:
    """
    Extrae el núcleo de calle/POI para fuzzy.
    No usa la dirección completa.
    """
    # solo primer segmento, antes de CP/municipio/etc.
    first = text.split(",")[0].strip()
    norm = _normalize_phrase(first)

    # quitar tipos de vía y palabras débiles
    norm = re.sub(
        r"\b(calle|carrer|carre|avenida|avinguda|paseo|passeig|plaza|placa|plaça|pasaje|passatge|carretera|ctra|ronda|via|travessera|de|del|d|la|el|els|les)\b",
        " ",
        norm,
    )
    norm = re.sub(r"\b\d{1,4}\b", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()

    # aplicar token canonization al core
    tokens = []
    for tok in re.findall(r"[a-z0-9']+", norm):
        mapped = _TOKEN_EQUIV.get(tok, tok)
        if mapped and mapped not in _COMPARE_STOPWORDS:
            tokens.append(mapped)

    core = " ".join(tokens).strip()
    return core


def _fuzzy_ratio_core(source: str, result: str) -> float:
    src_core = _extract_core_name(source)
    res_core = _extract_core_name(result)

    if not src_core or not res_core:
        return 0.0

    # comparar string completa del core
    ratios = [difflib.SequenceMatcher(None, src_core, res_core).ratio()]

    # comparar token principal de source contra tokens del result
    src_tokens = src_core.split()
    res_tokens = res_core.split()

    if src_tokens and res_tokens:
        src_main = max(src_tokens, key=len)
        for tok in res_tokens:
            ratios.append(difflib.SequenceMatcher(None, src_main, tok).ratio())

    return max(ratios)


def _soft_match_score(
    cleaned: str,
    result: GeoResult,
    query_type: PickupQueryType,
) -> tuple[float, bool, float, bool, float]:
    """
    Devuelve:
    - raw_ratio
    - soft_ok
    - canonical_ratio
    - number_match
    - fuzzy_ratio
    """
    source_tokens = _compare_tokens(cleaned)
    result_tokens = _compare_tokens(result.formatted_address)
    raw_overlap = len(source_tokens & result_tokens)
    raw_ratio = raw_overlap / max(1, len(source_tokens))

    source_canon = _canonical_tokens(cleaned)
    result_canon = _canonical_tokens(result.formatted_address)
    canon_overlap = len(source_canon & result_canon)
    canonical_ratio = canon_overlap / max(1, min(len(source_canon), len(result_canon)) or 1)

    src_num = _extract_number(cleaned)
    res_num = _extract_number(result.formatted_address)
    number_match = src_num is None or src_num == res_num

    # fuzzy SOLO como último recurso y nunca en INTERSECTION
    if query_type == PickupQueryType.INTERSECTION:
        fuzzy_ratio = 0.0
    else:
        fuzzy_ratio = _fuzzy_ratio_core(cleaned, result.formatted_address)

    if query_type in (PickupQueryType.POI_REFERENCE, PickupQueryType.TRANSIT_HUB):
        threshold = 0.34
    elif query_type == PickupQueryType.INTERSECTION:
        threshold = 0.28
    else:
        threshold = 0.50

    if query_type == PickupQueryType.ADDRESS and src_num is not None and not number_match:
        soft_ok = False
    else:
        soft_ok = (
            raw_ratio >= threshold
            or canonical_ratio >= threshold
            or fuzzy_ratio >= 0.75
        )

    return raw_ratio, soft_ok, canonical_ratio, number_match, fuzzy_ratio


def _evaluate_geo_result(
    result: Optional[GeoResult], prep: PreprocessedPickup
) -> tuple[PickupStatus, bool, Optional[str]]:
    """
    Devuelve:
    - status propuesto
    - acceptable: si se acepta ya sin más retry
    - rejection_reason: motivo de rechazo (catálogo cerrado) o None si aceptado
    """
    if result is None:
        return PickupStatus.NO_RESULT, False, "google_zero_results"

    muni = result.municipality
    in_amb = muni is not None and is_amb_municipality(muni)

    raw_ratio, soft_ok, canonical_ratio, number_match, fuzzy_ratio = _soft_match_score(
        prep.cleaned,
        result,
        prep.query_type,
    )

    # Guardarraíl duro: si hay número en source y no coincide, nunca validar ADDRESS
    if prep.query_type == PickupQueryType.ADDRESS and _extract_number(prep.cleaned) is not None and not number_match:
        if in_amb and result.partial_match:
            return PickupStatus.PARTIAL_MATCH, False, "number_conflict"
        return PickupStatus.OUTSIDE_AMB, False, "number_conflict"

    # Guardarraíl de municipio: si el preprocessor detectó un municipio explícito
    # en la query (p.ej. "Barcelona") y el resultado es de otro municipio AMB
    # (p.ej. Cornellà), rechazar para forzar el retry con query enriquecida.
    if (
        prep.probable_municipality is not None
        and in_amb
        and muni is not None
    ):
        result_muni_official = normalize_municipality(muni)
        query_muni_official = normalize_municipality(prep.probable_municipality)
        if (
            result_muni_official is not None
            and query_muni_official is not None
            and result_muni_official != query_muni_official
        ):
            log.debug(
                "[PICKUP] municipio_mismatch: query=%r result=%r → rechazado para retry",
                query_muni_official, result_muni_official,
            )
            return PickupStatus.OUTSIDE_AMB, False, "municipality_conflict"

    # ------------------------------------------------------------------
    # 1) Frase/token canonizado fuerte
    # ------------------------------------------------------------------
    if in_amb and prep.query_type == PickupQueryType.ADDRESS and number_match and canonical_ratio >= 0.80:
        return PickupStatus.VALIDATED, True, None

    # ------------------------------------------------------------------
    # 2) Resultado limpio y razonable
    # ------------------------------------------------------------------
    if in_amb and soft_ok and not result.partial_match:
        return PickupStatus.VALIDATED, True, None

    # ------------------------------------------------------------------
    # 3) POI / hub con matching suficiente
    # ------------------------------------------------------------------
    if prep.query_type in (PickupQueryType.POI_REFERENCE, PickupQueryType.TRANSIT_HUB):
        if in_amb and not result.partial_match:
            return PickupStatus.VALIDATED, True, None
        if in_amb and (canonical_ratio >= 0.50 or fuzzy_ratio >= 0.86 or soft_ok):
            return PickupStatus.VALIDATED, True, None
        if in_amb and fuzzy_ratio >= 0.75:
            return PickupStatus.USABLE_REVIEW, True, None

    # ------------------------------------------------------------------
    # 4) Fuzzy como último recurso para ADDRESS
    # ------------------------------------------------------------------
    if in_amb and prep.query_type == PickupQueryType.ADDRESS and number_match:
        if fuzzy_ratio >= 0.86:
            return PickupStatus.VALIDATED, True, None
        if fuzzy_ratio >= 0.75:
            return PickupStatus.USABLE_REVIEW, True, None

    # ------------------------------------------------------------------
    # 5) Intersections: nunca verde por fuzzy, solo usable_review
    # ------------------------------------------------------------------
    if prep.query_type == PickupQueryType.INTERSECTION:
        if in_amb and (canonical_ratio >= 0.35 or raw_ratio >= 0.28):
            return PickupStatus.USABLE_REVIEW, True, None

    # ------------------------------------------------------------------
    # 6) Partial dentro AMB: mantener como revisión útil / parcial
    # ------------------------------------------------------------------
    if in_amb and result.partial_match:
        if prep.query_type == PickupQueryType.ADDRESS and number_match and canonical_ratio >= 0.55:
            return PickupStatus.USABLE_REVIEW, True, None
        return PickupStatus.PARTIAL_MATCH, False, "partial_match_only"

    if not in_amb:
        return PickupStatus.OUTSIDE_AMB, False, "candidate_outside_amb"

    # Sin candidato de confianza suficiente
    return PickupStatus.OUTSIDE_AMB, False, "rejected_low_confidence"


def _copy_pickup_state(target: "ServiceData", source: "ServiceData") -> "ServiceData":
    pickup_fields = [
        "recogida",
        "_recogida_raw",
        "_recogida_latlon",
        "_recogida_place_id",
        "_recogida_partial_match",
        "_recogida_status",
        "_recogida_municipio",
        "_geo_google_called",
        "_geo_retry_called",
        "_geo_cache_hit",
        "_geo_operator_edited_pickup",
        "_pickup_repair_correction",
        "_pickup_unit_detail",
        "_pickup_type",
    ]
    for field_name in pickup_fields:
        setattr(target, field_name, getattr(source, field_name))
    return target


class AddressNormalizer:
    def __init__(self, maps_client: MapsClient) -> None:
        self._maps = maps_client
        self._cache: dict[CacheKey, _NormalizeResult] = {}
        self._last_raw: str = ""

    @staticmethod
    def _score_candidates_with_reasons(
        qr: "GeoQueryResult",
        prep: PreprocessedPickup,
    ) -> tuple[
        Optional[GeoResult],
        Optional[int],
        Optional[str],
        dict,
        int,
    ]:
        """Evalúa todos los candidatos y devuelve:
        (best_accepted, accepted_index, decision_reason, rejection_by_idx, result_count)
        """
        from app.geo.amb_municipalities import is_amb_municipality as _is_amb
        candidates = qr.candidates
        result_count = len(candidates)
        rejection_by_idx: dict = {}

        accepted_result: Optional[GeoResult] = None
        accepted_index: Optional[int] = None
        final_decision_reason: Optional[str] = None

        # Buscar primer candidato aceptable
        for idx, cand in enumerate(candidates):
            _status, acceptable, reason = _evaluate_geo_result(cand, prep)
            if not acceptable:
                rejection_by_idx[idx] = reason
            if acceptable and accepted_result is None:
                accepted_result = cand
                accepted_index = idx
                final_decision_reason = None  # aceptado → reason es "accepted_*"
                # Determinar decision_reason de aceptación
                muni = cand.municipality
                in_amb = muni is not None and _is_amb(muni)
                if _status == PickupStatus.VALIDATED and in_amb:
                    final_decision_reason = "accepted_high_confidence"
                else:
                    final_decision_reason = "accepted_high_confidence"
                # Seguimos iterando para calcular rejection del resto
                continue
            if accepted_result is not None and idx not in rejection_by_idx:
                # Los candidatos posteriores al aceptado no se evalúan como rechazo
                pass

        if accepted_result is None:
            # Ningún candidato aceptado → calcular decision_reason del fallo
            if result_count == 0:
                final_decision_reason = "google_zero_results"
            else:
                # Tomar el reason del primer candidato como indicador principal
                r0 = rejection_by_idx.get(0)
                # Si varios candidatos válidos → ambiguous
                valid_amb = [
                    idx for idx, cand in enumerate(candidates)
                    if cand.municipality and _is_amb(cand.municipality)
                ]
                if len(valid_amb) > 1 and not r0:
                    final_decision_reason = "multiple_valid_candidates"
                elif r0:
                    final_decision_reason = r0
                else:
                    final_decision_reason = "no_high_confidence_candidate"

        return accepted_result, accepted_index, final_decision_reason, rejection_by_idx, result_count

    def _geocode_with_retry(self, prep: PreprocessedPickup) -> _NormalizeResult:
        from app.geo.amb_municipalities import normalize_municipality as _norm_muni

        # ── Intento 1: query primaria (cleaned del preprocessor) ──────────
        qr1 = self._maps.geocode_full(prep.cleaned)
        accepted1, acc_idx1, decision_reason1, rejection1, count1 = self._score_candidates_with_reasons(qr1, prep)

        if accepted1 is not None:
            muni_official = _norm_muni(accepted1.municipality or "")
            return _NormalizeResult(
                status=_evaluate_geo_result(accepted1, prep)[0],
                formatted_address=accepted1.formatted_address,
                lat=accepted1.lat,
                lon=accepted1.lon,
                place_id=accepted1.place_id,
                partial_match=accepted1.partial_match,
                municipality=muni_official or accepted1.municipality,
                google_called=True,
                retry_called=False,
                cache_hit=False,
                decision_reason=decision_reason1,
                pickup_query_primary=prep.cleaned,
                pickup_query_retry=None,
                google_result_count=count1,
                was_retry_used=False,
                accepted_candidate_index=acc_idx1,
                accepted_place_id=accepted1.place_id,
                accepted_formatted_address=accepted1.formatted_address,
                _raw_candidates=qr1.candidates,
                _accepted_result=accepted1,
                _candidate_rejection_reasons=rejection1,
            )

        # ── Intento 2: query enriquecida (con municipio / Barcelona) ──────
        enriched = _build_enriched_query(prep.cleaned, prep.query_type, prep.probable_municipality)
        if not enriched:
            # Sin retry posible — devolver resultado del intento 1
            best1 = qr1.best
            return _NormalizeResult(
                status=_evaluate_geo_result(best1, prep)[0] if best1 else PickupStatus.NO_RESULT,
                formatted_address=best1.formatted_address if best1 else None,
                lat=best1.lat if best1 else None,
                lon=best1.lon if best1 else None,
                place_id=best1.place_id if best1 else None,
                partial_match=best1.partial_match if best1 else None,
                municipality=_norm_muni(best1.municipality or "") if best1 and best1.municipality else (best1.municipality if best1 else None),
                google_called=bool(best1),
                retry_called=False,
                cache_hit=False,
                decision_reason=decision_reason1,
                pickup_query_primary=prep.cleaned,
                pickup_query_retry=None,
                google_result_count=count1,
                was_retry_used=False,
                _raw_candidates=qr1.candidates,
                _accepted_result=None,
                _candidate_rejection_reasons=rejection1,
            )

        log.debug("geocoding retry enriched=%r", enriched)
        qr2 = self._maps.geocode_full(enriched)
        accepted2, acc_idx2, decision_reason2, rejection2, count2 = self._score_candidates_with_reasons(qr2, prep)

        if accepted2 is not None:
            muni_official = _norm_muni(accepted2.municipality or "")
            return _NormalizeResult(
                status=_evaluate_geo_result(accepted2, prep)[0],
                formatted_address=accepted2.formatted_address,
                lat=accepted2.lat,
                lon=accepted2.lon,
                place_id=accepted2.place_id,
                partial_match=accepted2.partial_match,
                municipality=muni_official or accepted2.municipality,
                google_called=True,
                retry_called=True,
                cache_hit=False,
                decision_reason=decision_reason2,
                pickup_query_primary=prep.cleaned,
                pickup_query_retry=enriched,
                google_result_count=count2,
                was_retry_used=True,
                accepted_candidate_index=acc_idx2,
                accepted_place_id=accepted2.place_id,
                accepted_formatted_address=accepted2.formatted_address,
                _raw_candidates=qr2.candidates,
                _accepted_result=accepted2,
                _candidate_rejection_reasons=rejection2,
            )

        # ── Intento 3: tipo de vía en catalán ─────────────────────────────
        catalan_query = _build_catalan_query(prep.cleaned, prep.query_type, prep.probable_municipality)
        if catalan_query:
            log.debug("geocoding retry catalan=%r", catalan_query)
            qr3 = self._maps.geocode_full(catalan_query)
            accepted3, acc_idx3, decision_reason3, rejection3, count3 = self._score_candidates_with_reasons(qr3, prep)
            if accepted3 is not None:
                muni_official = _norm_muni(accepted3.municipality or "")
                return _NormalizeResult(
                    status=_evaluate_geo_result(accepted3, prep)[0],
                    formatted_address=accepted3.formatted_address,
                    lat=accepted3.lat,
                    lon=accepted3.lon,
                    place_id=accepted3.place_id,
                    partial_match=accepted3.partial_match,
                    municipality=muni_official or accepted3.municipality,
                    google_called=True,
                    retry_called=True,
                    cache_hit=False,
                    decision_reason=decision_reason3,
                    pickup_query_primary=prep.cleaned,
                    pickup_query_retry=catalan_query,
                    google_result_count=count3,
                    was_retry_used=True,
                    accepted_candidate_index=acc_idx3,
                    accepted_place_id=accepted3.place_id,
                    accepted_formatted_address=accepted3.formatted_address,
                    _raw_candidates=qr3.candidates,
                    _accepted_result=accepted3,
                    _candidate_rejection_reasons=rejection3,
                )

        # ── Ningún intento tuvo éxito — usar el mejor de intento 2 ────────
        best2 = qr2.best
        if best2 is not None:
            muni_official = _norm_muni(best2.municipality or "")
            return _NormalizeResult(
                status=_evaluate_geo_result(best2, prep)[0],
                formatted_address=best2.formatted_address,
                lat=best2.lat,
                lon=best2.lon,
                place_id=best2.place_id,
                partial_match=best2.partial_match,
                municipality=muni_official or best2.municipality,
                google_called=True,
                retry_called=True,
                cache_hit=False,
                decision_reason=decision_reason2,
                pickup_query_primary=prep.cleaned,
                pickup_query_retry=enriched,
                google_result_count=count2,
                was_retry_used=True,
                _raw_candidates=qr2.candidates,
                _accepted_result=None,
                _candidate_rejection_reasons=rejection2,
            )

        # Fallback: resultado del intento 1
        best1 = qr1.best
        return _NormalizeResult(
            status=_evaluate_geo_result(best1, prep)[0] if best1 else PickupStatus.NO_RESULT,
            formatted_address=best1.formatted_address if best1 else None,
            lat=best1.lat if best1 else None,
            lon=best1.lon if best1 else None,
            place_id=best1.place_id if best1 else None,
            partial_match=best1.partial_match if best1 else None,
            municipality=_norm_muni(best1.municipality or "") if best1 and best1.municipality else (best1.municipality if best1 else None),
            google_called=True,
            retry_called=True,
            cache_hit=False,
            decision_reason=decision_reason1 or "no_high_confidence_candidate",
            pickup_query_primary=prep.cleaned,
            pickup_query_retry=enriched,
            google_result_count=count1,
            was_retry_used=True,
            _raw_candidates=qr1.candidates,
            _accepted_result=None,
            _candidate_rejection_reasons=rejection1,
        )

    def _apply_result(
        self,
        data: "ServiceData",
        raw: str,
        norm_result: _NormalizeResult,
        pickup_for_geocoding: Optional[str] = None,
    ) -> "ServiceData":
        # pickup_for_geocoding es la dirección enriquecida (con número inyectado).
        # Se usa como fallback cuando Google no devuelve formatted_address.
        # Si no se pasa, se usa raw (comportamiento anterior).
        display_fallback = (pickup_for_geocoding or raw).replace(" (REVISAR)", "").strip()

        data._recogida_raw = raw
        data._recogida_status = norm_result.status.value
        data._recogida_municipio = norm_result.municipality
        data._recogida_place_id = norm_result.place_id
        data._recogida_partial_match = norm_result.partial_match
        data._geo_google_called = norm_result.google_called
        data._geo_retry_called = norm_result.retry_called
        data._geo_cache_hit = norm_result.cache_hit
        # Trazabilidad completa — usada por session_builder para geo_diagnostics
        data._geo_norm_result = norm_result
        data._recogida_latlon = (
            (norm_result.lat, norm_result.lon)
            if norm_result.lat is not None and norm_result.lon is not None
            else None
        )

        status = norm_result.status

        if status == PickupStatus.VALIDATED:
            data.recogida = norm_result.formatted_address or display_fallback

        elif status in (PickupStatus.PARTIAL_MATCH, PickupStatus.USABLE_REVIEW):
            addr = norm_result.formatted_address or display_fallback
            data.recogida = addr if addr.endswith("(REVISAR)") else f"{addr} (REVISAR)"

        elif status in (PickupStatus.OUTSIDE_AMB, PickupStatus.NO_RESULT):
            data.recogida = f"{display_fallback} (REVISAR)"

        log.debug("[PICKUP] pickup_shown_in_ui=%r  status=%s", data.recogida, status.value)
        return data

    def _prepare_pickup(self, transcript: str, raw: str):
        repair = extract_best_pickup_from_transcript(transcript, raw)
        log.debug(
            "[PICKUP] llm_pickup_raw=%r  repair.address_for_geocoding=%r  "
            "number_injected=%r",
            raw,
            repair.address_for_geocoding,
            repair.correction_detected,
        )
        if repair.is_incomplete:
            return None, repair

        prep = preprocess(repair.address_for_geocoding)
        log.debug("[PICKUP] pickup_sent_to_maps=%r  query_type=%s", prep.cleaned, prep.query_type.value)
        prep = PreprocessedPickup(
            cleaned=prep.cleaned,
            query_type=prep.query_type,
            probable_municipality=prep.probable_municipality,
            original=repair.original,
        )
        return prep, repair

    def _run(
        self,
        data: "ServiceData",
        transcript: str,
        current_data: "ServiceData",
        locked_fields: set[str],
        bypass_stability: bool = False,
        stability_tracker: Optional["PickupStabilityTracker"] = None,
    ) -> "ServiceData":
        data = copy.copy(data)

        if "recogida" in locked_fields:
            data._recogida_status = PickupStatus.OPERATOR_LOCKED.value
            return data

        raw = (data.recogida or "").strip()
        if not raw or raw.upper() == "PENDIENTE":
            data._recogida_status = PickupStatus.SKIPPED.value
            return data
        if raw.upper() == "DESCONOCIDA":
            data._recogida_status = "unknown_or_incomplete"
            data.recogida = "DESCONOCIDA"
            return data

        if not bypass_stability and stability_tracker is not None:
            stability_tracker.observe(raw)
            if not stability_tracker.is_stable():
                if getattr(current_data, "_recogida_status", "") not in ("", PickupStatus.SKIPPED.value):
                    return _copy_pickup_state(data, current_data)
                data._recogida_status = PickupStatus.SKIPPED.value
                return data

        prep, repair = self._prepare_pickup(transcript, raw)
        # pickup_for_geocoding: la dirección enriquecida (calle LLM + número
        # inyectado del transcript). Es lo que se manda a Maps y lo que se
        # muestra en UI cuando Google no devuelve formatted_address.
        pickup_for_geocoding = repair.address_for_geocoding

        data._pickup_repair_correction = repair.correction_detected
        data._pickup_unit_detail = repair.unit_detail

        # No modificar observaciones si el operador la tiene bloqueada.
        # El lock de observaciones debe respetarse en todo el pipeline,
        # no solo en el extractor LLM.
        if repair.unit_detail and "observaciones" not in locked_fields:
            obs = (getattr(data, "observaciones", "PENDIENTE") or "PENDIENTE").strip()
            if obs.upper() == "PENDIENTE" or obs == "":
                data.observaciones = repair.unit_detail
            elif repair.unit_detail not in obs:
                data.observaciones = obs + " | " + repair.unit_detail

        if prep is None or prep.query_type == PickupQueryType.UNKNOWN_OR_INCOMPLETE:
            data._pickup_type = PickupQueryType.UNKNOWN_OR_INCOMPLETE.value
            data._recogida_status = "unknown_or_incomplete"
            data.recogida = "DESCONOCIDA"
            data._recogida_raw = raw
            return data

        data._pickup_type = prep.query_type.value

        # Shortcircuit de caché: solo si el raw Y el pickup enriquecido no cambiaron.
        # Si el número se inyectó esta vez pero no la anterior, hay que re-geocodificar.
        if raw == self._last_raw and not repair.correction_detected and getattr(current_data, "_recogida_status", "") not in ("", PickupStatus.SKIPPED.value):
            return _copy_pickup_state(data, current_data)

        cache_key: CacheKey = (
            prep.cleaned,
            prep.query_type.value,
            prep.probable_municipality or "",
        )
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            cached_copy = _NormalizeResult(**{**cached.__dict__, "cache_hit": True})
            self._last_raw = raw
            return self._apply_result(data, raw, cached_copy, pickup_for_geocoding)

        norm_result = self._geocode_with_retry(prep)

        # Enriquecer resultado con datos del pipeline previos a Google
        # (no están disponibles dentro de _geocode_with_retry)
        repaired_text = repair.address_for_geocoding
        preprocessed_text = prep.cleaned
        original_extracted = raw  # raw = texto original extraído por el LLM

        # stage_before_google: último paso de transformación antes de llamar a Google
        if preprocessed_text != repaired_text:
            stage_bg = "preprocess_pickup"
        elif repair.correction_detected and repaired_text != original_extracted:
            stage_bg = "repair_pickup"
        else:
            stage_bg = "extracted_pickup"

        norm_result.pickup_repaired_text = repaired_text
        norm_result.pickup_preprocessed_text = preprocessed_text
        norm_result.stage_before_google = stage_bg

        self._cache[cache_key] = norm_result
        self._last_raw = raw
        return self._apply_result(data, raw, norm_result, pickup_for_geocoding)

    def normalize_pickup(
        self,
        data: "ServiceData",
        transcript: str,
        current_data: "ServiceData",
        locked_fields: set[str],
        stability_tracker: Optional["PickupStabilityTracker"] = None,
    ) -> "ServiceData":
        return self._run(
            data,
            transcript,
            current_data,
            locked_fields,
            bypass_stability=False,
            stability_tracker=stability_tracker,
        )

    def normalize_pickup_now(
        self,
        data: "ServiceData",
        transcript: str,
        current_data: "ServiceData",
        locked_fields: set[str],
    ) -> "ServiceData":
        return self._run(
            data,
            transcript,
            current_data,
            locked_fields,
            bypass_stability=True,
            stability_tracker=None,
        )
