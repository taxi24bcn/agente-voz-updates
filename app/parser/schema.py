"""JSON Schema estricto para los campos del servicio de taxi."""
from __future__ import annotations

FIELD_KEYS = [
    "cliente",
    "telefono",
    "recogida",
    "destino",
    "fecha",
    "hora",
    "tipo_servicio",
    "observaciones",
]

FIELD_LABELS = {
    "cliente": "CLIENTE",
    "telefono": "TELEFONO",
    "recogida": "RECOGIDA",
    "destino": "DESTINO",
    "fecha": "FECHA",
    "hora": "HORA",
    "tipo_servicio": "TIPO DE SERVICIO",
    "observaciones": "OBSERVACIONES",
}

# OpenAI Structured Outputs schema (strict).
SERVICE_JSON_SCHEMA = {
    "name": "service_data",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": FIELD_KEYS,
        "properties": {
            "cliente": {"type": "string"},
            "telefono": {"type": "string"},
            "recogida": {"type": "string"},
            "destino": {"type": "string"},
            "fecha": {"type": "string"},
            "hora": {"type": "string"},
            "tipo_servicio": {"type": "string"},
            "observaciones": {"type": "string"},
        },
    },
}
