"""Fase 0 - Validacion de audio VB-CABLE + MicroSIP.

Graba N segundos en paralelo desde el cable virtual (audio del cliente) y
desde el microfono del operador, guarda dos WAVs, envia cada uno a
gpt-4o-transcribe y reporta metricas de nivel y texto transcrito.

Uso basico:
    python scripts/phase0_audio_probe.py
    python scripts/phase0_audio_probe.py --duration 30
    python scripts/phase0_audio_probe.py --list-devices

Requiere un .env con OPENAI_API_KEY (ver .env.example).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from openai import OpenAI

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 1024

DEFAULT_CABLE_HINT = "cable output"
DEFAULT_DURATION = 60
DBFS_OK_THRESHOLD = -40.0

OUTPUT_DIR = Path("logs/phase0")


def find_input_device(hint: str) -> int | None:
    hint_lower = hint.lower()
    for idx, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] <= 0:
            continue
        if hint_lower in info["name"].lower():
            return idx
    return None


def list_input_devices() -> None:
    print("Dispositivos de entrada disponibles:\n")
    for idx, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            print(f"  [{idx:3d}] {info['name']}")


def record_dual(
    cable_dev: int,
    mic_dev: int,
    duration: int,
    cable_out: Path,
    mic_out: Path,
) -> tuple[np.ndarray, np.ndarray]:
    """Graba ambos dispositivos concurrentemente con sus propias callbacks."""
    frames_total = int(duration * SAMPLE_RATE)
    cable_buf = np.zeros((frames_total, CHANNELS), dtype=np.int16)
    mic_buf = np.zeros((frames_total, CHANNELS), dtype=np.int16)
    cable_idx = [0]
    mic_idx = [0]

    def make_callback(buf: np.ndarray, idx: list[int], label: str):
        def callback(indata, frame_count, time_info, status):
            if status:
                print(f"[{label}] audio status: {status}", file=sys.stderr)
            i = idx[0]
            take = min(frame_count, frames_total - i)
            if take > 0:
                buf[i : i + take] = indata[:take]
                idx[0] = i + take

        return callback

    cable_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        device=cable_dev,
        callback=make_callback(cable_buf, cable_idx, "cliente"),
        blocksize=BLOCKSIZE,
    )
    mic_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        device=mic_dev,
        callback=make_callback(mic_buf, mic_idx, "operador"),
        blocksize=BLOCKSIZE,
    )

    with cable_stream, mic_stream:
        start = time.monotonic()
        while cable_idx[0] < frames_total or mic_idx[0] < frames_total:
            if time.monotonic() - start > duration + 2:
                break
            time.sleep(0.05)

    sf.write(cable_out, cable_buf, SAMPLE_RATE, subtype="PCM_16")
    sf.write(mic_out, mic_buf, SAMPLE_RATE, subtype="PCM_16")
    return cable_buf, mic_buf


def rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms == 0.0:
        return float("-inf")
    return 20.0 * float(np.log10(rms / 32768.0))


def transcribe(client: OpenAI, path: Path) -> str:
    with path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f,
            language="es",
        )
    return result.text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fase 0 probe: graba + transcribe cliente/operador."
    )
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    parser.add_argument(
        "--cable-hint",
        default=os.getenv("CABLE_HINT", DEFAULT_CABLE_HINT),
        help='Substring del dispositivo VB-CABLE (por defecto "cable output").',
    )
    parser.add_argument(
        "--mic-hint",
        default=os.getenv("OPERATOR_MIC_HINT", ""),
        help="Substring del mic del operador. Vacio = default del sistema.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista los dispositivos de entrada y sale.",
    )
    parser.add_argument(
        "--skip-transcription",
        action="store_true",
        help="Solo graba y mide nivel; no llama a OpenAI.",
    )
    args = parser.parse_args()

    if args.list_devices:
        list_input_devices()
        return 0

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not args.skip_transcription and not api_key:
        print("ERROR: falta OPENAI_API_KEY en .env", file=sys.stderr)
        print("       copia .env.example a .env y rellenalo.", file=sys.stderr)
        return 1

    cable_dev = find_input_device(args.cable_hint)
    if cable_dev is None:
        print(
            f"ERROR: no se encontro dispositivo de entrada con '{args.cable_hint}'",
            file=sys.stderr,
        )
        list_input_devices()
        return 2

    if args.mic_hint:
        mic_dev = find_input_device(args.mic_hint)
        if mic_dev is None:
            print(f"ERROR: no se encontro mic con '{args.mic_hint}'", file=sys.stderr)
            list_input_devices()
            return 3
    else:
        mic_dev = sd.default.device[0]
        if mic_dev is None or mic_dev < 0:
            print(
                "ERROR: no hay dispositivo de entrada por defecto; usa --mic-hint",
                file=sys.stderr,
            )
            return 4

    cable_name = sd.query_devices(cable_dev)["name"]
    mic_name = sd.query_devices(mic_dev)["name"]

    print("=" * 60)
    print("Fase 0 - Validacion de audio")
    print("=" * 60)
    print(f"Cliente  (CABLE Output) -> [{cable_dev}] {cable_name}")
    print(f"Operador (Microfono)    -> [{mic_dev}] {mic_name}")
    print(f"Duracion: {args.duration} s")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cliente_wav = OUTPUT_DIR / "cliente.wav"
    operador_wav = OUTPUT_DIR / "operador.wav"

    print(f"Grabando {args.duration} s - haz ahora una llamada de prueba.")
    print("Di frases como:")
    print('  "Recogida en Calle Aragon 281"')
    print('  "Destino Terminal 1"')
    print('  "Manana a las cinco y media"')
    print('  "Mi telefono es 622 87 80 40"')
    print()

    start = time.monotonic()
    cable_audio, mic_audio = record_dual(
        cable_dev, mic_dev, args.duration, cliente_wav, operador_wav
    )
    elapsed = time.monotonic() - start
    print(f"Grabacion completada en {elapsed:.1f} s")
    print()

    print("=== Metricas de nivel ===")
    results = {"cliente": cable_audio, "operador": mic_audio}
    for name, audio in results.items():
        db = rms_dbfs(audio)
        if db == float("-inf"):
            status = "SILENCIO (canal muerto)"
        elif db > DBFS_OK_THRESHOLD:
            status = "OK"
        else:
            status = f"BAJO (<{DBFS_OK_THRESHOLD:.0f} dBFS - revisa ganancia)"
        db_str = "-inf" if db == float("-inf") else f"{db:6.1f}"
        print(f"  {name:10s}  RMS: {db_str} dBFS  [{status}]")
    print()

    if args.skip_transcription:
        print("Transcripcion omitida (--skip-transcription).")
        return 0

    print("=== Transcripcion gpt-4o-transcribe ===")
    client = OpenAI(api_key=api_key)
    exit_code = 0
    for name, wav in [("cliente", cliente_wav), ("operador", operador_wav)]:
        print(f"\n--- {name.upper()} ---")
        try:
            text = transcribe(client, wav)
            print(text if text.strip() else "(vacio)")
            (OUTPUT_DIR / f"{name}.txt").write_text(text, encoding="utf-8")
        except Exception as exc:
            print(f"ERROR transcribiendo {name}: {exc}", file=sys.stderr)
            exit_code = 5

    print()
    print(f"Listo. Revisa {OUTPUT_DIR}/ para WAVs y transcripciones.")
    print()
    print("Checklist de aprobacion Fase 0:")
    print("  [ ] Operador oyo al cliente sin eco ni latencia >300 ms")
    print("  [ ] Cliente oyo al operador sin degradacion")
    print("  [ ] Numeros y calles transcritos con >=90% aciertos")
    print("  [ ] RMS cliente y operador ambos > -40 dBFS")
    print("  [ ] Canal cliente sin ruido de escritorio (VB-CABLE aislado)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
