"""Tabla local de aliases de recogida frecuentes del negocio.

Objetivo:
- Resolver POIs, hoteles, clínicas, CAPs y puntos recurrentes del AMB
- Corregir alias coloquiales o STT deformado antes del geocoding
- No sustituir scoring global: esto es una capa de memoria local del negocio

Importante:
- NO importamos PickupQueryType para evitar circular import.
- query_type se guarda como string y pickup_preprocessor lo convierte a Enum.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.geo.amb_municipalities import _norm


@dataclass(frozen=True)
class KnownPickupAlias:
    canonical_query: str
    municipality: Optional[str]
    query_type: str               # "poi_reference" | "transit_hub" | "address"
    confidence: str = "high"      # "high" | "medium"
    notes: str = ""


# ---------------------------------------------------------------------
# ALIASES EXPLÍCITOS
# Clave = texto normalizado que puede venir del LLM/STT
# ---------------------------------------------------------------------
KNOWN_PICKUP_ALIASES: dict[str, KnownPickupAlias] = {
    # Hospitales / clínicas / CAPs
    "hospital del mar": KnownPickupAlias(
        canonical_query="Hospital del Mar, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        notes="POI hospitalario frecuente"
    ),
    "clinica san antoni": KnownPickupAlias(
        canonical_query="Clínica Sant Antoni, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
        notes="Nombre bilingüe frecuente; confirmar dirección exacta si se repite"
    ),
    "clinica sant antoni": KnownPickupAlias(
        canonical_query="Clínica Sant Antoni, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "cap sant ildefons": KnownPickupAlias(
        canonical_query="CAP Sant Ildefons, Cornellà de Llobregat",
        municipality="Cornellà de Llobregat",
        query_type="poi_reference",
    ),
    "hospital clinic": KnownPickupAlias(
        canonical_query="Hospital Clínic de Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "hospital bellvitge": KnownPickupAlias(
        canonical_query="Hospital Universitari de Bellvitge, L'Hospitalet de Llobregat",
        municipality="L'Hospitalet de Llobregat",
        query_type="poi_reference",
    ),
    "vall d hebron": KnownPickupAlias(
        canonical_query="Hospital Universitari Vall d'Hebron, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "can ruti": KnownPickupAlias(
        canonical_query="Hospital Universitari Germans Trias i Pujol, Badalona",
        municipality="Badalona",
        query_type="poi_reference",
    ),

    # Hoteles / hostels
    "ga hostel": KnownPickupAlias(
        canonical_query="G.A Hostel, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
        notes="Alias detectado en logs; revisar dirección real cuando se confirme"
    ),
    "g a hostel": KnownPickupAlias(
        canonical_query="G.A Hostel, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
    ),
    "j hostel": KnownPickupAlias(
        canonical_query="J Hostel, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
        notes="Alias ambiguo; mejorar si reaparece"
    ),

    # Recintos / puntos frecuentes
    "foixarda": KnownPickupAlias(
        canonical_query="La Foixarda, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
        notes="Solo útil cuando el contexto NO indica desconocimiento"
    ),
    "centro deportivo foixarda": KnownPickupAlias(
        canonical_query="La Foixarda, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
        confidence="medium",
    ),
    "la maquinista": KnownPickupAlias(
        canonical_query="La Maquinista, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "camp nou": KnownPickupAlias(
        canonical_query="Camp Nou, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "palau sant jordi": KnownPickupAlias(
        canonical_query="Palau Sant Jordi, Barcelona",
        municipality="Barcelona",
        query_type="poi_reference",
    ),
    "gran via 2": KnownPickupAlias(
        canonical_query="Gran Via 2, L'Hospitalet de Llobregat",
        municipality="L'Hospitalet de Llobregat",
        query_type="poi_reference",
    ),
    "fira gran via": KnownPickupAlias(
        canonical_query="Fira Gran Via, L'Hospitalet de Llobregat",
        municipality="L'Hospitalet de Llobregat",
        query_type="poi_reference",
    ),
}


# ---------------------------------------------------------------------
# PATRONES FLEXIBLES
# ---------------------------------------------------------------------
PATTERN_ALIASES: list[tuple[re.Pattern[str], KnownPickupAlias]] = [
    (
        re.compile(r"\bclinica\s+(san|sant)\s+antoni\b", re.IGNORECASE),
        KnownPickupAlias(
            canonical_query="Clínica Sant Antoni, Barcelona",
            municipality="Barcelona",
            query_type="poi_reference",
            confidence="medium",
        ),
    ),
    (
        re.compile(r"\bg\s*\.?\s*a\s*\.?\s*hostel\b", re.IGNORECASE),
        KnownPickupAlias(
            canonical_query="G.A Hostel, Barcelona",
            municipality="Barcelona",
            query_type="poi_reference",
            confidence="medium",
        ),
    ),
    (
        re.compile(r"\bj\s*\.?\s*hostel\b", re.IGNORECASE),
        KnownPickupAlias(
            canonical_query="J Hostel, Barcelona",
            municipality="Barcelona",
            query_type="poi_reference",
            confidence="medium",
        ),
    ),
]


def resolve_known_pickup_alias(raw_text: str) -> Optional[KnownPickupAlias]:
    """Devuelve un alias conocido si encuentra una coincidencia útil."""
    norm = _norm(raw_text)

    # 1) match exacto
    if norm in KNOWN_PICKUP_ALIASES:
        return KNOWN_PICKUP_ALIASES[norm]

    # 2) match por inclusión larga
    for key, alias in sorted(KNOWN_PICKUP_ALIASES.items(), key=lambda x: -len(x[0])):
        if key in norm:
            return alias

    # 3) match por patrón
    for pattern, alias in PATTERN_ALIASES:
        if pattern.search(raw_text):
            return alias

    return None
