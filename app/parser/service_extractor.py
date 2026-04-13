"""Extraccion estructurada via GPT Structured Output + debounce + lock por campo."""
from __future__ import annotations

import copy
import datetime as _dt
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, TYPE_CHECKING

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.config.settings import (
    EXTRACTION_FIRST_RUN_MIN_TOKENS,
    EXTRACTION_LONG_ELAPSED_S,
    EXTRACTION_MIN_INTERVAL_S,
    EXTRACTION_MIN_NEW_TOKENS,
    LLM_MODEL,
)
from app.parser.schema import FIELD_KEYS, SERVICE_JSON_SCHEMA

if TYPE_CHECKING:
    from app.geo.address_normalizer import AddressNormalizer
    from app.geo.pickup_stability import PickupStabilityTracker

log = logging.getLogger(__name__)

_DAY_NAMES_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

SYSTEM_PROMPT = """Eres un asistente que extrae datos de reservas de taxi a partir de la transcripcion de una llamada en espanol entre un CLIENTE y un OPERADOR.

Devuelves UN OBJETO JSON con exactamente estos campos:
- cliente
- telefono
- recogida
- destino
- fecha
- hora
- tipo_servicio
- observaciones

Reglas estrictas generales:
- Si un dato no aparece claramente en la conversacion, escribe "PENDIENTE".
- Si una direccion parece incompleta o dudosa, anade " (REVISAR)" al final del valor.
- NO inventes datos que no esten en la conversacion.
- Si se te indica que hay CAMPOS BLOQUEADOS POR EL OPERADOR, devuelve EXACTAMENTE esos valores para esos campos sin modificarlos.

REGLA CRITICA — Numeros escritos en palabras a digitos:
Convierte SIEMPRE los numeros expresados en palabras a digitos arabes en TODOS los campos antes de escribirlos. Esto es obligatorio para que la geocodificacion y las observaciones funcionen.
Ejemplos:
- "calle Mallorca seiscientos cuarenta" -> "Calle Mallorca 640"
- "numero veintitres" -> "numero 23"
- "nueve y cuarto" -> "09:15"
- "nueve y media" -> "09:30"
- "ocho menos cuarto" -> "07:45"
- "las diez de la noche" -> "22:00"
- "cinco personas" -> "5 personas"
- "dos maletas grandes" -> "2 maletas grandes"
- "tres ninos" -> "3 ninos"
Aplica a recogida, destino, hora, observaciones y cualquier otro campo. Nunca dejes el numero en letras.

FECHA — formato y normalizacion:
Devuelve la fecha en formato DD/MM/YYYY usando la fecha actual que se te pasa en el contexto del usuario.
- "hoy" o "ahora" -> fecha de hoy
- "manana" -> fecha de manana
- "pasado manana" -> fecha de hoy + 2 dias
- "el lunes" / "el martes" ... -> proximo lunes/martes a partir de hoy
- "el dia 15" / "el 15" -> dia 15 del mes en curso (o del proximo mes si ya paso)
- "el 20 de mayo" -> 20/05/<ano correcto>
Si no se menciona fecha en absoluto, devuelve "PENDIENTE".

HORA — formato 24h:
Devuelve la hora siempre en formato HH:MM (24h).
- "ahora" o servicio inmediato -> hora="Ahora"
- "a las nueve" sin especificar -> "09:00" si es maniana, "21:00" si es noche, segun contexto
- "nueve de la manana" -> "09:00"
- "nueve de la noche" -> "21:00"
- "nueve y cuarto" -> "09:15"
- "diez y media" -> "10:30"
- "doce del mediodia" -> "12:00"
- "doce de la noche" / "medianoche" -> "00:00"

TIPO_SERVICIO debe ser uno de: "Inmediato", "Reserva", "Aeropuerto", "Evento", "Otros".
- Si fecha=hoy y hora=Ahora -> "Inmediato"
- Si destino o recogida es aeropuerto, terminal, T1, T2 -> "Aeropuerto"
- Si se menciona congreso, feria, hotel para evento, boda -> "Evento"
- En otros casos con fecha/hora futura -> "Reserva"

Reglas especiales para RECOGIDA:
- Si durante la llamada el cliente o el operador dice que NO sabe la direccion, que va a buscarla y volvera a llamar, o que no conoce el lugar, escribe recogida="DESCONOCIDA". No inventes una direccion aproximada.
- REGLA CRITICA DE CORRECCION: Si aparecen dos posibles direcciones de recogida en la misma llamada, usa SIEMPRE la que quede confirmada en ultimo lugar. Una correccion puede ser:
  * Negacion directa del cliente: "no, es...", "espera, es...", "me refiero a...", "perdona...", "no, no, la direccion es..."
  * Reformulacion positiva sin negacion: "este es de [direccion]", "es de [direccion]", "la calle es [direccion]", "estamos en [direccion]"
  * Precision del operador tras buscar o confirmar: "[operador repite la direccion corregida]"
  * Confirmacion mutua: si el operador dice "perfecto", "de acuerdo", "muy bien", "ok" inmediatamente despues de que se menciona una direccion, esa es la direccion valida — no la mencionada anteriormente.
  * Patron tipico de hotel o empresa: "de la calle X... este es de [nombre del establecimiento], [calle Y] [numero]" — la segunda direccion con nombre de establecimiento es la correcta.
- REGLA DE DESEMPATE: cuando hay ambiguedad entre dos candidatos de recogida, prevalece siempre el confirmado con mayor proximidad al final de la llamada o al cierre de la confirmacion.
- El numero del portal SIEMPRE en digitos, nunca en palabras.
- Los detalles de piso/puerta/escalera (ej: "segundo primera", "piso 3", "puerta B") SI van en observaciones ademas de en recogida.
- Si se menciona "aeropuerto", intenta detectar "Terminal 1" o "Terminal 2" si se dice.
- ATENCION — confusion frecuente STT: la transcripcion puede escribir "cafe" cuando el cliente dijo "calle". Si el contexto es una direccion postal (nombre propio + numero), interpreta "cafe" como "calle". Ejemplo: "cafe Mallorca 403" -> "Calle Mallorca 403". Solo usa "Cafe" o "Cafeteria" si el contexto deja claro que es un establecimiento (ej: "quedamos en el cafe de abajo").

REGLA CRITICA — Combinar CALLE + NUMERO en frases separadas (RECOGIDA y DESTINO):
En llamadas de taxi es MUY frecuente que el cliente diga la calle y, en una frase aparte o en un fragmento posterior, el numero del portal. Debes COMBINAR SIEMPRE ambos elementos en una sola direccion de RECOGIDA o DESTINO cuando pertenezcan claramente al mismo lugar.

Ejemplos:
- "Calle Can Travi" + "Numero cuarenta y tres" -> "Calle Can Travi 43"
- "Mallorca" + "el seiscientos cuarenta" -> "Mallorca 640"
- "Consell de Cent" + "numero 70" -> "Consell de Cent 70"
- "Gran Via" + "el 245" -> "Gran Via 245"
- "Avenida Diagonal" + "el quinientos diez" -> "Avenida Diagonal 510"

Reglas de combinacion:
1. Si aparece una calle o via y despues aparece una frase corta con un numero de portal ("numero 43", "el 43", "portal 43", "bloque 12", "el cuarenta y tres"), asume por defecto que ese numero pertenece a la ULTIMA calle mencionada, salvo que haya una nueva direccion distinta entre medias.
2. Convierte siempre los numeros escritos en palabras a digitos antes de combinarlos.
3. NO dejes el numero suelto en OBSERVACIONES si claramente pertenece a una direccion.
4. Si la calle y el numero van separados en distintos fragmentos de la transcripcion, igualmente debes unirlos en el campo RECOGIDA o DESTINO final.
5. Solo deja la calle sin numero si de verdad no aparece ningun numero asociado en la conversacion.

IMPORTANTE — no confundir numero de portal con otros numeros:
- "puerta 4", "piso 2", "escalera B", "planta 3" NO son el numero de la calle. Van en OBSERVACIONES, no en RECOGIDA.
- "4 personas", "2 maletas", "a las 5" NO son numeros de portal. No los adjuntes a la calle.
- Solo es numero de portal cuando se dice explicitamente "numero X", "portal X", o cuando el numero sigue directamente al nombre de la calle sin otro contexto.

Prioridad: ante la duda entre portal y piso/puerta, usa solo el numero precedido por "numero" o "portal", o el que va pegado a la calle sin calificador.

OBSERVACIONES — que SI poner y que NO:
SI debes poner en observaciones (separados por " | "):
- Numero de pasajeros si es 4 o mas: "5 personas"
- Sillita de bebe / silla de nino: "sillita bebe"
- Mascota / perro / gato: "viaja con perro" / "viaja con mascota"
- Equipaje grande o varias maletas: "2 maletas grandes"
- Silla de ruedas / movilidad reducida / PMR: "silla de ruedas (PMR)"
- Forma de pago si la mencionan explicitamente: "paga con tarjeta", "paga efectivo"
- Cambio de billete grande: "necesita cambio de 50 EUR"
- Piso/puerta/escalera del cliente
- Cualquier indicacion logistica relevante: "esperar 5 minutos", "llamar al llegar", "portal azul"
- Idioma del pasajero si no es espaniol: "habla ingles"

NO debes poner en observaciones NUNCA:
- La direccion de recogida (ya esta en RECOGIDA)
- La direccion de destino (ya esta en DESTINO)
- El nombre del cliente (ya esta en CLIENTE)
- El telefono (ya esta en TELEFONO)
- La fecha o la hora (ya estan en FECHA y HORA)

Si no hay ninguna observacion relevante, escribe "PENDIENTE"."""


@dataclass
class ServiceData:
    # Public fields (shown in UI and exported)
    cliente: str = "PENDIENTE"
    telefono: str = "PENDIENTE"
    recogida: str = "PENDIENTE"
    destino: str = "PENDIENTE"
    fecha: str = "PENDIENTE"
    hora: str = "PENDIENTE"
    tipo_servicio: str = "PENDIENTE"
    observaciones: str = "PENDIENTE"

    # Internal geo fields (not in UI, not in FIELD_KEYS, used for metrics/V2)
    _recogida_raw: str = ""
    _recogida_latlon: "tuple[float, float] | None" = None
    _recogida_place_id: "str | None" = None
    _recogida_partial_match: "bool | None" = None
    _recogida_status: str = "skipped"   # PickupStatus.value
    _recogida_municipio: "str | None" = None
    _geo_google_called: bool = False
    _geo_retry_called: bool = False
    _geo_cache_hit: bool = False
    _geo_operator_edited_pickup: bool = False
    _pickup_repair_correction: bool = False   # se detectó corrección en la llamada
    _pickup_unit_detail: "str | None" = None  # piso/puerta separado antes de geocodificar
    _pickup_type: str = ""                    # PickupQueryType.value

    @classmethod
    def empty(cls) -> "ServiceData":
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceData":
        return cls(**{k: str(data.get(k, "PENDIENTE")) for k in FIELD_KEYS})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in FIELD_KEYS}


class ServiceExtractor:
    """Thread-safe debounced extractor with per-field lock semantics."""

    def __init__(self, openai_api_key: str) -> None:
        # timeout=30.0: connect + read en un solo valor; gpt-4o-mini responde
        # en <5 s en condiciones normales. 30 s da margen sin colgar el worker.
        self._client = OpenAI(api_key=openai_api_key, timeout=30.0)
        self._last_run = 0.0
        self._last_word_count = 0
        self._lock = threading.Lock()
        self._normalizer: "AddressNormalizer | None" = None
        self._stability: "PickupStabilityTracker | None" = None
        self._on_auth_error: Optional[Callable[[], None]] = None

    def set_auth_error_handler(self, handler: Callable[[], None]) -> None:
        self._on_auth_error = handler

    def attach_geocoding(
        self,
        normalizer: "AddressNormalizer",
        stability: "PickupStabilityTracker",
    ) -> None:
        self._normalizer = normalizer
        self._stability = stability

    def should_extract(self, current_word_count: int) -> bool:
        now = time.time()
        with self._lock:
            if self._last_run == 0.0:
                return current_word_count >= EXTRACTION_FIRST_RUN_MIN_TOKENS
            elapsed = now - self._last_run
            if elapsed < EXTRACTION_MIN_INTERVAL_S:
                return False
            new_tokens = current_word_count - self._last_word_count
            return (
                new_tokens >= EXTRACTION_MIN_NEW_TOKENS
                or elapsed >= EXTRACTION_LONG_ELAPSED_S
            )

    def mark_run(self, word_count: int) -> None:
        with self._lock:
            self._last_run = time.time()
            self._last_word_count = word_count

    def extract(
        self,
        transcript: str,
        current_data: ServiceData,
        locked_fields: Iterable[str],
    ) -> ServiceData:
        locked_set = set(locked_fields)
        locked_block = ""
        if locked_set:
            locked_values = {k: getattr(current_data, k) for k in locked_set}
            locked_block = (
                "\n\nCAMPOS BLOQUEADOS POR EL OPERADOR (no modificar): "
                + json.dumps(locked_values, ensure_ascii=False)
            )

        now = _dt.datetime.now()
        date_ctx = (
            f"Fecha actual: {now.strftime('%d/%m/%Y')} ({_DAY_NAMES_ES[now.weekday()]}). "
            f"Hora actual: {now.strftime('%H:%M')}."
        )

        user_content = (
            date_ctx
            + "\n\nTranscripcion de la llamada:\n\n"
            + transcript
            + locked_block
            + "\n\nDevuelve el JSON."
        )

        try:
            resp = self._client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": SERVICE_JSON_SCHEMA,
                },
                temperature=0,
            )
            choice = resp.choices[0] if resp.choices else None
            content = (choice.message.content if choice else None) or "{}"
            payload = json.loads(content)

            # Mantener el estado interno previo. Solo sobrescribir los 8 campos públicos.
            extracted = copy.copy(current_data)
            for field_name in FIELD_KEYS:
                new_val = str(payload.get(field_name, "PENDIENTE"))
                prev_val = getattr(current_data, field_name, "PENDIENTE")
                # No degradar: si la nueva extracción dice PENDIENTE pero el campo
                # ya tenía un valor confirmado, conservar el anterior.
                # Excepción: si el campo está bloqueado, el hard-enforce al final
                # del método lo sobreescribirá de todos modos.
                if new_val == "PENDIENTE" and prev_val not in ("PENDIENTE", ""):
                    setattr(extracted, field_name, prev_val)
                else:
                    setattr(extracted, field_name, new_val)

            # Métrica de lock manual del campo recogida en esta pasada.
            extracted._geo_operator_edited_pickup = "recogida" in locked_set

            if self._normalizer is not None:
                extracted = self._normalizer.normalize_pickup(
                    extracted,
                    transcript=transcript,
                    current_data=current_data,
                    locked_fields=locked_set,
                    stability_tracker=self._stability,
                )

            # Hard-enforce locked fields client-side.
            for field_name in locked_set:
                setattr(extracted, field_name, getattr(current_data, field_name))
            return extracted
        except AuthenticationError:
            # La API key es inválida o fue revocada. No tiene sentido reintentar.
            # Se notifica a la UI a través del callback para evitar matar el worker thread.
            log.error("OpenAI API key inválida — extracción desactivada")
            if self._on_auth_error is not None:
                self._on_auth_error()
            return current_data
        except RateLimitError:
            # Cuota agotada. El siguiente ciclo de debounce reintentará.
            log.warning("Rate limit OpenAI — se mantienen datos previos")
            return current_data
        except APIConnectionError as exc:
            # Red caída o DNS fallido. Transitorio.
            log.warning("Red caída en extracción (%s) — se mantienen datos previos", exc)
            return current_data
        except APIStatusError as exc:
            # 5xx u otros errores HTTP de la API. Transitorio.
            log.warning("Error HTTP %s en extracción — se mantienen datos previos", exc.status_code)
            return current_data
        except json.JSONDecodeError as exc:
            # El modelo devolvió algo que no es JSON válido.
            log.error("JSON inválido en respuesta del extractor: %s", exc)
            return current_data
        except Exception as exc:  # pragma: no cover
            log.exception("extraction error inesperado: %s", exc)
            return current_data
