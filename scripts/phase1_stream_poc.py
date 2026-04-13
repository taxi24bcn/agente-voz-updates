"""Fase 1 PoC - streaming STT en consola, sin UI.

Captura audio de CABLE Output + microfono del operador, los envia a
gpt-4o-transcribe en chunks silencio-gated, e imprime el texto con etiqueta
[C] / [O] en la consola.

Uso:
    python scripts/phase1_stream_poc.py
    python scripts/phase1_stream_poc.py --duration 120
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# Allow running as a script without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio.capture import DualChannelCapture  # noqa: E402
from app.audio.devices import device_name, resolve_capture_devices  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.stt.realtime_client import RealtimeSTTClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Segundos a grabar (0 = indefinido, Ctrl+C para parar).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        cable_dev, mic_dev = resolve_capture_devices(
            settings.cable_hint, settings.operator_mic_hint
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Cliente  -> [{cable_dev}] {device_name(cable_dev)}")
    print(f"Operador -> [{mic_dev}] {device_name(mic_dev)}")
    print()

    def on_transcript(speaker: str, text: str) -> None:
        tag = "C" if speaker == "cliente" else "O"
        print(f"[{tag}] {text}", flush=True)

    capture = DualChannelCapture(cable_dev, mic_dev)
    capture.start()
    stt = RealtimeSTTClient(
        openai_api_key=settings.openai_api_key,
        cliente_queue=capture.queue_cliente,
        operador_queue=capture.queue_operador,
        on_transcript=on_transcript,
    )
    stt.start()

    print("Streaming. Pulsa Ctrl+C para parar.")
    print("-" * 60)

    stop_flag = {"stop": False}

    def _sigint(_sig, _frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _sigint)

    start = time.monotonic()
    try:
        while not stop_flag["stop"]:
            if args.duration and (time.monotonic() - start) >= args.duration:
                break
            time.sleep(0.1)
    finally:
        print("-" * 60)
        print("Parando...")
        stt.stop()
        capture.stop()
        print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
