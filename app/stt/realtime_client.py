"""STT chunked near-realtime con OpenAI gpt-4o-transcribe.

Por cada canal (cliente, operador) corre un ChannelWorker que:
  1. Lee frames de 20 ms desde la queue del canal.
  2. Acumula en un buffer y mide RMS para detectar pausas de voz.
  3. Al detectar silencio prolongado (>=600 ms) con buffer >=1 s, o al llegar
     al limite de MAX_BUFFER_SECONDS, envia el buffer a gpt-4o-transcribe.
  4. Llama al callback `on_transcript(speaker, text)` con el texto resultante.

Se usa chunked + silence-gating en vez de Realtime WebSocket por robustez:
cero gestion de sesion WS, reintentos triviales, latencia 2-5 s por frase
(suficiente para el flujo copy/paste del operador).
"""
from __future__ import annotations

import io
import logging
import queue
import threading
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from httpx import Timeout
from openai import OpenAI

from app.config.settings import (
    LLM_LANGUAGE,
    MAX_BUFFER_SECONDS,
    MIN_FLUSH_SECONDS,
    SAMPLE_RATE,
    SILENCE_DBFS_THRESHOLD,
    SILENCE_FRAMES_TO_FLUSH,
    STT_MODEL,
)

log = logging.getLogger(__name__)

TranscriptCallback = Callable[[str, str], None]  # (speaker, text)
ErrorCallback = Callable[[str, str], None]       # (speaker, error_message)


def _rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms == 0.0:
        return float("-inf")
    return 20.0 * float(np.log10(rms / 32768.0))


def _audio_to_wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class ChannelWorker(threading.Thread):
    """Worker thread per channel: buffers audio and flushes to OpenAI."""

    def __init__(
        self,
        speaker: str,
        frame_queue: "queue.Queue[np.ndarray]",
        client: OpenAI,
        on_transcript: TranscriptCallback,
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        super().__init__(daemon=True, name=f"stt-{speaker}")
        self.speaker = speaker
        self.frame_queue = frame_queue
        self.client = client
        self.on_transcript = on_transcript
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._consecutive_errors = 0

    def stop(self) -> None:
        self._stop_event.set()

    def _flush(self, buffer: list[np.ndarray]) -> None:
        if not buffer:
            return
        audio = np.concatenate(buffer, axis=0)
        # Guard: if the whole chunk is below the silence floor, skip the API call.
        if _rms_dbfs(audio) < SILENCE_DBFS_THRESHOLD - 6:
            return
        try:
            wav_bytes = _audio_to_wav_bytes(audio)
            result = self.client.audio.transcriptions.create(
                model=STT_MODEL,
                file=(f"{self.speaker}.wav", wav_bytes, "audio/wav"),
                language=LLM_LANGUAGE,
            )
            text = (getattr(result, "text", "") or "").strip()
            # Strip zero-width and other non-printable Unicode chars OpenAI
            # occasionally returns, which break cp1252 console output on Windows.
            text = "".join(ch for ch in text if ch.isprintable())
            # Guard: no entregar el transcript si stop() ya fue llamado.
            # Evita callbacks tardíos sobre un transcript/UI que pertenece
            # a la llamada anterior.
            if text and not self._stop_event.is_set():
                self._consecutive_errors = 0
                self.on_transcript(self.speaker, text)
        except Exception as exc:  # pragma: no cover
            self._consecutive_errors += 1
            log.exception(
                "STT flush error (%s) [consecutive=%d]: %s",
                self.speaker,
                self._consecutive_errors,
                exc,
            )
            # Notificar a la UI solo si el canal sigue activo.
            if self.on_error is not None and not self._stop_event.is_set():
                self.on_error(self.speaker, str(exc))

    def run(self) -> None:
        buffer: list[np.ndarray] = []
        silent_frames = 0
        total_samples = 0

        while not self._stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            buffer.append(frame)
            total_samples += frame.shape[0]

            if _rms_dbfs(frame) < SILENCE_DBFS_THRESHOLD:
                silent_frames += 1
            else:
                silent_frames = 0

            total_seconds = total_samples / SAMPLE_RATE
            should_flush = (
                silent_frames >= SILENCE_FRAMES_TO_FLUSH
                and total_seconds >= MIN_FLUSH_SECONDS
            ) or total_seconds >= MAX_BUFFER_SECONDS

            if should_flush:
                self._flush(buffer)
                buffer = []
                silent_frames = 0
                total_samples = 0

        # Final flush on shutdown
        if buffer:
            self._flush(buffer)


class RealtimeSTTClient:
    """Facade that runs one ChannelWorker per channel."""

    def __init__(
        self,
        openai_api_key: str,
        cliente_queue: "queue.Queue[np.ndarray]",
        operador_queue: "queue.Queue[np.ndarray]",
        on_transcript: TranscriptCallback,
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        # Timeout fino: connect=5 s para detectar red caída rápido;
        # read=20 s da margen ante picos de latencia de gpt-4o-transcribe
        # sin bloquear el worker indefinidamente.
        self.client = OpenAI(api_key=openai_api_key, timeout=Timeout(5.0, connect=5.0, read=20.0))
        self.on_transcript = on_transcript
        self._workers = [
            ChannelWorker("cliente", cliente_queue, self.client, on_transcript, on_error),
            ChannelWorker("operador", operador_queue, self.client, on_transcript, on_error),
        ]

    def start(self) -> None:
        for w in self._workers:
            w.start()
        log.info("RealtimeSTTClient started (%d workers)", len(self._workers))

    def stop(self) -> None:
        for w in self._workers:
            w.stop()
        for w in self._workers:
            w.join(timeout=5)
        log.info("RealtimeSTTClient stopped")
