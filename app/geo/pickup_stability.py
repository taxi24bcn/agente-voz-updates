"""Tracker de estabilidad del valor de RECOGIDA.

Decide cuándo el campo RECOGIDA lleva suficiente tiempo sin cambiar como para
que valga la pena llamar a Google Maps (las direcciones completas geocodifican
mucho mejor que cadenas parciales que aún están siendo dictadas).

Uso:
    tracker = PickupStabilityTracker(stable_seconds=3.0)
    tracker.observe(data.recogida)   # llamar en cada ciclo de extracción
    if tracker.is_stable():
        normalizer.normalize_pickup(data, locked_fields, tracker)
"""
from __future__ import annotations

import time


class PickupStabilityTracker:
    """Detecta cuando el valor de RECOGIDA no ha cambiado en `stable_seconds`."""

    def __init__(self, stable_seconds: float = 3.0) -> None:
        self._stable_seconds = stable_seconds
        self._last_value: str = ""
        self._last_changed_at: float = 0.0

    def observe(self, current_value: str) -> None:
        """Registrar el valor actual de RECOGIDA.

        Debe llamarse en cada ciclo de extracción (desde ServiceExtractor.extract).
        """
        if current_value != self._last_value:
            self._last_value = current_value
            self._last_changed_at = time.monotonic()

    def is_stable(self) -> bool:
        """True si el valor no ha cambiado en los últimos `stable_seconds`.

        Devuelve False si nunca se ha observado ningún valor.
        """
        if self._last_changed_at == 0.0:
            return False
        elapsed = time.monotonic() - self._last_changed_at
        return elapsed >= self._stable_seconds

    def force_stable(self) -> None:
        """Marca el estado actual como estable inmediatamente.

        Útil al finalizar la llamada para que normalize_pickup_now()
        encuentre un estado coherente. En realidad, normalize_pickup_now()
        hace bypass del tracker, así que este método solo actualiza
        el timestamp para coherencia de logs.
        """
        self._last_changed_at = time.monotonic() - self._stable_seconds - 1.0

    def reset(self) -> None:
        """Reinicia el tracker. Llamar al hacer 'Limpiar (nueva llamada)'."""
        self._last_value = ""
        self._last_changed_at = 0.0
