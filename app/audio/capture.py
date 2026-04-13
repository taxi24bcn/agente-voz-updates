"""Captura simultanea de dos streams de audio (cliente + operador).

Cada canal abre su propia `sd.InputStream` con callback independiente que
empuja frames int16 de 20 ms a una queue thread-safe. Las callbacks del
driver de audio no deben bloquearse: solo copian el frame y lo encolan.
Los workers de STT consumen las queues en hilos separados.
"""
from __future__ import annotations

import logging
import queue
from typing import Optional

import numpy as np
import sounddevice as sd

from app.config.settings import BLOCKSIZE, CHANNELS, DTYPE, SAMPLE_RATE

log = logging.getLogger(__name__)

CHANNEL_CLIENTE = "cliente"
CHANNEL_OPERADOR = "operador"


class DualChannelCapture:
    """Owns two parallel input streams and exposes per-channel queues."""

    def __init__(self, cable_device: int, mic_device: int) -> None:
        self.cable_device = cable_device
        self.mic_device = mic_device
        self.queue_cliente: "queue.Queue[np.ndarray]" = queue.Queue()
        self.queue_operador: "queue.Queue[np.ndarray]" = queue.Queue()
        self._cable_stream: Optional[sd.InputStream] = None
        self._mic_stream: Optional[sd.InputStream] = None
        self._running = False

    @staticmethod
    def _make_callback(q: "queue.Queue[np.ndarray]", label: str):
        def callback(indata, frame_count, time_info, status):  # noqa: ARG001
            if status:
                log.warning("audio status [%s]: %s", label, status)
            q.put(indata.copy())

        return callback

    def start(self) -> None:
        if self._running:
            return
        self._cable_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            device=self.cable_device,
            callback=self._make_callback(self.queue_cliente, CHANNEL_CLIENTE),
            blocksize=BLOCKSIZE,
        )
        self._mic_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            device=self.mic_device,
            callback=self._make_callback(self.queue_operador, CHANNEL_OPERADOR),
            blocksize=BLOCKSIZE,
        )
        self._cable_stream.start()
        self._mic_stream.start()
        self._running = True
        log.info(
            "DualChannelCapture started: cable=%s mic=%s",
            self.cable_device,
            self.mic_device,
        )

    def stop(self) -> None:
        if not self._running:
            return
        for stream in (self._cable_stream, self._mic_stream):
            if stream is None:
                continue
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # pragma: no cover
                log.warning("error closing stream: %s", exc)
        self._cable_stream = None
        self._mic_stream = None
        self._running = False
        log.info("DualChannelCapture stopped")

    def __enter__(self) -> "DualChannelCapture":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
