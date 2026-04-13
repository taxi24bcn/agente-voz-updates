"""Whitelist oficial de los 36 municipios del AMB + alias coloquiales.

Fuente: Área Metropolitana de Barcelona (AMB) — 36 municipios metropolitanos.
Usado por AddressNormalizer para validar que el resultado de Google Maps
pertenece al área de operación de Taxi24H.
"""
from __future__ import annotations

import unicodedata


def _norm(s: str) -> str:
    """Lowercase + strip accents para comparación tolerante."""
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


# 36 municipios oficiales del AMB
AMB_MUNICIPALITIES: frozenset[str] = frozenset({
    "Badalona",
    "Badia del Vallès",
    "Barberà del Vallès",
    "Barcelona",
    "Begues",
    "Castellbisbal",
    "Castelldefels",
    "Cerdanyola del Vallès",
    "Cervelló",
    "Corbera de Llobregat",
    "Cornellà de Llobregat",
    "El Papiol",
    "El Prat de Llobregat",
    "Esplugues de Llobregat",
    "Gavà",
    "L'Hospitalet de Llobregat",
    "La Palma de Cervelló",
    "Molins de Rei",
    "Montcada i Reixac",
    "Montgat",
    "Pallejà",
    "Ripollet",
    "Sant Adrià de Besòs",
    "Sant Andreu de la Barca",
    "Sant Boi de Llobregat",
    "Sant Climent de Llobregat",
    "Sant Cugat del Vallès",
    "Sant Feliu de Llobregat",
    "Sant Joan Despí",
    "Sant Just Desvern",
    "Sant Vicenç dels Horts",
    "Santa Coloma de Cervelló",
    "Santa Coloma de Gramenet",
    "Tiana",
    "Torrelles de Llobregat",
    "Viladecans",
})

# Índice normalizado para lookup rápido: norma → nombre oficial
_AMB_NORM: dict[str, str] = {_norm(m): m for m in AMB_MUNICIPALITIES}

# Alias coloquiales → nombre oficial AMB
# Se amplía con casos reales que aparezcan en los logs de producción.
AMB_ALIASES: dict[str, str] = {
    "hospitalet": "L'Hospitalet de Llobregat",
    "l hospitalet": "L'Hospitalet de Llobregat",
    "l'hospitalet": "L'Hospitalet de Llobregat",
    "l hospitalet de llobregat": "L'Hospitalet de Llobregat",
    "el prat": "El Prat de Llobregat",
    "prat": "El Prat de Llobregat",
    "prat de llobregat": "El Prat de Llobregat",
    "santa coloma": "Santa Coloma de Gramenet",
    "santa coloma de gramenet": "Santa Coloma de Gramenet",
    "sant adria": "Sant Adrià de Besòs",
    "sant adrià": "Sant Adrià de Besòs",
    "sant adria de besos": "Sant Adrià de Besòs",
    "cornella": "Cornellà de Llobregat",
    "cornella de llobregat": "Cornellà de Llobregat",
    "sant cugat": "Sant Cugat del Vallès",
    "sant boi": "Sant Boi de Llobregat",
    "sant boi de llobregat": "Sant Boi de Llobregat",
    "montcada": "Montcada i Reixac",
    "montcada i reixac": "Montcada i Reixac",
    "sant just": "Sant Just Desvern",
    "sant joan despi": "Sant Joan Despí",
    "esplugues": "Esplugues de Llobregat",
    "esplugues de llobregat": "Esplugues de Llobregat",
    "molins": "Molins de Rei",
    "molins de rei": "Molins de Rei",
    "cerdanyola": "Cerdanyola del Vallès",
    "barbera": "Barberà del Vallès",
    "badia": "Badia del Vallès",
    "papiol": "El Papiol",
    "palleja": "Pallejà",
    "ripollet": "Ripollet",
    "montgat": "Montgat",
    "tiana": "Tiana",
    "torrelles": "Torrelles de Llobregat",
    "viladecans": "Viladecans",
    "gava": "Gavà",
    "castelldefels": "Castelldefels",
    "corbera": "Corbera de Llobregat",
    "cervello": "Cervelló",
    "sant vicenc dels horts": "Sant Vicenç dels Horts",
    "sant vicenç": "Sant Vicenç dels Horts",
    "sant andreu de la barca": "Sant Andreu de la Barca",
    "sant feliu": "Sant Feliu de Llobregat",
    "sant feliu de llobregat": "Sant Feliu de Llobregat",
    "sant climent": "Sant Climent de Llobregat",
    "la palma": "La Palma de Cervelló",
    "santa coloma de cervello": "Santa Coloma de Cervelló",
}

# Índice normalizado de aliases
_ALIAS_NORM: dict[str, str] = {_norm(k): v for k, v in AMB_ALIASES.items()}


def normalize_municipality(name: str) -> str | None:
    """Devuelve el nombre AMB oficial si 'name' (o su alias) pertenece al AMB.

    Tolerante a mayúsculas, acentos y pequeñas variantes coloquiales.
    Devuelve None si el nombre no pertenece al AMB.
    """
    key = _norm(name.strip())
    # 1. Match directo en la whitelist
    if key in _AMB_NORM:
        return _AMB_NORM[key]
    # 2. Match en alias
    if key in _ALIAS_NORM:
        return _ALIAS_NORM[key]
    # 3. Búsqueda parcial: si el nombre normalizado contiene un municipio AMB
    for muni_norm, muni_official in _AMB_NORM.items():
        if muni_norm in key or key in muni_norm:
            return muni_official
    return None


def is_amb_municipality(name: str) -> bool:
    """True si 'name' corresponde a un municipio del AMB."""
    return normalize_municipality(name) is not None
