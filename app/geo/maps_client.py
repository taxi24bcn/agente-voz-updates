"""Cliente thin para la Google Maps Geocoding API.

Mejoras:
- no se queda ciegamente con results[0]
- puntúa varios candidatos y prioriza municipios AMB
- intenta inferir municipio también desde formatted_address
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from app.geo.amb_municipalities import normalize_municipality, _norm as _norm_muni

log = logging.getLogger(__name__)

_BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TIMEOUT_S = 3.0

_STOPWORDS = {
    "calle", "carrer", "avenida", "avinguda", "paseo", "passeig", "plaza", "plaça",
    "numero", "número", "espana", "españa", "del", "de", "la", "el",
    "en", "con", "entre",
}


@dataclass
class GeoResult:
    formatted_address: str
    lat: float
    lon: float
    place_id: str
    partial_match: bool
    municipality: Optional[str]
    raw_status: str


def _extract_municipality(components: list[dict], formatted_address: str = "") -> Optional[str]:
    for target_type in (
        "locality",
        "postal_town",
        "administrative_area_level_4",
        "administrative_area_level_3",
        "administrative_area_level_2",
    ):
        for comp in components:
            if target_type in comp.get("types", []):
                name = comp.get("long_name")
                if name:
                    return name

    if formatted_address:
        parts = [p.strip() for p in formatted_address.split(",") if p.strip()]
        for part in parts:
            muni = normalize_municipality(part)
            if muni:
                return muni
    return None


def _tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-záéíóúüñç0-9']+", text.lower())
        if len(tok) >= 4 and tok not in _STOPWORDS
    }


def _extract_mentioned_municipality(query: str) -> Optional[str]:
    """Detecta si la query menciona explícitamente un municipio AMB.

    Usa el mismo mecanismo que el preprocessor para ser consistente.
    Itera sobre alias de mayor a menor longitud para evitar matches parciales
    (p.ej. "cornella" dentro de "cornella de llobregat").
    """
    from app.geo.amb_municipalities import _ALIAS_NORM, _AMB_NORM
    norm_q = re.sub(r"[,\.;:/\-]+", " ", _norm_muni(query))
    norm_q = " " + re.sub(r"\s+", " ", norm_q).strip() + " "

    # Iterar de más largo a más corto para evitar falsos positivos
    candidates: list[tuple[str, str]] = []
    for alias_norm, official in _ALIAS_NORM.items():
        if f" {alias_norm} " in norm_q or norm_q.strip().endswith(alias_norm):
            candidates.append((alias_norm, official))
    for muni_norm, official in _AMB_NORM.items():
        if f" {muni_norm} " in norm_q or norm_q.strip().endswith(muni_norm):
            candidates.append((muni_norm, official))

    if not candidates:
        return None
    # El match más largo es el más específico
    candidates.sort(key=lambda x: -len(x[0]))
    return candidates[0][1]


def _score_candidate(query: str, result: GeoResult) -> float:
    query_tokens = _tokens(query)
    result_tokens = _tokens(result.formatted_address)
    overlap = len(query_tokens & result_tokens)
    ratio = overlap / max(1, len(query_tokens))

    src_num = re.search(r"\b(\d{1,4})\b", query)
    dst_num = re.search(r"\b(\d{1,4})\b", result.formatted_address)
    number_bonus = 0.0
    if src_num and dst_num and src_num.group(1) == dst_num.group(1):
        number_bonus = 0.8
    elif src_num and not dst_num:
        number_bonus = -0.4

    in_amb = normalize_municipality(result.municipality or "") is not None

    # Bonus adicional cuando el municipio mencionado en la query coincide
    # con el municipio del resultado. Evita que un municipio AMB erróneo
    # (p.ej. Cornellà) gane sobre Barcelona cuando el usuario dijo "Barcelona".
    mentioned_muni = _extract_mentioned_municipality(query)
    result_muni_official = normalize_municipality(result.municipality or "")
    muni_match_bonus = 0.0
    if mentioned_muni and result_muni_official:
        if mentioned_muni == result_muni_official:
            muni_match_bonus = 2.0
        else:
            # El usuario mencionó un municipio distinto al del resultado → penalizar
            muni_match_bonus = -1.5

    return (
        (2.5 if in_amb else 0.0)
        + (1.0 if not result.partial_match else 0.0)
        + (ratio * 3.0)
        + number_bonus
        + muni_match_bonus
    )


class MapsClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("MapsClient requiere una API key no vacía")
        self._api_key = api_key

    def geocode(self, address: str) -> Optional[GeoResult]:
        params = urllib.parse.urlencode({
            "address": address,
            "language": "es",
            "region": "es",
            "components": "country:ES",
            "key": self._api_key,
        })
        url = f"{_BASE_URL}?{params}"

        log.debug("geocoding address=%r", address)

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            log.warning("Maps HTTP error %s for address=%r", exc.code, address)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("Maps network error for address=%r: %s", address, exc)
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("Maps JSON parse error for address=%r: %s", address, exc)
            return None

        status = data.get("status", "UNKNOWN")
        if status != "OK":
            log.info("Maps status=%s for address=%r", status, address)
            return None

        results = data.get("results", [])
        if not results:
            return None

        candidates: list[GeoResult] = []
        for result in results[:5]:
            try:
                location = result["geometry"]["location"]
                formatted = result.get("formatted_address", "")
                components = result.get("address_components", [])
                candidate = GeoResult(
                    formatted_address=formatted,
                    lat=float(location["lat"]),
                    lon=float(location["lng"]),
                    place_id=result.get("place_id", ""),
                    partial_match=result.get("partial_match", False),
                    municipality=_extract_municipality(components, formatted),
                    raw_status=status,
                )
                candidates.append(candidate)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Maps response parse error for address=%r: %s", address, exc)
                continue

        if not candidates:
            return None

        best = max(candidates, key=lambda item: _score_candidate(address, item))
        return best
