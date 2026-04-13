"""Preprocesador local de direcciones de recogida antes de llamar a Google Maps.

Fixes:
- no sustituir por POI conocido si la cadena ya parece una dirección postal
- el hit de POI ya no vale por cualquier substring débil
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from app.geo.amb_municipalities import _norm

if TYPE_CHECKING:
    from app.geo.pickup_repair import RepairResult


class PickupQueryType(Enum):
    ADDRESS = "address"
    INTERSECTION = "intersection"
    POI_REFERENCE = "poi_reference"
    TRANSIT_HUB = "transit_hub"
    AMBIGUOUS = "ambiguous"
    UNKNOWN_OR_INCOMPLETE = "unknown_or_incomplete"


@dataclass
class PreprocessedPickup:
    cleaned: str
    query_type: PickupQueryType
    probable_municipality: Optional[str]
    original: str


_ABBREVIATIONS: list[tuple[str, str]] = [
    (r"\bavda\.?\b", "Avinguda"),
    (r"\bavd\.?\b", "Avinguda"),
    (r"\bav\.?\b", "Avinguda"),
    (r"\bpg\.?\b", "Passeig"),
    (r"\bpas\.?\b", "Passeig"),
    (r"\bpº\b", "Passeig"),
    (r"\bpzza\.?\b", "Piazza"),
    (r"\bc/\b", "Carrer"),
    (r"\bcl\.?\b", "Carrer"),
    (r"\bctra\.?\b", "Carretera"),
    (r"\bcrta\.?\b", "Carretera"),
    (r"\bpl\.?\b", "Plaça"),
    (r"\bpla\.?\b", "Plaça"),
    (r"\bpza\.?\b", "Plaza"),
    (r"\bnº\b", ""),
    (r"\bnum\.?\b", ""),
    (r"\bpral\.?\b", "principal"),
    (r"\bpto\.?\b", "puerta"),
    (r"\besc\.?\b", "escalera"),
    (r"\bbajos?\b", "bajo"),
]

# Correcciones de confusión STT/LLM: palabras que Whisper o el LLM
# confunden habitualmente con tipos de vía en contexto de dirección.
# Se aplican ANTES de enviar la query a Google Maps.
# Nota: los lookAhead usan \w para compatibilidad unicode en Windows.
_STT_CONFUSION_FIXES: list[tuple[str, str]] = [
    # "Café Mallorca 403" → "Calle Mallorca 403"
    # "café/cafe" seguido de palabra (nombre de calle) + dígitos = dirección postal
    (r"(?i)\bcaf[eé]\b(?=\s+\w+\s+\d)", "Calle"),
]

_INTERSECTION_MARKERS = [
    " con ",
    " esquina con ",
    " esquina ",
    " cruce con ",
    " cruce ",
    " entre ",
    " / ",
    " y la ",
    " y el ",
]

# Orden importante: primero los más largos para evitar sustituciones parciales
_INTERSECTION_MARKER_RE_LIST: list[tuple[str, str]] = [
    (r"\s+esquina\s+con\s+", " & "),
    (r"\s+esquina\s+",       " & "),
    (r"\s+cruce\s+con\s+",   " & "),
    (r"\s+cruce\s+",         " & "),
    (r"\s+con\s+",           " & "),
    (r"\s+entre\s+",         " & "),
    (r"\s+y\s+la\s+",        " & "),
    (r"\s+y\s+el\s+",        " & "),
    (r"\s*/\s*",             " & "),
]


def _rewrite_intersection_as_ampersand(text: str) -> str:
    """Reescribe una intersección usando '&' para que Google Maps la resuelva bien.

    'Calle Valencia con Paseo San Juan, Barcelona'
    → 'Calle Valencia & Paseo San Juan, Barcelona'

    Google Maps resuelve intersecciones con '&' de forma mucho más precisa
    que con 'con', 'esquina', etc.
    """
    result = text
    for pattern, replacement in _INTERSECTION_MARKER_RE_LIST:
        new = re.sub(pattern, replacement, result, flags=re.IGNORECASE, count=1)
        if new != result:
            return new
    return result

_POI_MARKERS = [
    "junto a", "junto al", "junto a la",
    "frente a", "frente al", "frente a la",
    "al lado de", "al lado del",
    "cerca de", "cerca del",
    "delante de", "delante del",
    "enfrente de", "enfrente del",
    "ambulatorio", "cap ", "cap de", "caf ",
    "centro de salud", "consultorio",
    "hospital ", "clinica", "clínica",
    "colegio ", "escuela ", "instituto ",
    "mercado ", "mercado municipal",
    "facultad", "universidad",
    "polideportivo", "piscina municipal",
    "parking ", "aparcamiento",
    "supermercado", "hipermercado",
    "farmacia",
]

TAXI_KNOWN_POIS: dict[str, tuple[str, PickupQueryType, Optional[str]]] = {
    "aeropuerto": (
        "Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "aeroport": (
        "Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "terminal 1": (
        "Terminal 1 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "terminal1": (
        "Terminal 1 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    " t1": (
        "Terminal 1 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "terminal 2": (
        "Terminal 2 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "terminal2": (
        "Terminal 2 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    " t2": (
        "Terminal 2 Aeropuerto Josep Tarradellas Barcelona-El Prat",
        PickupQueryType.TRANSIT_HUB, "El Prat de Llobregat",
    ),
    "estacion sants": (
        "Estació de Barcelona-Sants, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "estacio sants": (
        "Estació de Barcelona-Sants, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "sants estacion": (
        "Estació de Barcelona-Sants, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "arc de triomf": (
        "Estació Arc de Triomf, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "estacion de francia": (
        "Estació de França, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "estacio de franca": (
        "Estació de França, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "passeig de gracia estacion": (
        "Estació de Passeig de Gràcia, Barcelona",
        PickupQueryType.TRANSIT_HUB, "Barcelona",
    ),
    "vall d hebron": (
        "Hospital Universitari Vall d'Hebron, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "vall hebron": (
        "Hospital Universitari Vall d'Hebron, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "bellvitge": (
        "Hospital Universitari de Bellvitge, L'Hospitalet de Llobregat",
        PickupQueryType.POI_REFERENCE, "L'Hospitalet de Llobregat",
    ),
    "can ruti": (
        "Hospital Universitari Germans Trias i Pujol, Badalona",
        PickupQueryType.POI_REFERENCE, "Badalona",
    ),
    "germans trias": (
        "Hospital Universitari Germans Trias i Pujol, Badalona",
        PickupQueryType.POI_REFERENCE, "Badalona",
    ),
    "hospital clinic": (
        "Hospital Clínic de Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "clinic barcelona": (
        "Hospital Clínic de Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "sant pau": (
        "Hospital de la Santa Creu i Sant Pau, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "hospital del mar": (
        "Hospital del Mar, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "esperit sant": (
        "Hospital de l'Esperit Sant, Santa Coloma de Gramenet",
        PickupQueryType.POI_REFERENCE, "Santa Coloma de Gramenet",
    ),
    "hospital de sant boi": (
        "Hospital Psiquiàtric Universitari Institut Pere Mata, Sant Boi de Llobregat",
        PickupQueryType.POI_REFERENCE, "Sant Boi de Llobregat",
    ),
    "diagonal mar": (
        "Diagonal Mar, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "forum": (
        "Parc del Fòrum, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "forum barcelona": (
        "Parc del Fòrum, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "mercabarna": (
        "Mercabarna, El Prat de Llobregat",
        PickupQueryType.POI_REFERENCE, "El Prat de Llobregat",
    ),
    "gran via 2": (
        "Gran Via 2, L'Hospitalet de Llobregat",
        PickupQueryType.POI_REFERENCE, "L'Hospitalet de Llobregat",
    ),
    "la maquinista": (
        "La Maquinista, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "glories": (
        "Les Glòries, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "les glories": (
        "Les Glòries, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "maremagnum": (
        "Maremagnum, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "gran via fira": (
        "Gran Via de l'Hospitalet - Fira de Barcelona, L'Hospitalet de Llobregat",
        PickupQueryType.POI_REFERENCE, "L'Hospitalet de Llobregat",
    ),
    "fira hospitalet": (
        "Fira Gran Via, L'Hospitalet de Llobregat",
        PickupQueryType.POI_REFERENCE, "L'Hospitalet de Llobregat",
    ),
    "fira montjuic": (
        "Fira de Barcelona Montjuïc, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "camp nou": (
        "Camp Nou, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
    "palau sant jordi": (
        "Palau Sant Jordi, Barcelona",
        PickupQueryType.POI_REFERENCE, "Barcelona",
    ),
}

_POI_NORM: dict[str, tuple[str, PickupQueryType, Optional[str]]] = {
    _norm(k): v for k, v in TAXI_KNOWN_POIS.items()
}

_POSTAL_NUMBER_RE = re.compile(r"\b\d{1,4}\b", re.IGNORECASE)
_POSTAL_STREET_RE = re.compile(
    r"\b(calle|carrer|carre|avenida|avinguda|avda|av\.?|paseo|passeig|pasaje|passatge|plaza|plaça|via|ronda|carretera|travessera)\b",
    re.IGNORECASE,
)


def _strip_revisar(text: str) -> str:
    return re.sub(r"\s*\(REVISAR\)\s*$", "", text, flags=re.IGNORECASE).strip()


def _apply_stt_confusion_fixes(text: str) -> str:
    """Corrige confusiones frecuentes del STT/LLM antes de geocodificar.

    Por ejemplo, Whisper o GPT a veces transcriben/extraen "Café Mallorca 403"
    cuando el cliente dijo "Calle Mallorca 403".  Este fix solo actúa cuando
    la palabra confundida va seguida de un nombre propio (mayúscula), lo que
    reduce falsos positivos sobre POIs reales (p.ej. "Café de Flore").
    """
    for pattern, replacement in _STT_CONFUSION_FIXES:
        text = re.sub(pattern, replacement, text)
    return text


def _apply_abbreviations(text: str) -> str:
    for pattern, replacement in _ABBREVIATIONS:
        text = re.sub(pattern, replacement + " ", text, flags=re.IGNORECASE)
    return re.sub(r" {2,}", " ", text).strip()


def _looks_like_postal_address(text: str) -> bool:
    has_number = bool(_POSTAL_NUMBER_RE.search(text))
    has_street = bool(_POSTAL_STREET_RE.search(text))
    return has_number or has_street


def _find_poi_hit(
    normalized: str,
) -> Optional[tuple[str, PickupQueryType, Optional[str]]]:
    """
    Match más estricto:
    - no basta cualquier substring
    - exige que el POI ocupe una parte significativa de la cadena,
      o que la cadena empiece/termine por ese POI
    """
    hits: list[tuple[str, tuple[str, PickupQueryType, Optional[str]]]] = []
    for k, v in _POI_NORM.items():
        if k not in normalized:
            continue

        ratio = len(k) / max(1, len(normalized))
        if normalized == k or normalized.startswith(k) or normalized.endswith(k) or ratio >= 0.45:
            hits.append((k, v))

    if not hits:
        return None

    hits.sort(key=lambda x: len(x[0]), reverse=True)
    return hits[0][1]


def _detect_intersection(text_lower: str) -> bool:
    return any(marker in text_lower for marker in _INTERSECTION_MARKERS)


def _detect_poi_generic(text_lower: str) -> bool:
    return any(marker in text_lower for marker in _POI_MARKERS)


def _infer_municipality(text_lower: str) -> Optional[str]:
    from app.geo.amb_municipalities import _ALIAS_NORM, _AMB_NORM
    norm = _norm(text_lower)
    for alias_norm, official in sorted(_ALIAS_NORM.items(), key=lambda x: -len(x[0])):
        if alias_norm in norm:
            return official
    for muni_norm, official in sorted(_AMB_NORM.items(), key=lambda x: -len(x[0])):
        if muni_norm in norm:
            return official
    return None


def preprocess_with_repair(raw: str) -> "tuple[PreprocessedPickup, RepairResult]":
    from app.geo.pickup_repair import extract_best_pickup, RepairResult

    repair = extract_best_pickup(raw)

    if repair.is_incomplete:
        prep = PreprocessedPickup(
            cleaned=raw,
            query_type=PickupQueryType.UNKNOWN_OR_INCOMPLETE,
            probable_municipality=None,
            original=raw,
        )
        return prep, repair

    prep = preprocess(repair.address_for_geocoding)
    prep = PreprocessedPickup(
        cleaned=prep.cleaned,
        query_type=prep.query_type,
        probable_municipality=prep.probable_municipality,
        original=raw,
    )
    return prep, repair


def preprocess(raw: str) -> PreprocessedPickup:
    from app.geo.known_pickup_aliases import resolve_known_pickup_alias

    original = raw

    text = _strip_revisar(raw)
    text = _apply_stt_confusion_fixes(text)
    cleaned = _apply_abbreviations(text)
    text_norm = _norm(cleaned)

    # Si ya parece dirección postal, NO sustituir por POI conocido aunque
    # contenga "aeropuerto" u otro substring en cola conversacional.
    looks_postal = _looks_like_postal_address(cleaned)

    # 0) Alias locales del negocio (clínicas, hoteles, CAPs, recintos)
    if not looks_postal:
        alias = resolve_known_pickup_alias(cleaned)
        if alias:
            return PreprocessedPickup(
                cleaned=alias.canonical_query,
                query_type=PickupQueryType(alias.query_type),
                probable_municipality=alias.municipality,
                original=original,
            )

    # 1) POIs globales conocidos
    if not looks_postal:
        poi_hit = _find_poi_hit(text_norm)
        if poi_hit:
            expanded, query_type, probable_muni = poi_hit
            return PreprocessedPickup(
                cleaned=expanded,
                query_type=query_type,
                probable_municipality=probable_muni,
                original=original,
            )

    if _detect_intersection(cleaned.lower()):
        probable_muni = _infer_municipality(cleaned.lower())
        cleaned_intersection = _rewrite_intersection_as_ampersand(cleaned)
        return PreprocessedPickup(
            cleaned=cleaned_intersection,
            query_type=PickupQueryType.INTERSECTION,
            probable_municipality=probable_muni,
            original=original,
        )

    if _detect_poi_generic(cleaned.lower()) and not looks_postal:
        probable_muni = _infer_municipality(cleaned.lower())
        return PreprocessedPickup(
            cleaned=cleaned,
            query_type=PickupQueryType.POI_REFERENCE,
            probable_municipality=probable_muni,
            original=original,
        )

    probable_muni = _infer_municipality(cleaned.lower())
    query_type = PickupQueryType.ADDRESS if cleaned.strip() else PickupQueryType.AMBIGUOUS

    return PreprocessedPickup(
        cleaned=cleaned,
        query_type=query_type,
        probable_municipality=probable_muni,
        original=original,
    )
