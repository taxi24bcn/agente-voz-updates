"""Sincronizacion de sesiones con Google Drive + Sheets via Apps Script webhook.

Arquitectura:
  - CloudUploader(QThread): sube un JSON de sesion en background, sin bloquear la UI
  - retry_pending(): reintenta sesiones con upload_status == "pending"
  - update_local_json_status(): actualiza el JSON local con el resultado de la subida

Serialización de escrituras al JSON local:
  Se usa un threading.Lock por session_id para evitar carreras entre
  CloudUploader y retry_pending cuando ambos intentan actualizar el mismo archivo.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QThread, Signal

from app.config.settings import PENDING_DIR, SESSIONS_DIR
from app.net.ssl_utils import get_ssl_context

log = logging.getLogger(__name__)

_TIMEOUT_S = 15.0
_RETRY_BACKOFF_S = (5.0, 15.0, 30.0)  # backoff entre reintentos del mismo session_id

# Lock global por session_id para serializar escrituras al JSON local
_json_update_locks: dict[str, threading.Lock] = {}
_json_update_locks_mutex = threading.Lock()


def _get_session_lock(session_id: str) -> threading.Lock:
    with _json_update_locks_mutex:
        if session_id not in _json_update_locks:
            _json_update_locks[session_id] = threading.Lock()
        return _json_update_locks[session_id]


# ---------------------------------------------------------------------------
# Actualización del JSON local
# ---------------------------------------------------------------------------

def update_local_json_status(
    session_id: str,
    upload_status: str,
    remote_json_file_id: Optional[str] = None,
    remote_txt_file_id: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Actualiza upload_status y campos relacionados en el JSON local de sesion.

    Serializado por session_id para evitar carreras entre hilos.
    """
    lock = _get_session_lock(session_id)
    with lock:
        json_path = SESSIONS_DIR / f"{session_id}.json"
        if not json_path.exists():
            log.warning("update_local_json_status: %s not found", json_path)
            return

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("update_local_json_status: cannot read %s: %s", json_path, exc)
            return

        data["upload_status"] = upload_status
        data["last_upload_at"] = datetime.now().isoformat(timespec="seconds")
        if remote_json_file_id is not None:
            data["remote_json_file_id"] = remote_json_file_id
        if remote_txt_file_id is not None:
            data["remote_txt_file_id"] = remote_txt_file_id
        if error_code is not None:
            data["last_upload_error_code"] = error_code
        if error_message is not None:
            data["last_upload_error_message"] = error_message

        try:
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            log.debug("updated local JSON status: session=%s status=%s", session_id, upload_status)
        except OSError as exc:
            log.error("update_local_json_status: cannot write %s: %s", json_path, exc)


# ---------------------------------------------------------------------------
# Pending pointers
# ---------------------------------------------------------------------------

def _create_pending_pointer(session_id: str, json_path: Path, txt_path: Path) -> None:
    """Crea un archivo .pending.json con punteros al backup local."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending_path = PENDING_DIR / f"{session_id}.pending.json"
    pointer = {
        "session_id": session_id,
        "json_path": str(json_path),
        "txt_path": str(txt_path),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        pending_path.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("pending pointer created: %s", pending_path)
    except OSError as exc:
        log.error("cannot create pending pointer for %s: %s", session_id, exc)


def _remove_pending_pointer(session_id: str) -> None:
    pending_path = PENDING_DIR / f"{session_id}.pending.json"
    try:
        if pending_path.exists():
            pending_path.unlink()
    except OSError as exc:
        log.warning("cannot remove pending pointer %s: %s", pending_path, exc)


# ---------------------------------------------------------------------------
# HTTP upload
# ---------------------------------------------------------------------------

def _do_upload(
    webhook_url: str,
    token: str,
    session_json: dict[str, Any],
    txt_content: str,
) -> dict[str, Any]:
    """Envia una sesion al webhook y devuelve el JSON de respuesta.

    Lanza urllib.error.URLError / TimeoutError en caso de fallo de red.
    """
    payload = {
        "token": token,
        "session": session_json,
        "txt": txt_content,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S, context=get_ssl_context()) as resp:
        raw = resp.read()
    return json.loads(raw)


def upload_session(
    webhook_url: str,
    token: str,
    session_id: str,
    json_path: Path,
    txt_path: Path,
) -> str:
    """Sube una sesion al webhook y actualiza el JSON local.

    Devuelve el upload_status resultante.
    No crea .pending si el resultado es no reintentable.
    """
    try:
        session_json = json.loads(json_path.read_text(encoding="utf-8"))
        txt_content = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    except OSError as exc:
        log.error("upload_session: cannot read local files for %s: %s", session_id, exc)
        return "local_only"

    try:
        result = _do_upload(webhook_url, token, session_json, txt_content)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("upload_session: network error for %s: %s", session_id, exc)
        return "pending"

    status = result.get("status", "")
    log.info("upload_session: session=%s webhook_status=%s", session_id, status)

    if status in ("ok", "already_exists", "recovered"):
        file_id_json = result.get("json_file_id")
        file_id_txt = result.get("txt_file_id")
        final_status = status if status != "recovered" else "recovered"
        update_local_json_status(
            session_id,
            upload_status=final_status,
            remote_json_file_id=file_id_json,
            remote_txt_file_id=file_id_txt,
        )
        _remove_pending_pointer(session_id)
        return final_status

    if status == "forbidden":
        update_local_json_status(
            session_id,
            upload_status="auth_failed",
            error_code="forbidden",
        )
        return "auth_failed"

    if status == "invalid_payload":
        update_local_json_status(
            session_id,
            upload_status="invalid_payload",
            error_code="invalid_payload",
            error_message=str(result),
        )
        return "invalid_payload"

    # Respuesta inesperada → tratar como error de red reintentable
    log.warning("upload_session: unexpected response for %s: %s", session_id, result)
    return "pending"


# ---------------------------------------------------------------------------
# QThread uploader
# ---------------------------------------------------------------------------

class CloudUploader(QThread):
    """Sube una sesion en background (no bloquea la UI).

    Señales:
        upload_finished(session_id, upload_status)
    """
    upload_finished = Signal(str, str)  # (session_id, upload_status)

    def __init__(
        self,
        webhook_url: str,
        token: str,
        session_id: str,
        json_path: Path,
        txt_path: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._webhook_url = webhook_url
        self._token = token
        self._session_id = session_id
        self._json_path = json_path
        self._txt_path = txt_path

    def run(self) -> None:
        status = upload_session(
            self._webhook_url,
            self._token,
            self._session_id,
            self._json_path,
            self._txt_path,
        )
        if status == "pending":
            _create_pending_pointer(self._session_id, self._json_path, self._txt_path)
            update_local_json_status(self._session_id, "pending")

        self.upload_finished.emit(self._session_id, status)

        if status in ("ok", "already_exists", "recovered"):
            _retry_pending_background(self._webhook_url, self._token)


# ---------------------------------------------------------------------------
# Retry pending
# ---------------------------------------------------------------------------

def retry_pending(webhook_url: str, token: str) -> None:
    """Reintenta todas las sesiones con .pending.json en PENDING_DIR.

    Aplica backoff básico entre intentos del mismo session_id.
    Sesiones con archivos locales borrados se marcan missing_local_files.
    """
    pending_files = list(PENDING_DIR.glob("*.pending.json"))
    if not pending_files:
        return

    log.info("retry_pending: found %d pending sessions", len(pending_files))

    for pf in pending_files:
        try:
            pointer = json.loads(pf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("retry_pending: cannot read pointer %s: %s", pf, exc)
            continue

        session_id = pointer.get("session_id", pf.stem.replace(".pending", ""))
        json_path = Path(pointer.get("json_path", ""))
        txt_path = Path(pointer.get("txt_path", ""))

        # Verificar que los archivos locales existen
        if not json_path.exists():
            log.warning("retry_pending: missing local JSON for %s — marking", session_id)
            update_local_json_status(
                session_id,
                "missing_local_files",
                error_message=f"json_path not found: {json_path}",
            )
            try:
                pf.unlink()
            except OSError:
                pass
            continue

        # Backoff: comprobar cuántos reintentos lleva
        attempt_count = pointer.get("attempt_count", 0)
        if attempt_count < len(_RETRY_BACKOFF_S):
            delay = _RETRY_BACKOFF_S[attempt_count]
        else:
            delay = _RETRY_BACKOFF_S[-1]

        last_attempt = pointer.get("last_attempt_at")
        if last_attempt:
            elapsed = time.time() - datetime.fromisoformat(last_attempt).timestamp()
            if elapsed < delay:
                continue

        # Actualizar puntero con el nuevo intento
        pointer["attempt_count"] = attempt_count + 1
        pointer["last_attempt_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            pf.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

        status = upload_session(webhook_url, token, session_id, json_path, txt_path)
        if status == "pending":
            log.info("retry_pending: still pending session=%s attempt=%d", session_id, attempt_count + 1)
        else:
            log.info("retry_pending: resolved session=%s status=%s", session_id, status)
            if status != "pending":
                try:
                    pf.unlink()
                except OSError:
                    pass


def _retry_pending_background(webhook_url: str, token: str) -> None:
    """Lanza retry_pending en un hilo daemon para no bloquear el uploader."""
    t = threading.Thread(
        target=retry_pending,
        args=(webhook_url, token),
        daemon=True,
        name="retry-pending",
    )
    t.start()


class PendingRetryWorker(QThread):
    """QThread para ejecutar retry_pending al arrancar la app."""

    def __init__(self, webhook_url: str, token: str, parent=None) -> None:
        super().__init__(parent)
        self._webhook_url = webhook_url
        self._token = token

    def run(self) -> None:
        retry_pending(self._webhook_url, self._token)
