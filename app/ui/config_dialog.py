"""Dialogo de configuracion inicial — claves API y dispositivos de audio.

Se muestra automaticamente en el primer arranque cuando no existe .env,
y puede invocarse manualmente desde el menu de la ventana principal.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.audio.devices import list_input_devices
from app.config.settings import CONFIG_DIR, save_env_config, reload_env_file


class ConfigDialog(QDialog):
    """Formulario para introducir y guardar la configuracion en .env."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuracion — Agente Voz Taxi24H")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()
        self._load_existing_values()

    # ── Construccion UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 20, 24, 20)

        # Cabecera
        title = QLabel("Configuracion de la aplicacion")
        title.setObjectName("AppTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "Las claves se guardan de forma local en tu equipo.\n"
            f"Archivo: {CONFIG_DIR / '.env'}"
        )
        subtitle.setObjectName("AppSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Formulario
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._openai_key = QLineEdit()
        self._openai_key.setPlaceholderText("sk-...")
        self._openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("OpenAI API Key *", self._openai_key)

        self._maps_key = QLineEdit()
        self._maps_key.setPlaceholderText("AIza... (opcional)")
        form.addRow("Google Maps API Key", self._maps_key)

        self._cable_hint = QLineEdit()
        self._cable_hint.setPlaceholderText("cable output")
        form.addRow("Dispositivo cliente (cable virtual)", self._cable_hint)

        self._operator_mic = QComboBox()
        self._operator_mic.setEditable(True)
        self._operator_mic.lineEdit().setPlaceholderText("(microfono por defecto del sistema)")
        self._populate_mic_combo()
        form.addRow("Microfono del operador", self._operator_mic)

        root.addLayout(form)

        # Nota
        note = QLabel(
            "* La OpenAI API Key es obligatoria. Puedes obtenerla en\n"
            "https://platform.openai.com/api-keys"
        )
        note.setObjectName("AppSubtitle")
        note.setWordWrap(True)
        root.addWidget(note)

        # Botones
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty(
            "variant", "primary"
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar y continuar")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _populate_mic_combo(self) -> None:
        """Rellena el combo con los dispositivos de entrada disponibles."""
        self._operator_mic.clear()
        # Opcion vacia = usar el dispositivo por defecto del sistema
        self._operator_mic.addItem("", "")
        cable_hint = self._cable_hint.text().strip().lower() or "cable"
        first_non_cable = None
        for _idx, name in list_input_devices():
            self._operator_mic.addItem(name, name)
            if first_non_cable is None and cable_hint not in name.lower():
                first_non_cable = name
        # Pre-seleccionar el primer micrófono que no sea el cable virtual
        if first_non_cable is not None:
            idx = self._operator_mic.findData(first_non_cable)
            if idx >= 0:
                self._operator_mic.setCurrentIndex(idx)

    # ── Carga de valores existentes ──────────────────────────────────────────

    def _load_existing_values(self) -> None:
        """Pre-rellena el formulario con los valores actuales del .env si existe."""
        env_path = CONFIG_DIR / ".env"
        if not env_path.exists():
            self._cable_hint.setText("cable output")
            return

        try:
            content = env_path.read_text(encoding="utf-8")
        except OSError:
            return

        def _get(key: str) -> str:
            for line in content.splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
            return ""

        self._openai_key.setText(_get("OPENAI_API_KEY"))
        self._maps_key.setText(_get("GOOGLE_MAPS_API_KEY"))
        cable = _get("CABLE_HINT") or "cable output"
        self._cable_hint.setText(cable)
        saved_mic = _get("OPERATOR_MIC_HINT")
        if saved_mic:
            idx = self._operator_mic.findData(saved_mic)
            if idx >= 0:
                self._operator_mic.setCurrentIndex(idx)
            else:
                # El dispositivo guardado ya no existe — mostrarlo igualmente
                self._operator_mic.setCurrentText(saved_mic)

    # ── Guardado ─────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        openai_key = self._openai_key.text().strip()
        if not openai_key:
            QMessageBox.warning(
                self,
                "Clave obligatoria",
                "La OpenAI API Key es obligatoria para usar el asistente.\n\n"
                "Puedes obtenerla en https://platform.openai.com/api-keys",
            )
            self._openai_key.setFocus()
            return

        cable_hint = self._cable_hint.text().strip() or "cable output"
        mic_hint = self._operator_mic.currentText().strip()

        # Evitar guardar un microfono cuyo nombre contiene el hint del cable
        if mic_hint and cable_hint.lower() in mic_hint.lower():
            QMessageBox.warning(
                self,
                "Dispositivo invalido",
                f"El microfono seleccionado ('{mic_hint}') parece ser el mismo "
                f"dispositivo que el cable virtual ('{cable_hint}').\n\n"
                "Selecciona el microfono fisico del operador.",
            )
            self._operator_mic.setFocus()
            return

        save_env_config(
            openai_api_key=openai_key,
            cable_hint=cable_hint,
            operator_mic_hint=mic_hint,
            google_maps_api_key=self._maps_key.text().strip(),
        )
        reload_env_file()
        self.accept()
