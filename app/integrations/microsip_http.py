"""Servidor HTTP local para recibir eventos de llamada desde MicroSIP.

MicroSIP no tiene UI para comandos: las claves cmdIncomingCall /
cmdCallAnswer / cmdCallEnd se editan a mano en microsip.ini. Además,
MicroSIP pasa el Caller ID como argumento posicional al comando
ejecutado (no acepta variables como %state% o %number%).

Estrategia: MicroSIP ejecuta un .bat wrapper que inserta el Caller ID
en la URL correcta. Expondremos 3 endpoints específicos, uno por evento:

    GET /call/incoming?number=...    -> ringing (solo UI, no limpia)
    GET /call/answered?number=...    -> confirmed (limpia + arranca escucha)
    GET /call/ended?number=...       -> disconnected (detiene, mantiene datos)

Además, mantenemos el endpoint legacy para pruebas manuales desde navegador:

    GET /call?state=confirmed&dir=incoming&number=...

El servidor corre en un hilo daemon y expone una QObject con señales Qt
(thread-safe) que MainWindow conecta.
"""
from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8733

# Ventana de deduplicación: ignorar el mismo (state,dir) si vuelve antes de esto.
_DEDUP_WINDOW_SECONDS = 1.5


class CallEventBridge(QObject):
    """Puente thread-safe entre el hilo HTTP y la UI de Qt.

    Las señales de Qt son seguras entre hilos (Qt::QueuedConnection por
    defecto cuando origen y destino viven en hilos distintos), así que
    MainWindow puede conectar sus slots a estas señales sin tocar la UI
    desde el hilo del servidor.
    """

    ringing = Signal(str)       # number
    confirmed = Signal(str)     # number
    disconnected = Signal(str)  # number

    def __init__(self) -> None:
        super().__init__()
        self._last_key: tuple[str, str] | None = None
        self._last_ts: float = 0.0
        self._lock = threading.Lock()

    def dispatch(self, state: str, direction: str, number: str) -> None:
        """Endpoint legacy /call?state=...&dir=...&number=... — usado en pruebas manuales.

        Filtra por dir=incoming, deduplica y reemite como evento interno.
        """
        if direction != "incoming":
            log.debug("microsip: ignorado dir=%s", direction)
            return

        # Mapear state legacy -> event interno
        mapping = {
            "ringing": "incoming",
            "confirmed": "answered",
            "disconnected": "ended",
        }
        event = mapping.get(state)
        if event is None:
            log.debug("microsip: estado legacy no manejado %s", state)
            return
        self.dispatch_event(event, number)

    def dispatch_event(self, event: str, number: str) -> None:
        """Endpoint nuevo /call/{incoming|answered|ended}?number=...

        Llamado desde los .bat que MicroSIP ejecuta en cmdIncomingCall /
        cmdCallAnswer / cmdCallEnd. No requiere filtro por dirección: las
        claves cmd* de MicroSIP ya son específicas de entrantes.
        """
        key = ("event", event)
        now = time.monotonic()
        with self._lock:
            if self._last_key == key and (now - self._last_ts) < _DEDUP_WINDOW_SECONDS:
                log.debug("microsip: dedup %s", event)
                return
            self._last_key = key
            self._last_ts = now

        log.info("microsip: evento=%s number=%s", event, number or "-")
        if event == "incoming":
            self.ringing.emit(number)
        elif event == "answered":
            self.confirmed.emit(number)
        elif event == "ended":
            self.disconnected.emit(number)
        else:
            log.debug("microsip: evento no manejado %s", event)


def _make_handler(bridge: CallEventBridge) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            log.debug("microsip-http: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            qs = parse_qs(parsed.query)
            number = (qs.get("number", [""])[0] or "").strip()

            try:
                if path == "/call":
                    # Legacy: /call?state=...&dir=...&number=...
                    state = (qs.get("state", [""])[0] or "").strip().lower()
                    direction = (qs.get("dir", [""])[0] or "").strip().lower()
                    bridge.dispatch(state, direction, number)
                elif path in ("/call/incoming", "/call/answered", "/call/ended"):
                    event = path.rsplit("/", 1)[1]
                    bridge.dispatch_event(event, number)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
            except Exception:
                log.exception("microsip: dispatch error")

            self.send_response(204)
            self.end_headers()

    return _Handler


def start_server(bridge: CallEventBridge) -> HTTPServer | None:
    """Arranca el servidor HTTP en un hilo daemon. Devuelve el server o None."""
    try:
        server = HTTPServer((HOST, PORT), _make_handler(bridge))
    except OSError as exc:
        log.error("microsip: no se pudo abrir %s:%s (%s)", HOST, PORT, exc)
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name="microsip-http",
        daemon=True,
    )
    thread.start()
    log.info("microsip: servidor HTTP escuchando en http://%s:%s/call", HOST, PORT)
    return server
