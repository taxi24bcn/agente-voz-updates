"""Tema visual global — Taxi24H Voice Assistant."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# Paleta de color
# Fondo app:   #F6F2EA  (marfil cálido)
# Cards:       #FFFFFF  con borde #E7DED2
# Acento oro:  #D4A437
# Texto base:  #1E1B16
# Muted:       #7B6F61
# Activo:      #2A7A50  (verde)
# Warning:     #8A6508  (ámbar)

APP_QSS = """
/* ================================================================
   BASE
   ================================================================ */
QMainWindow, QWidget#CentralWidget {
    background-color: #F6F2EA;
}

QWidget {
    color: #1E1B16;
    font-family: "Segoe UI";
    font-size: 13px;
}

/* ================================================================
   CARDS
   ================================================================ */
QWidget[role="card"] {
    background-color: #FFFFFF;
    border: 1.5px solid #DDD4C6;
    border-radius: 16px;
}

/* Filas de campos — planas con separador inferior */
QWidget[role="field_row"] {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #EDE8E1;
}

/* ================================================================
   LABELS
   ================================================================ */
QLabel#AppTitle {
    font-size: 20px;
    font-weight: 700;
    color: #111111;
}

QLabel#AppSubtitle {
    font-size: 11px;
    color: #7B6F61;
}

QLabel[role="section_title"] {
    font-size: 12px;
    font-weight: 700;
    color: #5A5048;
    letter-spacing: 0.8px;
}

/* ================================================================
   TRANSCRIPT
   ================================================================ */
QTextEdit#TranscriptView {
    background-color: #FCFBF8;
    border: 1px solid #DED6CB;
    border-radius: 10px;
    padding: 8px;
    font-family: "Consolas", "Courier New";
    font-size: 12px;
    color: #2A2520;
    selection-background-color: #D4A437;
    selection-color: #111111;
}

/* ================================================================
   BOTONES — BASE
   ================================================================ */
QPushButton {
    background-color: #FFFFFF;
    color: #1A1A1A;
    border: 1px solid #DCCFBF;
    border-radius: 10px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 12px;
    min-height: 30px;
}

QPushButton:hover {
    background-color: #FBF7EF;
    border-color: #CFA84B;
}

QPushButton:pressed {
    background-color: #F2E7CC;
}

QPushButton:disabled {
    background-color: #ECE8E2;
    color: #A79D90;
    border-color: #DED6CB;
}

/* Botón principal (negro) */
QPushButton[variant="primary"] {
    background-color: #1A1A1A;
    color: #FFFFFF;
    border-color: #1A1A1A;
}
QPushButton[variant="primary"]:hover {
    background-color: #2E2E2E;
    border-color: #2E2E2E;
}
QPushButton[variant="primary"]:pressed {
    background-color: #111111;
}

/* Botón acento (dorado) */
QPushButton[variant="accent"] {
    background-color: #D4A437;
    color: #111111;
    border-color: #C49428;
    font-weight: 700;
}
QPushButton[variant="accent"]:hover {
    background-color: #E0B34E;
    border-color: #D4A437;
}
QPushButton[variant="accent"]:pressed {
    background-color: #C49428;
}

/* Botón peligro (rojo suave) */
QPushButton[variant="danger"] {
    background-color: #FFF2F0;
    color: #A63E2E;
    border-color: #F0C1B8;
}
QPushButton[variant="danger"]:hover {
    background-color: #FFE5E0;
    border-color: #E8A89A;
}

/* Botón mini (iconos inline en campos) */
QPushButton[variant="mini"] {
    background-color: transparent;
    color: #9A8E82;
    border: 1px solid #E2D9CE;
    border-radius: 6px;
    padding: 2px 4px;
    font-size: 12px;
    min-height: 0;
    font-weight: 500;
}
QPushButton[variant="mini"]:hover {
    background-color: #F6F2EA;
    color: #1A1A1A;
    border-color: #CFA84B;
}

/* ================================================================
   SCROLLBAR
   ================================================================ */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}
QScrollBar::handle:vertical {
    background: #D0C4B4;
    min-height: 28px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #BFB09E;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
    border: none;
    height: 0;
}

/* ================================================================
   MESSAGEBOX
   ================================================================ */
QMessageBox {
    background-color: #F6F2EA;
}
QMessageBox QLabel {
    color: #1E1B16;
    font-size: 13px;
}
"""


def apply_theme(app: QApplication) -> None:
    """Aplica el tema visual global a la aplicación."""
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_QSS)
