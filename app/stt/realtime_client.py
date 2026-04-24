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

Protecciones de coste (v2.5.0):
  - Ratio voz/silencio antes del flush: descarta buffers con RMS global
    sobre el umbral pero menos de STT_MIN_VOICED_RATIO de frames con voz
    real (ruido ambiente sostenido).
  - Circuit breaker: ante 429 `insufficient_quota` o `AuthenticationError`
    el worker pasa a estado terminal y deja de llamar a la API. Ante 429
    transitorio / errores de red, pausa con backoff exponencial.
  - Watchdog de inactividad: si durante STT_INACTIVITY_TIMEOUT_S no se
    produce ni un solo transcript, el cliente detiene los workers.
"""
from __future__ import annotations

import io
import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from httpx import Timeout
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.config.settings import (
    LLM_LANGUAGE,
    MAX_BUFFER_SECONDS,
    MIN_FLUSH_SECONDS,
    SAMPLE_RATE,
    SILENCE_DBFS_THRESHOLD,
    SILENCE_FRAMES_TO_FLUSH,
    STT_BACKOFF_SECONDS,
    STT_INACTIVITY_TIMEOUT_S,
    STT_MIN_VOICED_RATIO,
    STT_MODEL,
)

log = logging.getLogger(__name__)

TranscriptCallback = Callable[[str, str], None]  # (speaker, text)
ErrorCallback = Callable[[str, str], None]       # (speaker, error_message)
InactivityCallback = Callable[[], None]


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


def _is_insufficient_quota(exc: RateLimitError) -> bool:
    """Detecta el 429 terminal `insufficient_quota` (saldo agotado).

    No es recuperable reintentando — es distinto de un throttle temporal.
    """
    # openai>=1.x expone el cuerpo en .body (dict) o en str(exc).
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        code = (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else body.get("code")
        if code == "insufficient_quota":
            return True
    return "insufficient_quota" in str(exc)


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
        # Circuit breaker state
        self._terminal = False          # saldo agotado / auth inválida → no reintentar
        self._paused_until = 0.0        # monotonic deadline para reanudar tras 429 transitorio
        self._last_success_ts = time.monotonic()  # para el watchdog de inactividad

    def stop(self) -> None:
        self._stop_event.set()

    def last_success_monotonic(self) -> float:
        """Timestamp (monotonic) del último flush que entregó texto."""
        return self._last_success_ts

    def is_terminal(self) -> bool:
        return self._terminal

    def _backoff_seconds(self) -> float:
        idx = min(self._consecutive_errors - 1, len(STT_BACKOFF_SECONDS) - 1)
        return STT_BACKOFF_SECONDS[max(idx, 0)]

    def _should_flush_voice_ratio(self, buffer: list[np.ndarray]) -> bool:
        """Segundo guard anti-ruido: al menos STT_MIN_VOICED_RATIO de los
        frames del buffer deben estar por encima del umbral de voz.

        Evita pagar por transcribir ruido ambiente sostenido (coche,
        aire acondicionado) cuyo RMS global supera el threshold aunque no
        haya voz real.
        """
        if not buffer:
            return False
        voiced = sum(1 for f in buffer if _rms_dbfs(f) >= SILENCE_DBFS_THRESHOLD)
        ratio = voiced / len(buffer)
        return ratio >= STT_MIN_VOICED_RATIO

    def _flush(self, buffer: list[np.ndarray]) -> None:
        if not buffer:
            return
        audio = np.concatenate(buffer, axis=0)
        # Guard 1: si el audio entero está bajo el umbral de silencio, skip.
        if _rms_dbfs(audio) < SILENCE_DBFS_THRESHOLD - 6:
            return
        # Guard 2 (v2.5.0): ratio de frames con voz real dentro del buffer.
        if not self._should_flush_voice_ratio(buffer):
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
                self._paused_until = 0.0
                self._last_success_ts = time.monotonic()
                self.on_transcript(self.speaker, text)
        except RateLimitError as exc:
            self._handle_rate_limit(exc)
        except AuthenticationError as exc:
            self._enter_terminal("clave OpenAI inválida", exc)
        except (APIConnectionError, APIStatusError) as exc:
            self._handle_transient_error(exc)
        except Exception as exc:  # pragma: no cover
            # Errores inesperados: tratamos como transitorios (backoff corto).
            self._handle_transient_error(exc)

    def _handle_rate_limit(self, exc: RateLimitError) -> None:
        if _is_insufficient_quota(exc):
            self._enter_terminal("saldo OpenAI agotado", exc)
            return
        # 429 transitorio: backoff corto y reintentar.
        self._consecutive_errors += 1
        wait = self._backoff_seconds()
        self._paused_until = time.monotonic() + wait
        log.warning(
            "STT 429 transitorio (%s) [consecutive=%d] — pausa %.0fs",
            self.speaker,
            self._consecutive_errors,
            wait,
        )
        if self.on_error is not None and not self._stop_event.is_set():
            self.on_error(self.speaker, f"rate limit — reintento en {int(wait)}s")

    def _handle_transient_error(self, exc: Exception) -> None:
        self._consecutive_errors += 1
        wait = self._backoff_seconds()
        self._paused_until = time.monotonic() + wait
        log.warning(
            "STT error transitorio (%s) [consecutive=%d] — pausa %.0fs: %s",
            self.speaker,
            self._consecutive_errors,
            wait,
            exc,
        )
        if self.on_error is not None and not self._stop_event.is_set():
            self.on_error(self.speaker, str(exc))

    def _enter_terminal(self, reason: str, exc: Exception) -> None:
        self._terminal = True
        log.error("STT terminal (%s): %s — %s", self.speaker, reason, exc)
        if self.on_error is not None and not self._stop_event.is_set():
            self.on_error(self.speaker, reason)

    def _drain_queue(self) -> None:
        """Vacía la cola de frames. Se invoca durante pausas y en estado
        terminal para evitar que el buffer interno crezca sin límite.
        """
        try:
            while True:
                self.frame_queue.get_nowait()
        except queue.Empty:
            return

    def run(self) -> None:
        buffer: list[np.ndarray] = []
        silent_frames = 0
        total_samples = 0

        while not self._stop_event.is_set():
            # Estado terminal: no volvemos a llamar a la API, solo drenamos.
            if self._terminal:
                self._drain_queue()
                # Espera pasiva hasta que alguien haga stop().
                if self._stop_event.wait(timeout=0.5):
                    break
                continue

            # Pausa por backoff: drenar frames nuevos para no acumular audio
            # que se enviaría de golpe al reanudar (amplificaría el coste).
            if self._paused_until and time.monotonic() < self._paused_until:
                self._drain_queue()
                buffer = []
                silent_frames = 0
                total_samples = 0
                if self._stop_event.wait(timeout=0.2):
                    break
                continue

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

        # Final flush on shutdown (sólo si no estamos en terminal).
        if buffer and not self._terminal:
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
        on_inactivity: Optional[InactivityCallback] = None,
    ) -> None:
        # Timeout fino: connect=5 s para detectar red caída rápido;
        # read=20 s da margen ante picos de latencia de gpt-4o-transcribe
        # sin bloquear el worker indefinidamente.
        self.client = OpenAI(api_key=openai_api_key, timeout=Timeout(5.0, connect=5.0, read=20.0))
        self.on_transcript = on_transcript
        self._on_inactivity = on_inactivity
        self._workers = [
            ChannelWorker("cliente", cliente_queue, self.client, on_transcript, on_error),
            ChannelWorker("operador", operador_queue, self.client, on_transcript, on_error),
        ]
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        for w in self._workers:
            w.start()
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._run_watchdog,
            name="stt-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        log.info("RealtimeSTTClient started (%d workers + watchdog)", len(self._workers))

    def stop(self) -> None:
        self._watchdog_stop.set()
        for w in self._workers:
            w.stop()
        for w in self._workers:
            w.join(timeout=5)
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=2)
        log.info("RealtimeSTTClient stopped")

    def _run_watchdog(self) -> None:
        """Detiene la captura si durante STT_INACTIVITY_TIMEOUT_S ningún
        worker produce transcripción. Cubre el caso raro de que MicroSIP
        crashee sin emitir `ended` o de captura arrancada manualmente y
        olvidada.
        """
        while not self._watchdog_stop.wait(timeout=30.0):
            now = time.monotonic()
            # Si todos los workers están en terminal, el watchdog también cierra.
            if all(w.is_terminal() for w in self._workers):
                log.warning("watchdog: todos los workers en estado terminal — cerrando")
                self._notify_inactivity()
                return
            last = max(w.last_success_monotonic() for w in self._workers)
            if now - last >= STT_INACTIVITY_TIMEOUT_S:
                log.warning(
                    "watchdog: sin transcripción en %.0fs — deteniendo captura",
                    now - last,
                )
                self._notify_inactivity()
                return

    def _notify_inactivity(self) -> None:
        if self._on_inactivity is not None:
            try:
                self._on_inactivity()
            except Exception:
                log.exception("watchdog: on_inactivity callback raised")
