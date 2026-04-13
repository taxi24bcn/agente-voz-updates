"""Entry point del asistente de voz Taxi24H.

Uso:
    python -m app.main

Requiere un .env en la raiz del proyecto con al menos OPENAI_API_KEY.
"""
from __future__ import annotations

import io
import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

# En Windows la consola puede usar cp1252 en lugar de UTF-8, lo que rompe
# los logs con caracteres españoles. Re-envolvemos stdout/stderr al arrancar.
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app.config.settings import Settings, has_required_config, reload_env_file
from app.integrations.microsip_http import CallEventBridge, start_server
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
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
