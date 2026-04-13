# BASELINE_V2_1_3

Fecha: 2026-04-11

## Estado
Baseline estable tras corregir:
- falsos positivos de aeropuerto en recogida
- sentence splitting de transcripts sin espacio tras ? ! .
- POI override incorrecto sobre direcciones postales

## Resultado agregado actual
Muestra total evaluada: 18 llamadas

- validated: 5 (28%)
- usable_review: 2 (11%)
- partial_match: 2 (11%)
- outside_amb: 7 (39%)
- unknown_incomplete: 2 (11%)

Utilizable total:
- 11/18 (61%)

## Bugs críticos eliminados
- No se detectan falsos positivos de recogida convertida en aeropuerto por mencionar destino
- Split de frases corregido para transcripts compactos
- Repair + preprocessor ya no mezclan destino con recogida como antes

## Limitaciones conocidas
- Fallos por STT/LLM en nombres de calle deformados
- POIs/hoteles/clínicas no conocidas localmente siguen cayendo en outside_amb
- Algunas direcciones válidas aún dependen de alias o contexto local para resolverse

## Siguiente paso aprobado
Implementar tabla local `known_pickup_aliases.py` con:
- clínicas
- CAPs
- hospitales
- hoteles
- recintos
- puntos frecuentes del AMB
