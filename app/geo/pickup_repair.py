"""Extrae la mejor hipótesis final de RECOGIDA.

Esta versión trabaja con el transcript real de la llamada y no solo con el
campo resumido del LLM.

Fixes:
- corta colas de destino como "para ir al aeropuerto"
- mejora separación de detalles de unidad postal
- evita que frases-pregunta se tomen como dirección
- convierte números en palabras a dígitos (transcript crudo del STT)
- merge limpio: conserva la calle del LLM, inyecta el número del transcript
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Optional


_CORRECTION_MARKERS = [
    r"\bno[,.]?\s+(?:es|era|son|sea)\b",
    r"\bes que\b",
    r"\bperd[oó]na\b",
    r"\bespera[,.]?\b",
    r"\bun momento[,.]?\b",
    r"\bel qu[eé][,.]?\b",
    r"\bah[,.]?\s+(?:ok|vale|perfecto|s[ií])\b",
    r"\btodo junto\b",
    # "ya lo saben" y "es correcto" son confirmaciones, no correcciones.
    # Activaban correction_detected en frases de cierre y sesgaban el scoring
    # hacia los últimos candidatos aunque fueran de despedida o de destino.
    r"\bquiero decir\b",
    r"\bme refiero\b",
    r"\bo sea\b",
    r"\bes decir\b",
]

_INCOMPLETE_PATTERNS = [
    r"\bno lo s[eé]\b",
    r"\bno s[eé] la direcci[oó]n\b",
    r"\bno soy de aqu[ií]\b",
    r"\bno somos de aqu[ií]\b",
    r"\bno tenemos la direcci[oó]n\b",
    r"\bvoy a buscar\b",
    r"\bvuelvo a llamar\b",
    r"\blo busco y llamo\b",
    r"\bno tengo la direcci[oó]n\b",
    r"\bno recuerdo la calle\b",
    r"\bno s[eé] el n[uú]mero\b",
]

# Cortes de cola de destino embebidos en la misma frase de recogida.
# Ej: "Berlín 63-65, Barcelona, para ir al aeropuerto"
_DESTINATION_TAIL_PATTERNS = [
    r"(?:,\s*|\s+)\bpara\s+ir\s+(?:al|a|hacia)\b.*$",
    r"(?:,\s*|\s+)\bpara\s+el\s+aeropuerto\b.*$",
    r"(?:,\s*|\s+)\bcon\s+destino\s+a\b.*$",
    r"(?:,\s*|\s+)\bdestino\b.*$",
    r"(?:,\s*|\s+)\bvoy\s+al\b.*$",
    r"(?:,\s*|\s+)\bque\s+voy\s+al\b.*$",
    # Variantes adicionales frecuentes en llamadas reales
    r"(?:,\s*|\s+)\bpara\s+llegar\s+(?:al|a|hasta)\b.*$",
    r"(?:,\s*|\s+)\bme\s+lleva[s]?\s+(?:al|a|hasta|hacia)\b.*$",
    r"(?:,\s*|\s+)\bhasta\s+(?:el|la|los|las)\b.*$",
    r"(?:,\s*|\s+)\bvoy\s+hacia\b.*$",
    r"(?:,\s*|\s+)\bquiero\s+(?:ir|que\s+me\s+lleves)\b.*$",
]

# Detalles de unidad postal que no deben enviarse a Google Maps.
_UNIT_DETAIL_PATTERNS = [
    # "primera tercera", "segundo primera", etc.
    r"\b(primero|primera|segundo|segunda|tercero|tercera|cuarto|cuarta|quinto|quinta|sexto|sexta|s[eé]ptimo|s[eé]ptima|octavo|octava|noveno|novena|d[eé]cimo|d[eé]cima)\s+(primera?|segunda?|tercera?|cuarta?|quinta?|derecha|izquierda|centro)\b",
    # "piso 3", "planta 2", "planta baja"
    r"\b(piso|planta)\s+(\d+|baja|alta|principal)\b",
    # "puerta 3", "puerta A"
    r"\b(puerta|pta\.?)\s*(\d+|[a-zA-Z])\b",
    # "escalera A", "escalera 1"
    r"\b(escalera|esc\.?)\s*(\d+|[a-zA-Z])\b",
    # "bajo izquierda/derecha", "bajos"
    r"\bbajo[s]?\s*(izquierda|derecha|izq\.?|dcha\.?)?\b",
    # "principal", "entresuelo"
    r"\b(entresuelo|entres\.?|principal|pral\.?)\b",
    # "ático"
    r"\b[aá]tico\b",
    # "interior/exterior"
    r"\b(interior|exterior)\b",
]


_PICKUP_START_CUES = [
    "direccion de recogida",
    "dirección de recogida",
    "direccion cuál es",
    "direccion cual es",
    "recogida es",
    "recogida:",
    "mandar un taxi aqui",
    "mandar un taxi aquí",
    "estoy en",
    "en la calle",
    "en calle",
    "era para ver si me pueden mandar un taxi aqui",
    "queria un taxi",
    "quería un taxi",
    "seria para mañana",
    "sería para mañana",
    "seria para esta noche",
    "sería para esta noche",
    "a ver, de",
]

_PICKUP_STOP_CUES = [
    "destino",
    "a donde va",
    "adonde va",
    "para cuando lo necesita",
    "para cuándo lo necesita",
    "cuantas personas",
    "cuántas personas",
    "maletas",
    "numero movil",
    "número móvil",
    "telefono",
    "teléfono",
    "ya le queda reservado",
    "queda programado",
    "le va a llamar el conductor",
    "este mismo del que me llama",
]

_ADDRESS_HINT_RE = re.compile(
    r"\b(?:calle|carrer|carre|avenida|avinguda|avda|av\.?|paseo|passeig|pasaje|passatge|plaza|plaça|via|ronda|carretera|travessera|cam[ií] de|camino)\b",
    re.IGNORECASE,
)
_POI_HINT_RE = re.compile(
    r"\b(?:hospital|clinica|clínica|hotel|cap|ambulatorio|parada de taxi|centro|polideportivo|mercado|estaci[oó]n|estacio)\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "calle", "carrer", "carre", "avenida", "avinguda", "paseo", "passeig",
    "plaza", "placa", "plaça", "numero", "número", "hospitalet", "barcelona",
    "llobregat", "esquina", "entre", "misma", "aqui", "aquí", "esta", "este",
    "con", "del", "de", "la", "el", "en", "y", "pasaje", "passatge",
}

# ---------------------------------------------------------------------------
# Conversión de números escritos en palabras a dígitos
# ---------------------------------------------------------------------------
_ONES = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22,
    "veintidós": 22, "veintitres": 23, "veintitrés": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintiséis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900, "mil": 1000,
}

_NUM_WORDS_RE = re.compile(
    # H + T + y + O  (ej: "doscientos treinta y cinco" → 235)
    r"\b("
    + "|".join(sorted(_HUNDREDS.keys(), key=len, reverse=True))
    + r")\s+("
    + "|".join(sorted(_TENS.keys(), key=len, reverse=True))
    + r")\s+y\s+("
    + "|".join(sorted(_ONES.keys(), key=len, reverse=True))
    + r")\b"
    # H + T  (ej: "doscientos treinta" → 230)
    r"|\b("
    + "|".join(sorted(_HUNDREDS.keys(), key=len, reverse=True))
    + r")\s+("
    + "|".join(sorted(_TENS.keys(), key=len, reverse=True))
    + r")\b"
    # H + O  (ej: "cuatrocientos tres" → 403)
    r"|\b("
    + "|".join(sorted(_HUNDREDS.keys(), key=len, reverse=True))
    + r")\s+("
    + "|".join(sorted(_ONES.keys(), key=len, reverse=True))
    + r")\b"
    # T + y + O  (ej: "treinta y cinco" → 35)
    r"|\b("
    + "|".join(sorted(_TENS.keys(), key=len, reverse=True))
    + r")\s+y\s+("
    + "|".join(sorted(_ONES.keys(), key=len, reverse=True))
    + r")\b"
    # H solo  (ej: "seiscientos" → 600)
    r"|\b("
    + "|".join(sorted(_HUNDREDS.keys(), key=len, reverse=True))
    + r")\b"
    # T solo  (ej: "cuarenta" → 40)
    r"|\b("
    + "|".join(sorted(_TENS.keys(), key=len, reverse=True))
    + r")\b"
    # O solo  (ej: "tres" → 3)
    r"|\b("
    + "|".join(sorted(_ONES.keys(), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _words_to_digits_sub(m: re.Match) -> str:
    # Grupos (índices) según el orden del regex:
    # 0,1,2 → H + T + y + O  (ej: doscientos treinta y cinco → 235)
    # 3,4   → H + T           (ej: doscientos treinta        → 230)
    # 5,6   → H + O           (ej: cuatrocientos tres        → 403)
    # 7,8   → T + y + O       (ej: treinta y cinco           →  35)
    # 9     → H solo          (ej: seiscientos               → 600)
    # 10    → T solo          (ej: cuarenta                  →  40)
    # 11    → O solo          (ej: tres                      →   3)
    g = m.groups()
    if g[0] and g[1] and g[2]:
        return str(_HUNDREDS[g[0].lower()] + _TENS[g[1].lower()] + _ONES[g[2].lower()])
    if g[3] and g[4]:
        return str(_HUNDREDS[g[3].lower()] + _TENS[g[4].lower()])
    if g[5] and g[6]:
        return str(_HUNDREDS[g[5].lower()] + _ONES[g[6].lower()])
    if g[7] and g[8]:
        return str(_TENS[g[7].lower()] + _ONES[g[8].lower()])
    if g[9]:
        return str(_HUNDREDS[g[9].lower()])
    if g[10]:
        return str(_TENS[g[10].lower()])
    if g[11]:
        return str(_ONES[g[11].lower()])
    return m.group(0)


_SPEAKER_TAG_RE = re.compile(r"\[[CO]\]\s*", re.IGNORECASE)

_ADDRESS_START_RE = re.compile(
    r"(?:calle|carrer|carre|avenida|avinguda|avda|paseo|passeig|"
    r"plaza|pla[cç]a|placa|via\s|ronda\s|carretera|travessera|camino\s|"
    r"(?:en\s+(?:la\s+)?)?(?:calle|carrer)|(?:desde\s+(?:la\s+)?)?(?:calle|carrer))",
    re.IGNORECASE,
)

# Tipos de vía al inicio para _street_core_tokens_from_llm
_STREET_TYPE_PREFIX_RE = re.compile(
    r"^(calle|carrer(?:\s+de)?|avenida|avda\.?|avinguda|"
    r"paseo|passeig|pasaje|passatge|plaza|pla[cç]a|placa|"
    r"travessera|ronda|carretera|camino)\s+",
    re.IGNORECASE,
)

_STOPWORDS_MERGE = {
    "de", "del", "la", "las", "el", "los", "les", "els", "d", "en", "y", "i",
}


# ---------------------------------------------------------------------------
# Helpers de limpieza
# ---------------------------------------------------------------------------

def _strip_speaker_tags(text: str) -> str:
    text = text or ""
    return re.sub(r"\s+", " ", _SPEAKER_TAG_RE.sub("", text)).strip()


def _norm_text(value: str) -> str:
    """Normalización para comparación: minúsculas, sin acentos, sin puntuación."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _has_house_number(text: str) -> bool:
    return bool(re.search(r"\b\d{1,4}[A-Za-z]?\b", text or ""))


def _extract_address_fragment(text: str) -> str:
    """Extrae el fragmento de dirección de una frase conversacional.

    "[C] Quiero coger un taxi desde la calle Valencia 35..."
    → "calle Valencia 35..."
    """
    clean = _strip_speaker_tags(text)
    m = _ADDRESS_START_RE.search(clean)
    if m:
        fragment = clean[m.start():]
        fragment = re.sub(
            r"^(?:desde\s+(?:la\s+)?|en\s+(?:la\s+)?)",
            "",
            fragment,
            flags=re.IGNORECASE,
        )
        return fragment.strip()
    return clean


def _convert_number_words(text: str) -> str:
    """Convierte solo los números en palabras a dígitos, sin tocar el resto."""
    text = re.sub(r"\b(n[uú]mero|n[uú]m|portal)\s+(?=\w)", "", text, flags=re.IGNORECASE)
    text = _NUM_WORDS_RE.sub(_words_to_digits_sub, text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# Merge limpio: conserva la calle del LLM, inyecta el número del transcript
# ---------------------------------------------------------------------------

def _street_core_tokens_from_llm(llm_pickup: str) -> list[str]:
    """Extrae tokens significativos del nombre de calle del LLM.

    "Calle de Valencia, Barcelona" → ["valencia"]
    "Calle Can Travi, Barcelona"   → ["travi"]   (o ["can","travi"] si cortos)
    """
    main = (llm_pickup or "").split(",")[0].strip()
    main = re.sub(r"\b\d{1,4}[A-Za-z]?\b", "", main)  # quitar número si ya tiene
    main = _norm_text(main)
    main = _STREET_TYPE_PREFIX_RE.sub("", main)  # quitar tipo de vía

    tokens = [t for t in main.split() if t and t not in _STOPWORDS_MERGE and len(t) >= 3]
    if not tokens:
        return []

    # Para calles largas, usar primer token + últimos 2 evita falsos positivos
    if len(tokens) <= 3:
        return tokens
    reduced = [tokens[0]] + tokens[-2:]
    out: list[str] = []
    for t in reduced:
        if t not in out:
            out.append(t)
    return out


def _make_transcript_windows(transcript: str) -> list[str]:
    """Genera ventanas de 1, 2 y 3 líneas del transcript para buscar el número."""
    raw_lines = [line.strip() for line in (transcript or "").splitlines() if line.strip()]
    lines = [_strip_speaker_tags(line) for line in raw_lines if _strip_speaker_tags(line)]

    windows: list[str] = []
    for i in range(len(lines)):
        for size in (1, 2, 3):
            if i + size <= len(lines):
                windows.append(" ".join(lines[i:i + size]))
    return windows


def _window_matches_street(window: str, street_tokens: list[str]) -> bool:
    if not window or not street_tokens:
        return False
    window_norm = set(_norm_text(window).split())
    hits = sum(1 for t in street_tokens if t in window_norm)
    if len(street_tokens) == 1:
        return hits == 1
    if len(street_tokens) == 2:
        return hits == 2
    return hits >= 2


def _extract_house_number_from_window(window: str) -> Optional[str]:
    """Extrae el número postal priorizando patrones de portal reales.

    Orden de prioridad:
    1) número explícito: "número 35", "num 35", "portal 35"
    2) número justo detrás del nombre de calle
    3) fallback prudente excluyendo horas, personas, piso, puerta, teléfonos
    """
    fragment = _extract_address_fragment(window)
    fragment = _convert_number_words(fragment)
    low = fragment.lower()

    # 0) Corte rápido: contexto de teléfono
    if re.search(r"\b(?:tel[eé]fono|m[oó]vil|llamarme)\b", low):
        return None

    # 1) Marcador explícito de portal
    m = re.search(
        r"\b(?:n(?:u|ú)mero|num\.?|n[oº]\.?|portal)\s+(\d{1,4}[A-Za-z]?)\b",
        fragment,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # 2) Número inmediatamente detrás del tipo de vía + nombre
    m = re.search(
        r"\b(?:calle|carrer|carre|avenida|avinguda|avda|av\.?|paseo|passeig|"
        r"plaza|pla[cç]a|placa|pasaje|passatge|carretera|ronda|travessera)\b"
        r"(?:\s+de)?(?:\s+[A-Za-z\u00C0-\u024F'·-]+){1,5}\s+(\d{1,4}[A-Za-z]?)\b",
        fragment,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # 3) Fallback prudente: primer número que no esté en contexto prohibido
    for m in re.finditer(r"\b(\d{1,4}[A-Za-z]?)\b", fragment):
        num = m.group(1)
        start = max(0, m.start() - 24)
        end = min(len(fragment), m.end() + 24)
        ctx = fragment[start:end].lower()

        # Excluir números de piso/puerta/escalera/personas/maletas
        if re.search(r"\b(?:personas?|maletas?|piso|planta|puerta|escalera|interior|exterior)\b", ctx):
            continue
        # Excluir horas: "a las 5", "las 10"
        if re.search(r"\ba\s+las\b", ctx):
            continue
        # Excluir teléfonos (más de 5 dígitos seguidos)
        if re.search(r"\b\d{6,}\b", ctx):
            continue

        return num

    return None


def _merge_number_into_llm(llm_pickup: str, house_number: str) -> str:
    """Construye la dirección final: calle limpia del LLM + número detectado.

    "Calle de Valencia, Barcelona" + "35" → "Calle de Valencia 35, Barcelona"
    """
    parts = [p.strip() for p in (llm_pickup or "").split(",") if p.strip()]
    if not parts:
        return llm_pickup

    main = parts[0]
    # Eliminar cualquier número viejo que pudiera haber
    main = re.sub(
        r"\s*\b(?:n(?:u|ú)mero|num\.?|n[oº]\.?)?\s*\d{1,4}[A-Za-z]?\b",
        "",
        main,
        flags=re.IGNORECASE,
    )
    main = re.sub(r"\s+", " ", main).strip(" ,")

    merged = f"{main} {house_number}".strip()
    if len(parts) > 1:
        merged = f"{merged}, {', '.join(parts[1:])}"
    return merged


def merge_llm_pickup_with_transcript_number(transcript: str, llm_pickup: str) -> str:
    """Merge principal LLM + número del transcript.

    Regla:
    - Si el LLM ya trae número → no tocar, devolver tal cual.
    - Si el LLM no trae número → buscarlo en el transcript (misma línea,
      línea siguiente, hasta 3 líneas de ventana) cerca del nombre de calle.
    - Si se encuentra → inyectarlo en la calle del LLM (resultado limpio).
    - Si no se encuentra → devolver llm_pickup sin cambios.
    """
    llm_pickup = (llm_pickup or "").strip()
    if not llm_pickup:
        return llm_pickup

    if _has_house_number(llm_pickup):
        return llm_pickup

    street_tokens = _street_core_tokens_from_llm(llm_pickup)
    if not street_tokens:
        return llm_pickup

    windows = _make_transcript_windows(transcript)
    for window in windows:
        if not _window_matches_street(window, street_tokens):
            continue
        number = _extract_house_number_from_window(window)
        if number:
            return _merge_number_into_llm(llm_pickup, number)

    return llm_pickup


# ---------------------------------------------------------------------------
# Dataclass resultado
# ---------------------------------------------------------------------------

@dataclass
class RepairResult:
    address_for_geocoding: str
    unit_detail: Optional[str]
    is_incomplete: bool
    correction_detected: bool
    original: str


# ---------------------------------------------------------------------------
# Helpers internos de pickup_repair (ventana, candidatos, scoring)
# ---------------------------------------------------------------------------

def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_spaces(text)
    normalized = re.sub(r"([\.\!\?])(?=[A-ZÁÉÍÓÚÑ¿¡])", r"\1 ", normalized)
    normalized = re.sub(r"([a-záéíóúñ,])(?=[¿¡])", r"\1 ", normalized)
    parts = re.split(r"(?<=[\.\!\?])\s+", normalized)
    return [p.strip(" ,") for p in parts if p and p.strip(" ,")]


def _significant_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-záéíóúüñç0-9']+", text.lower())
        if len(tok) >= 4 and tok not in _STOPWORDS
    }


def _contains_pickup_cue(text: str) -> bool:
    t = text.lower()
    return any(cue in t for cue in _PICKUP_START_CUES) or bool(_ADDRESS_HINT_RE.search(t)) or bool(_POI_HINT_RE.search(t))


def _contains_stop_cue(text: str) -> bool:
    t = text.lower()
    return any(cue in t for cue in _PICKUP_STOP_CUES)


def _trim_destination_tail(text: str) -> str:
    trimmed = text
    for pattern in _DESTINATION_TAIL_PATTERNS:
        trimmed = re.sub(pattern, "", trimmed, flags=re.IGNORECASE).strip()
    return trimmed.strip(" ,")


def _find_pickup_window(transcript: str, llm_pickup: str) -> str:
    sentences = _split_sentences(transcript)
    if not sentences:
        return llm_pickup.strip()

    llm_tokens = _significant_tokens(llm_pickup)
    collecting = False
    collected: list[str] = []

    for sent in sentences:
        sent_tokens = _significant_tokens(sent)
        overlap = len(llm_tokens & sent_tokens)

        if not collecting:
            if _contains_pickup_cue(sent) or overlap >= 1:
                collecting = True
            else:
                continue

        if _contains_stop_cue(sent) and collected:
            break

        collected.append(sent)

    if not collected:
        return llm_pickup.strip()

    return _normalize_spaces(" ".join(collected))


def _extract_address_candidates(text: str, llm_pickup: str) -> list[str]:
    candidates: list[str] = []
    sentences = _split_sentences(text)
    llm_tokens = _significant_tokens(llm_pickup)

    for sent in sentences:
        sent_clean = sent.strip(" ,")
        if len(sent_clean) < 4:
            continue
        if sent_clean.endswith("?") and not _ADDRESS_HINT_RE.search(sent_clean):
            continue
        has_hint = _contains_pickup_cue(sent_clean)
        overlap = len(_significant_tokens(sent_clean) & llm_tokens)
        if not has_hint and overlap == 0:
            continue
        sent_clean = _trim_destination_tail(sent_clean)
        if len(sent_clean) < 4:
            continue
        candidates.append(sent_clean)

    if not candidates and llm_pickup.strip():
        fallback = _trim_destination_tail(llm_pickup.strip())
        if fallback:
            candidates.append(fallback)
    return candidates


def _choose_best_candidate(candidates: list[str], window: str, llm_pickup: str) -> tuple[str, bool]:
    if not candidates:
        return llm_pickup.strip(), False

    freq = Counter()
    for cand in candidates:
        for tok in _significant_tokens(cand):
            freq[tok] += 1

    correction_detected = False
    correction_positions = [
        m.end()
        for pattern in _CORRECTION_MARKERS
        for m in re.finditer(pattern, window.lower())
    ]
    last_correction_pos = max(correction_positions) if correction_positions else -1
    llm_tokens = _significant_tokens(llm_pickup)

    best_score = float("-inf")
    best_candidate = candidates[-1]

    running_offset = 0
    sentence_offsets: list[tuple[str, int]] = []
    for sent in _split_sentences(window):
        pos = window.lower().find(sent.lower(), running_offset)
        if pos < 0:
            pos = running_offset
        sentence_offsets.append((sent, pos))
        running_offset = pos + len(sent)

    for idx, cand in enumerate(candidates):
        tokens = _significant_tokens(cand)
        score = 0.0
        # Peso reducido: 0.6 → 0.2. Con 0.6 el décimo candidato sumaba +6.0
        # solo por ser el último, superando incluso el ADDRESS_HINT_RE (+2.5).
        # Con 0.2 la posición sigue favoreciendo correcciones tardías pero sin
        # anular los indicadores de calidad del candidato.
        score += idx * 0.2
        score += sum(freq[tok] for tok in tokens)
        if _ADDRESS_HINT_RE.search(cand):
            score += 2.5
        if _POI_HINT_RE.search(cand):
            score += 1.5
        if re.search(r"\b\d{1,4}\b", cand):
            score += 1.0
        score += 0.8 * len(tokens & llm_tokens)

        if last_correction_pos >= 0:
            correction_detected = True
            for sent, pos in sentence_offsets:
                # `cand` pasó por _trim_destination_tail() y strip() en
                # _extract_address_candidates(); `sent` no. Normalizar `sent`
                # antes de comparar para que el bonus de +3.0 realmente dispare
                # cuando el candidato proviene de una frase posterior a la corrección.
                sent_normalized = _trim_destination_tail(sent.strip(" ,"))
                if sent_normalized == cand and pos >= last_correction_pos:
                    score += 3.0
                    break
            if cand.lower() in window.lower()[last_correction_pos:]:
                score += 1.5

        if score >= best_score:
            best_score = score
            best_candidate = cand

    return best_candidate, correction_detected


def _extract_unit_detail(text: str) -> tuple[str, Optional[str]]:
    unit_parts: list[str] = []
    clean = text

    for pattern in _UNIT_DETAIL_PATTERNS:
        while True:
            m = re.search(pattern, clean, re.IGNORECASE)
            if not m:
                break
            unit_parts.append(m.group(0).strip(" ,"))
            clean = (clean[:m.start()] + " " + clean[m.end():]).strip()

    clean = re.sub(r"\s{2,}", " ", clean).strip().rstrip(",. ")
    unit_detail = ", ".join(dict.fromkeys(unit_parts)) if unit_parts else None
    return clean, unit_detail


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def extract_best_pickup_from_transcript(transcript: str, llm_pickup: str) -> RepairResult:
    transcript = transcript or ""
    llm_pickup = (llm_pickup or "").strip()
    original = llm_pickup

    # ── PASO 1: merge número del transcript en la calle del LLM ──────────
    # Esta es la operación PRINCIPAL. Produce una dirección limpia con la
    # calle normalizada del LLM + el número real del transcript.
    # Si el LLM ya trae número, este paso es un no-op.
    merged_pickup = merge_llm_pickup_with_transcript_number(transcript, llm_pickup)
    number_was_injected = merged_pickup != llm_pickup

    # ── PASO 2: comprobar incompleto ──────────────────────────────────────
    window = _find_pickup_window(transcript, llm_pickup)
    window_lower = window.lower()

    if any(re.search(p, window_lower) for p in _INCOMPLETE_PATTERNS):
        return RepairResult(
            address_for_geocoding="DESCONOCIDA",
            unit_detail=None,
            is_incomplete=True,
            correction_detected=False,
            original=original,
        )

    # ── PASO 3: si el merge inyectó número, usarlo directamente ──────────
    # Ya tenemos la dirección más limpia posible: calle del LLM + número.
    # No hace falta buscar candidatos en el transcript (evita ruido conversacional).
    if number_was_injected:
        clean_merged = _trim_destination_tail(merged_pickup)
        clean_merged, unit_detail = _extract_unit_detail(clean_merged)
        if len(clean_merged) >= 5:
            return RepairResult(
                address_for_geocoding=clean_merged,
                unit_detail=unit_detail,
                is_incomplete=False,
                correction_detected=True,  # marcamos que se enriqueció
                original=original,
            )

    # ── PASO 4: lógica original de candidatos del transcript ─────────────
    # Solo llega aquí si el LLM ya tenía número o el merge no encontró nada.
    # Si el LLM ya tenía número, no hay que buscar candidatos en el transcript
    # (evita devolver el transcript crudo con ruido conversacional).
    if _has_house_number(llm_pickup):
        clean = _trim_destination_tail(llm_pickup)
        clean, unit_detail = _extract_unit_detail(clean)
        return RepairResult(
            address_for_geocoding=clean or llm_pickup,
            unit_detail=unit_detail,
            is_incomplete=False,
            correction_detected=False,
            original=original,
        )

    candidates = _extract_address_candidates(window, llm_pickup)
    best_candidate, correction_detected = _choose_best_candidate(candidates, window, llm_pickup)
    best_candidate = _trim_destination_tail(best_candidate)
    # Extraer solo el fragmento de dirección (quita "[C]", texto conversacional)
    best_candidate = _extract_address_fragment(best_candidate)
    clean, unit_detail = _extract_unit_detail(best_candidate)

    if len(clean) < 5:
        clean = _trim_destination_tail(llm_pickup) or "DESCONOCIDA"
        unit_detail = None

    # Guardarraíl final: si el candidato del transcript contiene un teléfono
    # u otro número largo que no es portal, usar el llm_pickup limpio como base.
    if re.search(r"\b\d{6,}\b", clean):
        clean = _trim_destination_tail(llm_pickup)

    return RepairResult(
        address_for_geocoding=clean,
        unit_detail=unit_detail,
        is_incomplete=False,
        correction_detected=correction_detected,
        original=original,
    )


def extract_best_pickup(raw: str) -> RepairResult:
    """Compatibilidad hacia atrás para código antiguo."""
    text = raw.strip()
    if any(re.search(p, text.lower()) for p in _INCOMPLETE_PATTERNS):
        return RepairResult(
            address_for_geocoding="DESCONOCIDA",
            unit_detail=None,
            is_incomplete=True,
            correction_detected=False,
            original=text,
        )

    text = _trim_destination_tail(text)
    clean, unit_detail = _extract_unit_detail(text)
    return RepairResult(
        address_for_geocoding=clean or text,
        unit_detail=unit_detail,
        is_incomplete=False,
        correction_detected=False,
        original=text,
    )
