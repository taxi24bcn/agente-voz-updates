"""Deteccion de dispositivos de audio por nombre (case-insensitive)."""
from __future__ import annotations

import sounddevice as sd


def find_input_device(hint: str) -> int | None:
    """Return index of the first input device whose name contains `hint`."""
    if not hint:
        return None
    hint_lower = hint.lower()
    for idx, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] <= 0:
            continue
        if hint_lower in info["name"].lower():
            return idx
    return None


def default_input_device() -> int | None:
    dev = sd.default.device[0]
    if dev is None or dev < 0:
        return None
    return int(dev)


def device_name(index: int) -> str:
    return sd.query_devices(index)["name"]


def list_input_devices() -> list[tuple[int, str]]:
    return [
        (idx, info["name"])
        for idx, info in enumerate(sd.query_devices())
        if info["max_input_channels"] > 0
    ]


def resolve_capture_devices(cable_hint: str, mic_hint: str) -> tuple[int, int]:
    """Return (cable_device_index, mic_device_index) or raise RuntimeError."""
    cable_dev = find_input_device(cable_hint)
    if cable_dev is None:
        raise RuntimeError(
            f"No se encontro dispositivo de entrada con '{cable_hint}'. "
            "Verifica que VB-CABLE este instalado y que MicroSIP este enviando "
            "el audio a CABLE Input."
        )
    if mic_hint:
        mic_dev = find_input_device(mic_hint)
        if mic_dev is None:
            raise RuntimeError(
                f"No se encontro dispositivo de entrada con '{mic_hint}'. "
                "Revisa OPERATOR_MIC_HINT en .env."
            )
    else:
        mic_dev = default_input_device()
        if mic_dev is None:
            raise RuntimeError(
                "No hay dispositivo de entrada por defecto del sistema. "
                "Define OPERATOR_MIC_HINT en .env."
            )
    if mic_dev == cable_dev:
        raise RuntimeError(
            "El microfono del operador y CABLE Output son el mismo dispositivo. "
            "Define OPERATOR_MIC_HINT en .env para diferenciarlos."
        )
    return cable_dev, mic_dev
