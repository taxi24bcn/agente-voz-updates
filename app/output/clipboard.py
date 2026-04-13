"""Utilidades para portapapeles y formateo final del resumen."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication

from app.parser.schema import FIELD_KEYS, FIELD_LABELS
from app.parser.service_extractor import ServiceData


def format_service_text(data: ServiceData) -> str:
    lines = []
    for key in FIELD_KEYS:
        label = FIELD_LABELS[key]
        value = getattr(data, key, "PENDIENTE")
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def copy_to_clipboard(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)
