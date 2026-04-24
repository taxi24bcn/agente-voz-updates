# Changelog — Agente Voz Taxi24H

## v2.5.0 — 2026-04-24
Optimización de consumo OpenAI tras incidente de saldo agotado.

- **Circuit breaker STT**: ante un `insufficient_quota` (saldo agotado) o
  `AuthenticationError`, el worker pasa a estado terminal y deja de llamar
  a la API. Antes machacaba el endpoint hasta ~15 llamadas fallidas en 8 s.
- **Backoff STT** ante 429 transitorios / errores de red: 30 s → 60 s →
  120 s → 300 s. Durante la pausa, los frames se descartan para no
  enviarlos acumulados al reanudar.
- **Voice-ratio guard**: antes de enviar audio a la API, se comprueba que
  al menos el 15 % de los frames del buffer superen el umbral de voz.
  Evita pagar por transcribir ruido ambiente sostenido (coche, aire, etc.).
- **Watchdog de inactividad**: tras 10 min sin ningún transcript, o si
  todos los workers están en estado terminal, la captura se detiene sola
  y se avisa al operador. Cubre el caso de MicroSIP que crashea sin
  emitir el evento `ended`.
- **Prompt caching del parser**: el `SYSTEM_PROMPT` estático (~3200
  tokens) cachea al 50 % a partir de la segunda extracción. Se añade
  logging de `usage.cached_tokens` para verificar el hit ratio.
- **File logging permanente**: se escribe `app.log` (rotación 1 MB × 5)
  en `%LOCALAPPDATA%\Taxi24H\AgenteVoz\logs\`. Antes el `basicConfig`
  sólo escribía a stdout, que en el `.exe` frozen se pierde.

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
