# CHECKPOINT — Fase 1 + Fase 2 validado en 7 llamadas
Fecha: 2026-04-12

## Estado
Versión estable de trabajo. No es baseline oficial (muestra insuficiente para comparar con BASELINE_V2_1_3).
Los cambios son defendibles y no introducen regresiones en ningún caso postal limpio.

## Archivos modificados respecto al estado previo a Fase 1
- `app/parser/service_extractor.py` — timeout, no-degrade merge, clasificación de excepciones
- `app/stt/realtime_client.py` — timeout, late-callback guard, error callback + consecutive_errors
- `app/ui/main_window.py` — _stt_error signal, _on_stt_error slot, bridge _emit_stt_error_async
- `app/geo/pickup_repair.py` — correction bonus fix, index weight 0.6→0.2, markers cleanup, 5 tail patterns
- `app/geo/address_normalizer.py` — observaciones lock guard, poble→poblenou eliminado de _TOKEN_EQUIV

Copia de los 5 archivos en el momento pre-eval: `checkpoints/fase1_fase2_estable_pre_eval/`

## Eval sobre 7 llamadas (2026-04-12)
| Estado | N | % |
|---|---|---|
| validated | 2 | 29% |
| usable_review | 1 | 14% |
| partial_match | 3 | 43% |
| outside_amb | 0 | 0% |
| unknown_or_incomplete | 1 | 14% |
| **usable_total** | **3** | **43%** |

## Observaciones del eval
- 0 regressions detectadas en direcciones postales limpias
- partial_match × 3: todos debidos a errores upstream (STT deforma "Nicolau"→"812", LLM no incorpora corrección "Lepant→Laietana 71")
- DESCONOCIDA correctamente emitida cuando el cliente no sabe la dirección
- Santa Ana 13 validado con observaciones separadas — caso de control positivo

## Siguiente frente identificado
`service_extractor.py` — prompt engineering para detección de patrones de corrección de recogida.
Caso disparador: CLID 938560501 — "de la calle Lepant… Este es de Vía Laietana 71… Ah, perfecto."
El LLM entregó "Lepant" porque los patrones de corrección actuales no cubren:
- "Este es de X" / "es de X" (corrección en tercera persona / reformulación)
- confirmación operador ("ah, perfecto") como señal de que la última dirección es la válida
- regla explícita "cuando hay dos candidatos, prevalece el confirmado más recientemente"

## Pendiente para ciclos siguientes
- `_geocode_with_retry()` dead code en address_normalizer.py (~línea 511): `final_status = status2 if acceptable2 else status2`
- `AuthenticationError: raise` en service_extractor.py — convertir a signal/event (worker thread safety)
- `timeout=20.0` → `Timeout(connect=5.0, read=20.0)` en realtime_client.py
- eval_recordings.py: DEFAULT_FOLDER hardcoded, floor division en _pct(), dead reset lines 149-151
- Fase 3: ServiceDraft domain model, structured logging, regression test suite
