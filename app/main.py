"""Entry point del asistente de voz Taxi24H.

Uso:
    python -m app.main

Requiere un .env en la raiz del proyecto con al menos OPENAI_API_KEY.
"""
from __future__ import annotations

import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

# En Windows la consola puede usar cp1252 en lugar de UTF-8, lo que rompe
# los logs con caracteres españoles. Re-envolvemos stdout/stderr al arrancar.
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app.config.settings import LOGS_DIR, Settings, has_required_config, reload_env_file
from app.integrations.microsip_http import CallEventBridge, start_server
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def _configure_logging() -> None:
    """Configura logging a stdout + archivo rotatorio en LOGS_DIR/app.log.

    En un .exe frozen stdout no es visible, por lo que el archivo es la
    única forma de diagnosticar incidentes sin reproducir con el repo.
    """
    level = logging.DEBUG if os.getenv("AGENTE_VOZ_DEBUG") == "1" else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    # Evitar duplicar handlers si main() se invoca dos veces (tests, reload).
    for h in list(root.handlers):
        root.removeHandler(h)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOGS_DIR / "app.log",
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # Si no se puede escribir el archivo, seguimos con stdout solo.
        root.warning("No se pudo abrir el log de archivo: %s", exc)


def main() -> int:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Taxi24H Voice Assistant")
    apply_theme(app)

    # Primer arranque: mostrar dialogo de configuracion si falta la clave API
    if not has_required_config():
        from app.ui.config_dialog import ConfigDialog
        dlg = ConfigDialog()
        if dlg.exec() != ConfigDialog.DialogCode.Accepted:
            # El usuario canceló sin configurar — salir sin error
            return 0
        reload_env_file()

    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        QMessageBox.critical(None, "Configuracion incompleta", str(exc))
        return 1

    window = MainWindow(settings)

    # Servidor HTTP local para eventos de llamada de MicroSIP.
    # Si el puerto ya está ocupado, la app sigue funcionando en modo manual.
    call_bridge = CallEventBridge()
    window.attach_call_bridge(call_bridge)
    start_server(call_bridge)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
