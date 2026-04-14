"""
Actualizador seguro del Agente Voz Taxi24H.

Flujo completo:
  1. UpdateChecker (QThread) arranca en segundo plano al abrir la ventana
  2. Consulta UPDATE_CHECK_URL y compara versiones
  3. Si hay version nueva emite: version, download_url, release_notes, sha256
  4. show_update_dialog pregunta al usuario
  5. Si acepta: UpdateDownloader descarga el instalador con barra de progreso
  6. Valida: archivo no vacio, tamanio esperado, hash SHA-256
  7. Solo si todo es correcto: lanza el instalador de forma independiente y cierra la app
  8. Inno Setup instala la nueva version (el .env del usuario se conserva)

Formato del version.json remoto:
{
    "version": "2.2.0",
    "download_url": "https://github.com/TU_USUARIO/agente-voz-updates/releases/download/v2.2.0/AgenteVozTaxi24H-2.2.0-Setup.exe",
    "release_notes": "Mejoras en extraccion de direcciones.",
    "sha256": "3f2d...a8b9c"
}

CONFIGURAR: poner la URL real en UPDATE_CHECK_URL cuando haya repositorio configurado.
Para calcular el SHA-256 del instalador en PowerShell:
  (Get-FileHash "dist\\installer\\AgenteVozTaxi24H-X.Y.Z-Setup.exe" -Algorithm SHA256).Hash.ToLower()
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from app.net.ssl_utils import get_ssl_context

log = logging.getLogger(__name__)

# URL publica del version.json publicado con cada release.
# Ejemplo GitHub raw:
#   "https://raw.githubusercontent.com/TU_USUARIO/agente-voz-updates/main/version.json"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/taxi24bcn/agente-voz-updates/main/version.json"


# ── Utilidades ───────────────────────────────────────────────────────────────

def read_local_version() -> str:
    """Lee la version instalada desde version.txt.

    En modo frozen (PyInstaller 6.x) los archivos de datos van en _internal/,
    accesible via sys._MEIPASS, no junto al .exe.
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        else:
            base = Path(__file__).resolve().parents[1]
        return (base / "version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0, 0, 0)


def _get_downloads_dir() -> Path:
    """Carpeta de descargas del usuario. Siempre escribible."""
    from app.config.settings import DOWNLOADS_DIR
    return DOWNLOADS_DIR


def _launch_installer_and_exit(installer_path: str, parent: QWidget | None) -> None:
    """Lanza el instalador como proceso independiente y cierra la app."""
    try:
        detached_process        = 0x00000008
        create_new_process_group = 0x00000200
        subprocess.Popen(
            [installer_path],
            cwd=str(Path(installer_path).parent),
            creationflags=detached_process | create_new_process_group,
            close_fds=True,
        )
        log.info("Instalador lanzado: %s", installer_path)
    except Exception as exc:
        QMessageBox.critical(
            parent,
            "Error al lanzar el instalador",
            f"No se pudo iniciar el instalador:\n{exc}\n\n"
            f"Ejecútalo manualmente desde:\n{installer_path}",
        )
        return

    app = QApplication.instance()
    if app is not None:
        app.quit()


# ── Hilo 1: comprobacion de version ──────────────────────────────────────────

class UpdateChecker(QThread):
    """Consulta la version remota sin bloquear la UI.

    Emite update_available(version, download_url, release_notes, sha256)
    si hay una version mas nueva que la instalada.
    """

    update_available = Signal(str, str, str, str)  # (version, url, notes, sha256)

    def run(self) -> None:
        if not UPDATE_CHECK_URL:
            log.debug("UpdateChecker: UPDATE_CHECK_URL no configurada — omitiendo.")
            return
        try:
            import json
            import urllib.request

            with urllib.request.urlopen(
                UPDATE_CHECK_URL, timeout=6, context=get_ssl_context()
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            remote = data.get("version", "0.0.0")
            local  = read_local_version()

            if _version_tuple(remote) > _version_tuple(local):
                log.info("Actualizacion disponible: v%s → v%s", local, remote)
                self.update_available.emit(
                    remote,
                    data.get("download_url", ""),
                    data.get("release_notes", ""),
                    data.get("sha256", ""),
                )
            else:
                log.debug("App al dia (v%s).", local)
        except Exception as exc:
            log.debug("Comprobacion de actualizacion fallida: %s", exc)


# ── Hilo 2: descarga segura del instalador ───────────────────────────────────

class UpdateDownloader(QThread):
    """Descarga el instalador en chunks con cancelacion cooperativa y validacion SHA-256.

    Senales:
      progress(int)   → porcentaje 0-100
      done(str)       → ruta al archivo descargado y verificado
      failed(str)     → mensaje de error
      cancelled()     → descarga cancelada limpiamente por el usuario
    """

    progress  = Signal(int)
    done      = Signal(str)
    failed    = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        url: str,
        dest: Path,
        expected_sha256: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._url             = url
        self._dest            = dest
        self._temp_dest       = dest.with_suffix(dest.suffix + ".part")
        self._expected_sha256 = expected_sha256.strip().lower()
        self._cancel_requested = False

    def cancel(self) -> None:
        """Solicita cancelacion cooperativa — el hilo termina en el proximo chunk."""
        self._cancel_requested = True

    def _cleanup_partial(self) -> None:
        try:
            if self._temp_dest.exists():
                self._temp_dest.unlink()
        except OSError:
            pass

    def run(self) -> None:
        try:
            import urllib.request

            if not self._expected_sha256:
                raise RuntimeError(
                    "La actualizacion no incluye SHA-256.\n"
                    "La instalacion ha sido bloqueada por seguridad."
                )

            self._dest.parent.mkdir(parents=True, exist_ok=True)
            self._cleanup_partial()

            log.info("Descargando actualizacion: %s → %s", self._url, self._dest)

            request = urllib.request.Request(
                self._url,
                headers={"User-Agent": "AgenteVozTaxi24H-Updater/1.0"},
            )

            downloaded = 0
            total_size = 0
            sha256     = hashlib.sha256()
            chunk_size = 1024 * 128  # 128 KB por chunk

            with urllib.request.urlopen(
                request, timeout=30, context=get_ssl_context()
            ) as resp:
                content_length = resp.headers.get("Content-Length", "").strip()
                if content_length.isdigit():
                    total_size = int(content_length)

                with open(self._temp_dest, "wb") as f:
                    while True:
                        # Punto de cancelacion cooperativa
                        if self._cancel_requested:
                            self._cleanup_partial()
                            log.info("Descarga cancelada por el usuario.")
                            self.cancelled.emit()
                            return

                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        sha256.update(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            pct = min(int(downloaded * 100 / total_size), 100)
                            self.progress.emit(pct)

            # Validacion 1: archivo no vacio
            if downloaded <= 0:
                self._cleanup_partial()
                raise RuntimeError("La descarga llego vacia.")

            # Validacion 2: tamanio coherente con Content-Length
            if total_size > 0 and downloaded != total_size:
                self._cleanup_partial()
                raise RuntimeError(
                    f"Descarga incompleta.\n"
                    f"Esperado: {total_size:,} bytes\n"
                    f"Recibido: {downloaded:,} bytes"
                )

            # Validacion 3: hash SHA-256
            computed = sha256.hexdigest().lower()
            if computed != self._expected_sha256:
                self._cleanup_partial()
                raise RuntimeError(
                    "El hash SHA-256 no coincide. El archivo puede estar danado o manipulado.\n\n"
                    f"Esperado: {self._expected_sha256}\n"
                    f"Recibido: {computed}"
                )

            # Todo correcto: mover de .part a destino final
            if self._dest.exists():
                self._dest.unlink()
            self._temp_dest.replace(self._dest)

            self.progress.emit(100)
            log.info("Instalador validado correctamente (SHA-256 OK, %d bytes).", downloaded)
            self.done.emit(str(self._dest))

        except Exception as exc:
            self._cleanup_partial()
            log.error("Descarga/validacion fallida: %s", exc)
            self.failed.emit(str(exc))


# ── Dialogo principal de actualizacion ───────────────────────────────────────

def show_update_dialog(
    parent: QWidget | None,
    remote_version: str,
    download_url: str,
    release_notes: str,
    sha256: str,
) -> None:
    """Muestra el dialogo de nueva version y gestiona descarga + instalacion segura."""

    # Paso 1: preguntar al usuario
    msg = QMessageBox(parent)
    msg.setWindowTitle("Actualizacion disponible")
    msg.setText(f"Nueva version disponible: <b>v{remote_version}</b>")
    body = release_notes or "Hay una nueva version del Agente Voz disponible."
    msg.setInformativeText(body + "\n\n¿Descargar e instalar ahora?")
    msg.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    msg.setDefaultButton(QMessageBox.StandardButton.Yes)

    if msg.exec() != QMessageBox.StandardButton.Yes:
        return

    if not download_url:
        QMessageBox.warning(
            parent, "Sin URL de descarga",
            "No hay URL de descarga configurada para esta version.\n"
            "Contacta con el administrador del sistema.",
        )
        return

    if not sha256.strip():
        QMessageBox.warning(
            parent, "Actualizacion no segura",
            "Esta release no incluye SHA-256.\n"
            "La actualizacion automatica ha sido bloqueada por seguridad.\n\n"
            "Descarga e instala manualmente desde el canal de actualizaciones.",
        )
        return

    # Paso 2: descargar con barra de progreso
    filename = f"AgenteVozTaxi24H-{remote_version}-Setup.exe"
    dest     = _get_downloads_dir() / filename

    progress_dlg = QProgressDialog(
        f"Descargando v{remote_version}...", "Cancelar", 0, 100, parent
    )
    progress_dlg.setWindowTitle("Descargando actualizacion")
    progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
    progress_dlg.setMinimumDuration(0)
    progress_dlg.setAutoClose(False)
    progress_dlg.setAutoReset(False)
    progress_dlg.setValue(0)

    downloader = UpdateDownloader(download_url, dest, sha256, parent)
    downloader.progress.connect(progress_dlg.setValue)

    def _cleanup_refs() -> None:
        if parent is not None:
            try:
                parent._update_downloader = None       # type: ignore[attr-defined]
                parent._update_progress_dialog = None  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_done(path: str) -> None:
        progress_dlg.close()
        _cleanup_refs()
        _launch_installer_and_exit(path, parent)

    def _on_failed(error: str) -> None:
        progress_dlg.close()
        _cleanup_refs()
        QMessageBox.critical(
            parent, "Error de actualizacion",
            f"No se pudo descargar o validar la actualizacion:\n\n{error}\n\n"
            "La instalacion ha sido cancelada por seguridad.",
        )

    def _on_cancelled() -> None:
        progress_dlg.close()
        _cleanup_refs()

    def _on_cancel_request() -> None:
        progress_dlg.setLabelText("Cancelando descarga...")
        progress_dlg.setCancelButton(None)
        downloader.cancel()

    downloader.done.connect(_on_done)
    downloader.failed.connect(_on_failed)
    downloader.cancelled.connect(_on_cancelled)
    progress_dlg.canceled.connect(_on_cancel_request)

    # Guardamos refs en el parent para evitar GC mientras corre el hilo
    if parent is not None:
        parent._update_downloader = downloader          # type: ignore[attr-defined]
        parent._update_progress_dialog = progress_dlg  # type: ignore[attr-defined]

    downloader.start()
    progress_dlg.exec()
