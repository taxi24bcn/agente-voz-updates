"""QLineEdit con estado de lock, coloracion por estado y boton de desbloqueo."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

# Paleta de estados — coordina con theme.py
_BASE = "border-radius:6px; padding:3px 8px; font-size:12px;"

STYLE_NORMAL = f"QLineEdit {{ background:#FCFBF8; border:1px solid #DED6CB; color:#1B1B1B; {_BASE} }}"
STYLE_PENDING = f"QLineEdit {{ background:#FFF5F5; border:1px solid #F0C1B8; color:#C05040; {_BASE} }}"
STYLE_REVIEW = f"QLineEdit {{ background:#FFF4C2; border:1px solid #E8C84A; color:#5A4A10; {_BASE} }}"
STYLE_PROVISIONAL = f"QLineEdit {{ background:#FFF9EB; border:1px solid #F0D27A; color:#4A3C0A; {_BASE} }}"
STYLE_LOCKED = (
    f"QLineEdit {{ background:#EDEBE5; border:2px solid #B0A898; color:#2A2520; "
    f"font-weight:600; {_BASE} }}"
)

# Estados de geocoding (solo campo RECOGIDA)
STYLE_GEO_VALIDATED = (
    f"QLineEdit {{ background:#E8F4EE; border:2px solid #2A7A50; color:#1A4A30; {_BASE} }}"
)
STYLE_GEO_PARTIAL = f"QLineEdit {{ background:#FFF4C2; border:1px solid #E8C84A; color:#5A4A10; {_BASE} }}"
STYLE_GEO_OUTSIDE_AMB = (
    f"QLineEdit {{ background:#FFF0E0; border:1px solid #D4903A; color:#6A4010; {_BASE} }}"
)
STYLE_GEO_NO_RESULT = f"QLineEdit {{ background:#FFF5F5; border:1px solid #F0C1B8; color:#C05040; {_BASE} }}"

_GEO_STYLES: dict[str, str] = {
    "validated": STYLE_GEO_VALIDATED,
    "partial_match": STYLE_GEO_PARTIAL,
    "usable_review": STYLE_GEO_PARTIAL,
    "outside_amb": STYLE_GEO_OUTSIDE_AMB,
    "no_result": STYLE_GEO_NO_RESULT,
    "unknown_or_incomplete": STYLE_GEO_NO_RESULT,
    "operator_locked": STYLE_LOCKED,
    "skipped": "",
}


class FieldWidget(QWidget):
    """Single service field: label + editable QLineEdit + unlock button.

    Behavior:
    - set_value_from_model() updates silently; ignores update if locked.
    - On user edit (textEdited), the widget auto-locks. Emits locked_changed.
    - Unlock button returns control to the model extractor.
    """

    locked_changed = Signal(str, bool)  # (field_key, is_locked)

    def __init__(self, field_key: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.field_key = field_key
        self._locked = False
        self._provisional = False
        self._geo_status: str = "skipped"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(8)

        self._label = QLabel(label)
        self._label.setMinimumWidth(120)
        self._label.setMaximumWidth(120)
        self._label.setStyleSheet(
            "font-size:11px; font-weight:700; color:#9A8E82; letter-spacing:0.3px;"
        )
        layout.addWidget(self._label)

        self._edit = QLineEdit()
        self._edit.setText("PENDIENTE")
        self._edit.textEdited.connect(self._on_text_edited)
        layout.addWidget(self._edit, 1)

        self._copy_btn = QPushButton("↗")
        self._copy_btn.setProperty("variant", "mini")
        self._copy_btn.setFixedWidth(28)
        self._copy_btn.setFixedHeight(24)
        self._copy_btn.setToolTip("Copiar este campo")
        self._copy_btn.clicked.connect(self._on_copy_clicked)
        layout.addWidget(self._copy_btn)

        self._unlock_btn = QPushButton("✕")
        self._unlock_btn.setProperty("variant", "mini")
        self._unlock_btn.setFixedWidth(28)
        self._unlock_btn.setFixedHeight(24)
        self._unlock_btn.setToolTip("Desbloquear campo")
        self._unlock_btn.setEnabled(False)
        self._unlock_btn.clicked.connect(self._on_unlock_clicked)
        layout.addWidget(self._unlock_btn)

        # Fila plana — separador visual gestionado desde theme.py
        self.setProperty("role", "field_row")

        self._apply_style()

    def _on_text_edited(self, _text: str) -> None:
        if not self._locked:
            self._locked = True
            self._unlock_btn.setEnabled(True)
            self._apply_style()
            self.locked_changed.emit(self.field_key, True)

    def _on_copy_clicked(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._edit.text())

    def _on_unlock_clicked(self) -> None:
        if self._locked:
            self._locked = False
            self._unlock_btn.setEnabled(False)
            self._apply_style()
            self.locked_changed.emit(self.field_key, False)

    def value(self) -> str:
        return self._edit.text()

    def set_value_from_model(self, text: str) -> None:
        if self._locked:
            return
        if text == self._edit.text():
            if self._provisional:
                self._provisional = False
                self._apply_style()
            return
        self._edit.setText(text)
        self._provisional = True
        self._apply_style()

    def set_locked_from_system(self, text: str) -> None:
        """Establece un valor y bloquea el campo desde el sistema (no por edición humana)."""
        self._edit.setText(text)
        self._provisional = False
        if not self._locked:
            self._locked = True
            self._unlock_btn.setEnabled(True)
            self.locked_changed.emit(self.field_key, True)
        self._apply_style()

    def is_locked(self) -> bool:
        return self._locked

    def set_geo_status(self, status: str) -> None:
        if self._locked:
            return
        self._geo_status = status
        self._apply_style()

    def reset(self) -> None:
        self._locked = False
        self._provisional = False
        self._geo_status = "skipped"
        self._unlock_btn.setEnabled(False)
        self._edit.setText("PENDIENTE")
        self._apply_style()

    def _apply_style(self) -> None:
        if self._locked:
            self._edit.setStyleSheet(STYLE_LOCKED)
            return

        if self.field_key == "recogida" and self._geo_status in _GEO_STYLES:
            geo_style = _GEO_STYLES[self._geo_status]
            if geo_style:
                self._edit.setStyleSheet(geo_style)
                return

        text = self._edit.text()
        if text == "PENDIENTE":
            style = STYLE_PENDING
        elif "REVISAR" in text:
            style = STYLE_REVIEW
        elif self._provisional:
            style = STYLE_PROVISIONAL
        else:
            style = STYLE_NORMAL
        self._edit.setStyleSheet(style)
