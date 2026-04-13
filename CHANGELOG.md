# Changelog — Agente Voz Taxi24H

## v2.1.7 — 2026-04-13
- Fix: selector de micrófono en pantalla de configuración inicial
- Al instalar en un PC nuevo, ahora se muestra un desplegable con todos
  los micrófonos disponibles — ya no hace falta editar el .env a mano

## v2.1.3 — 2026-04-11 (baseline estable)
- Baseline de referencia tras validacion en produccion
- Correccion: falsos positivos de aeropuerto en recogida
- Correccion: sentence splitting para transcripciones compactas
- Mejora: precedencia de alias POI sobre direcciones postales
- Evaluacion: 61% de llamadas utilizables (28% validadas, 11% revision, 22% parcial/otras)

## v2.1.0 — 2026-03-XX
- Integracion MicroSIP via HTTP local (puerto 8733)
- Geocodificacion con Google Maps API
- Sistema de alias POI locales (hospitales, clinicas, hoteles)
- Estabilizador de recogida (ventana 2 segundos)

## v2.0.0 — 2026-03-XX
- Captura dual de audio (cliente/operador) con VB-CABLE
- STT con gpt-4o-transcribe (streaming chunked)
- Extraccion estructurada con gpt-4o-mini
- UI PySide6 con campos editables y bloqueo por operador
