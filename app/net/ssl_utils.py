"""Contexto SSL compartido — resuelve CERTIFICATE_VERIFY_FAILED en build frozen.

En builds de PyInstaller el exe no accede al almacén de certificados de
Windows y `ssl.create_default_context()` no encuentra CAs. Usar el bundle
de `certifi` garantiza verificación real en todos los entornos.
Nunca desactivar la verificación.
"""
from __future__ import annotations

import logging
import ssl
import sys
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_ssl_context() -> ssl.SSLContext:
    frozen = getattr(sys, "frozen", False)
    try:
        import certifi
        cafile = Path(certifi.where())
        if cafile.exists():
            log.info("SSL CA bundle: %s (frozen=%s)", cafile, frozen)
            return ssl.create_default_context(cafile=str(cafile))
        log.warning(
            "certifi.where() apunta a un archivo inexistente: %s (frozen=%s)",
            cafile, frozen,
        )
    except ImportError:
        log.warning("certifi no está disponible (frozen=%s)", frozen)
    except Exception as exc:
        log.warning(
            "No se pudo construir SSLContext con certifi: %s (frozen=%s)",
            exc, frozen,
        )

    log.warning("Fallback: usando ssl.create_default_context() sin certifi")
    return ssl.create_default_context()
