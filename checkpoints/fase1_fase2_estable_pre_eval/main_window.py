"""Ventana principal del asistente de voz Taxi24H."""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.audio.capture import DualChannelCapture
from app.audio.devices import device_name, resolve_capture_devices
from app.config.settings import PICKUP_GEOCODE_STABLE_SECONDS, Settings
from app.integrations.microsip_http import CallEventBridge
from app.geo.address_normalizer import AddressNormalizer
from app.geo.maps_client import MapsClient
from app.geo.pickup_stability import PickupStabilityTracker
from app.output.clipboard import copy_to_clipboard, format_service_text
from app.output.txt_exporter import save_session
from app.parser.schema import FIELD_KEYS, FIELD_LABELS
from app.parser.service_extractor import ServiceData, ServiceExtractor
from app.stt.realtime_client import RealtimeSTTClient
from app.stt.transcript_buffer import TranscriptBuffer
from app.ui.field_widget import FieldWidget
from app.updater import UpdateChecker, show_update_dialog

log = logging.getLogger(__name__)

# Estilos inline del status label (cambian dinámicamente según el estado de la llamada)
_STATUS_STYLES = {
    "idle": (
        "font-size:12px; font-weight:600; color:#6F655A;"
        " padding:6px 12px; background:#EDEBE5; border-radius:8px;"
    ),
    "active": (
        "font-size:12px; font-weight:600; color:#2A7A50;"
        " padding:6px 12px; background:#E8F4EE; border-radius:8px;"
    ),
    "warning": (
        "font-size:12px; font-weight:600; color:#8A6508;"
        " padding:6px 12px; background:#FFF4D8; border-radius:8px;"
    ),
}


class ExtractionWorker(QObject):
    """Runs ServiceExtractor.extract off the UI thread."""

    result_ready = Signal(object)  # ServiceData

    def __init__(self, extractor: ServiceExtractor) -> None:
        super().__init__()
        self._extractor = extractor

    @Slot(str, object, object)
    def do_extract(
        self,
        transcript: str,
        current_data: ServiceData,
        locked_fields: object,
    ) -> None:
        data = self._extractor.extract(transcript, current_data, locked_fields)
        self.result_ready.emit(data)


class MainWindow(QMainWindow):
    # Queued signals (cross-thread safe)
    _run_extraction = Signal(str, object, object)
    _transcript_appended = Signal(str, str)
    _stt_error = Signal(str, str)  # (speaker, message)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Taxi24H · Asistente de Voz")
        self.resize(960, 620)

        self.transcript_buffer = TranscriptBuffer()
        self.current_data = ServiceData.empty()
        self.locked_fields: set[str] = set()

        self._pending_reset: bool = False
        self._pending_phone: str = ""

        self.extractor = ServiceExtractor(settings.openai_api_key)
        self.capture: DualChannelCapture | None = None
        self.stt_client: RealtimeSTTClient | None = None
        self._normalizer: AddressNormalizer | None = None
        self._stability: PickupStabilityTracker | None = None

        if settings.google_maps_api_key:
            try:
                maps_client = MapsClient(settings.google_maps_api_key)
                self._normalizer = AddressNormalizer(maps_client)
                self._stability = PickupStabilityTracker(PICKUP_GEOCODE_STABLE_SECONDS)
                self.extractor.attach_geocoding(self._normalizer, self._stability)
                log.info("Normalizacion Google Maps: ACTIVADA (solo RECOGIDA)")
            except Exception:
                log.exception("Error al iniciar MapsClient — normalización desactivada")
        else:
            log.info("Normalizacion Google Maps: desactivada (falta GOOGLE_MAPS_API_KEY)")

        self._build_ui()
        self._setup_extraction_thread()

        self._extract_timer = QTimer(self)
        self._extract_timer.setInterval(1500)
        self._extract_timer.timeout.connect(self._maybe_extract)

        self._transcript_appended.connect(self._on_transcript_appended)
        self._stt_error.connect(self._on_stt_error)

        # Comprobador de actualizaciones (hilo daemon, no bloquea la UI)
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(
            lambda ver, url, notes, sha: show_update_dialog(self, ver, url, notes, sha)
        )
        self._update_checker.start()

    def attach_call_bridge(self, bridge: CallEventBridge) -> None:
        bridge.ringing.connect(self._on_call_ringing)
        bridge.confirmed.connect(self._on_call_confirmed)
        bridge.disconnected.connect(self._on_call_disconnected)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("CentralWidget")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        # ── Header card ──────────────────────────────────────────────────
        header_card = QWidget()
        header_card.setProperty("role", "card")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(18, 14, 14, 14)
        header_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        app_title = QLabel("Taxi24H Voice Assistant")
        app_title.setObjectName("AppTitle")
        app_subtitle = QLabel("Transcripción en tiempo real · Extracción estructurada del servicio")
        app_subtitle.setObjectName("AppSubtitle")
        title_col.addWidget(app_title)
        title_col.addWidget(app_subtitle)
        header_layout.addLayout(title_col)

        header_layout.addStretch()

        self.status_label = QLabel()
        self.status_label.setObjectName("StatusLabel")
        self._set_status("detenido", "idle")
        header_layout.addWidget(self.status_label)

        self.start_button = QPushButton("Iniciar escucha")
        self.start_button.setProperty("variant", "accent")
        self.start_button.setMinimumWidth(148)
        self.start_button.clicked.connect(self._toggle_capture)
        header_layout.addWidget(self.start_button)

        root.addWidget(header_card)

        # ── Transcript card ───────────────────────────────────────────────
        transcript_card = QWidget()
        transcript_card.setProperty("role", "card")
        tc_layout = QVBoxLayout(transcript_card)
        tc_layout.setContentsMargins(14, 10, 14, 10)
        tc_layout.setSpacing(6)

        t_label = QLabel("TRANSCRIPCIÓN EN VIVO")
        t_label.setProperty("role", "section_title")
        tc_layout.addWidget(t_label)

        self.transcript_view = QTextEdit()
        self.transcript_view.setObjectName("TranscriptView")
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setMinimumHeight(60)
        self.transcript_view.setPlaceholderText(
            "Aquí aparecerá la transcripción en tiempo real..."
        )
        tc_layout.addWidget(self.transcript_view)

        root.addWidget(transcript_card, 1)

        # ── Fields card (dentro de QScrollArea para que la ventana sea libre) ─
        fields_card = QWidget()
        fields_card.setProperty("role", "card")
        fc_layout = QVBoxLayout(fields_card)
        fc_layout.setContentsMargins(14, 10, 14, 10)
        fc_layout.setSpacing(6)

        f_label = QLabel("DATOS DEL SERVICIO")
        f_label.setProperty("role", "section_title")
        fc_layout.addWidget(f_label)

        # Layout:
        #   Fila 0: CLIENTE (col 0)  |  FECHA (col 2)
        #   Fila 1: TELEFONO (col 0) |  HORA (col 2)
        #   Fila 2: TIPO_SERVICIO (col 0) | OBSERVACIONES (col 2)
        #   Fila 3: RECOGIDA  — span completo (col 0..2)
        #   Fila 4: DESTINO   — span completo (col 0..2)
        _LEFT_TOP  = ["cliente", "telefono", "tipo_servicio"]
        _RIGHT_TOP = ["fecha",   "hora",     "observaciones"]
        _FULL_ROW  = ["recogida", "destino"]

        self.field_widgets: dict[str, FieldWidget] = {}
        fields_grid = QGridLayout()
        fields_grid.setVerticalSpacing(0)
        fields_grid.setHorizontalSpacing(0)
        fields_grid.setColumnStretch(0, 1)
        fields_grid.setColumnStretch(2, 1)

        # Divisor vertical entre columnas (solo filas superiores)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color: #E2D9CE;")
        fields_grid.addWidget(divider, 0, 1, len(_LEFT_TOP), 1)

        for row, key in enumerate(_LEFT_TOP):
            fw = FieldWidget(key, FIELD_LABELS[key])
            fw.locked_changed.connect(self._on_field_locked)
            self.field_widgets[key] = fw
            fields_grid.addWidget(fw, row, 0)

        for row, key in enumerate(_RIGHT_TOP):
            fw = FieldWidget(key, FIELD_LABELS[key])
            fw.locked_changed.connect(self._on_field_locked)
            self.field_widgets[key] = fw
            fields_grid.addWidget(fw, row, 2)

        # Recogida y Destino: ancho completo (span 3 columnas)
        for i, key in enumerate(_FULL_ROW):
            fw = FieldWidget(key, FIELD_LABELS[key])
            fw.locked_changed.connect(self._on_field_locked)
            self.field_widgets[key] = fw
            fields_grid.addWidget(fw, len(_LEFT_TOP) + i, 0, 1, 3)

        fc_layout.addLayout(fields_grid)

        # Acciones dentro de la card de campos
        fc_layout.addSpacing(4)
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.copy_btn = QPushButton("Copiar datos")
        self.copy_btn.setProperty("variant", "primary")
        self.copy_btn.clicked.connect(self._on_copy)
        actions.addWidget(self.copy_btn)

        self.save_btn = QPushButton("Guardar TXT")
        self.save_btn.clicked.connect(self._on_save)
        actions.addWidget(self.save_btn)

        self.clear_btn = QPushButton("Nueva llamada")
        self.clear_btn.setProperty("variant", "danger")
        self.clear_btn.clicked.connect(self._on_clear)
        actions.addWidget(self.clear_btn)

        actions.addStretch()
        fc_layout.addLayout(actions)

        # QScrollArea: permite que la ventana se encoja libremente
        scroll = QScrollArea()
        scroll.setWidget(fields_card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)

    def _setup_extraction_thread(self) -> None:
        self._ext_thread = QThread(self)
        self._ext_worker = ExtractionWorker(self.extractor)
        self._ext_worker.moveToThread(self._ext_thread)
        self._run_extraction.connect(self._ext_worker.do_extract)
        self._ext_worker.result_ready.connect(self._on_extraction_ready)
        self._ext_thread.start()

    # ─── helpers visuales ────────────────────────────────────────────────

    def _set_status(self, text: str, state: str = "idle") -> None:
        """Actualiza el texto y el estilo visual del status label."""
        dots = {"idle": "●", "active": "●", "warning": "●"}
        self.status_label.setText(f"{dots.get(state, '●')}  Estado: {text}")
        self.status_label.setStyleSheet(_STATUS_STYLES.get(state, _STATUS_STYLES["idle"]))

    # ─── capture control ─────────────────────────────────────────────────

    def _toggle_capture(self) -> None:
        if self.capture is None:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self) -> None:
        try:
            cable_dev, mic_dev = resolve_capture_devices(
                self.settings.cable_hint, self.settings.operator_mic_hint
            )
        except RuntimeError as exc:
            QMessageBox.critical(self, "Error de dispositivos", str(exc))
            return

        try:
            self.capture = DualChannelCapture(cable_dev, mic_dev)
            self.capture.start()
            self.stt_client = RealtimeSTTClient(
                openai_api_key=self.settings.openai_api_key,
                cliente_queue=self.capture.queue_cliente,
                operador_queue=self.capture.queue_operador,
                on_transcript=self._emit_transcript_async,
                on_error=self._emit_stt_error_async,
            )
            self.stt_client.start()
        except Exception as exc:
            QMessageBox.critical(self, "Error al iniciar captura", str(exc))
            self._stop_capture()
            return

        self._extract_timer.start()
        self.start_button.setText("Detener escucha")
        self._set_status("escuchando · audio activo", "active")
        self.status_label.setToolTip(
            f"cliente: [{cable_dev}] {device_name(cable_dev)}\n"
            f"operador: [{mic_dev}] {device_name(mic_dev)}"
        )

    def _stop_capture(self) -> None:
        self._extract_timer.stop()
        if self.stt_client is not None:
            try:
                self.stt_client.stop()
            except Exception:
                log.exception("stopping stt client")
            self.stt_client = None
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                log.exception("stopping capture")
            self.capture = None
        self.start_button.setText("Iniciar escucha")
        self._set_status("detenido", "idle")

    # ─── transcript handling ─────────────────────────────────────────────

    def _emit_transcript_async(self, speaker: str, text: str) -> None:
        self._transcript_appended.emit(speaker, text)

    def _emit_stt_error_async(self, speaker: str, message: str) -> None:
        """Llamado desde el worker thread — emite señal Qt para cruzar al hilo principal."""
        self._stt_error.emit(speaker, message)

    @Slot(str, str)
    def _on_stt_error(self, speaker: str, message: str) -> None:
        """Recibe errores STT en el hilo principal. Solo cambia el status; no interrumpe."""
        log.warning("STT error canal [%s]: %s", speaker, message)
        self._set_status(f"error STT [{speaker}] — ver log", "warning")

    @Slot(str, str)
    def _on_transcript_appended(self, speaker: str, text: str) -> None:
        if self._pending_reset and self._is_real_transcript_text(text):
            self._consume_pending_reset()

        self.transcript_buffer.append(speaker, text)
        tag = "C" if speaker == "cliente" else "O"
        self.transcript_view.append(f"[{tag}] {text}")

    @staticmethod
    def _is_real_transcript_text(text: str) -> bool:
        if not text:
            return False
        stripped = text.strip()
        if not stripped:
            return False
        return any(ch.isalnum() for ch in stripped)

    def _consume_pending_reset(self) -> None:
        log.info("microsip: limpieza diferida (primer texto de la nueva llamada)")
        self._flush_pickup_geocoding()
        self.transcript_buffer.clear()
        self.current_data = ServiceData.empty()
        self.locked_fields.clear()
        if self._stability is not None:
            self._stability.reset()
        self.transcript_view.clear()
        for fw in self.field_widgets.values():
            fw.reset()

        phone = self._pending_phone
        self._pending_reset = False
        self._pending_phone = ""

        if phone:
            tel_fw = self.field_widgets.get("telefono")
            if tel_fw is not None:
                tel_fw.set_locked_from_system(phone)
                self.current_data.telefono = phone
                self.locked_fields.add("telefono")

        self._set_status("nueva llamada en curso (escucha activa)", "active")

    # ─── extraction ──────────────────────────────────────────────────────

    def _maybe_extract(self) -> None:
        word_count = self.transcript_buffer.word_count()
        if not self.extractor.should_extract(word_count):
            return
        transcript = self.transcript_buffer.full_text()
        if not transcript.strip():
            return
        self.extractor.mark_run(word_count)
        self._run_extraction.emit(
            transcript, self.current_data, list(self.locked_fields)
        )

    @Slot(object)
    def _on_extraction_ready(self, data: Any) -> None:
        self.current_data = data
        for key in FIELD_KEYS:
            fw = self.field_widgets[key]
            if not fw.is_locked():
                fw.set_value_from_model(getattr(data, key))
        geo_status = getattr(data, "_recogida_status", "skipped")
        self.field_widgets["recogida"].set_geo_status(geo_status)

    def _flush_pickup_geocoding(self) -> None:
        if self._normalizer is None:
            return
        try:
            transcript = self.transcript_buffer.full_text()
            updated = self._normalizer.normalize_pickup_now(
                self.current_data,
                transcript=transcript,
                current_data=self.current_data,
                locked_fields=self.locked_fields,
            )
            self.current_data = updated
            fw = self.field_widgets["recogida"]
            if not fw.is_locked():
                fw.set_value_from_model(updated.recogida)
                fw.set_geo_status(getattr(updated, "_recogida_status", "skipped"))
        except Exception:
            log.exception("flush_pickup_geocoding error")

    # ─── field lock ──────────────────────────────────────────────────────

    @Slot(str, bool)
    def _on_field_locked(self, field_key: str, locked: bool) -> None:
        if locked:
            self.locked_fields.add(field_key)
            if field_key == "recogida":
                self.current_data._geo_operator_edited_pickup = True  # type: ignore[attr-defined]
        else:
            self.locked_fields.discard(field_key)
        setattr(
            self.current_data,
            field_key,
            self.field_widgets[field_key].value(),
        )

    # ─── actions ─────────────────────────────────────────────────────────

    def _read_data_from_ui(self) -> ServiceData:
        data = ServiceData()
        for key in FIELD_KEYS:
            setattr(data, key, self.field_widgets[key].value())
        return data

    def _on_copy(self) -> None:
        data = self._read_data_from_ui()
        text = format_service_text(data)
        copy_to_clipboard(text)
        self._set_status("copiado al portapapeles", "active")

    def _on_save(self) -> None:
        data = self._read_data_from_ui()
        transcript = self.transcript_buffer.full_text()
        try:
            path = save_session(transcript, data)
        except Exception as exc:
            QMessageBox.critical(self, "Error guardando sesion", str(exc))
            return
        QMessageBox.information(self, "Sesion guardada", f"Guardada en:\n{path}")

    def _on_clear(self) -> None:
        self._flush_pickup_geocoding()
        self._pending_reset = False
        self._pending_phone = ""
        self.transcript_buffer.clear()
        self.current_data = ServiceData.empty()
        self.locked_fields.clear()
        if self._stability is not None:
            self._stability.reset()
        self.transcript_view.clear()
        for fw in self.field_widgets.values():
            fw.reset()
        self._set_status("limpio · listo para nueva llamada", "idle")

    # ─── MicroSIP events ─────────────────────────────────────────────────

    @Slot(str)
    def _on_call_ringing(self, number: str) -> None:
        self._pending_reset = True
        clean = "".join(ch for ch in (number or "") if ch.isdigit())
        if len(clean) >= 9:
            self._pending_phone = clean
        else:
            self._pending_phone = ""

        if self.capture is None:
            self._start_capture()

        label = f"llamada entrante{' desde ' + number if number else ''} · escucha armada"
        self._set_status(label, "warning")

    @Slot(str)
    def _on_call_confirmed(self, number: str) -> None:
        self._on_call_ringing(number)

    @Slot(str)
    def _on_call_disconnected(self, _number: str) -> None:
        self._pending_reset = False
        self._pending_phone = ""
        if self.capture is not None:
            self._stop_capture()
        self._set_status("llamada finalizada · datos disponibles", "idle")

    # ─── close ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        self._flush_pickup_geocoding()
        self._stop_capture()
        self._ext_thread.quit()
        self._ext_thread.wait(2000)
        super().closeEvent(event)
