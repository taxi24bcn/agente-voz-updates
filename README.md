# Agente Voz Taxi24H — Canal de actualizaciones

Este repositorio es el canal público de distribución de actualizaciones del Agente Voz Taxi24H.

## Contenido

- `version.json` — versión actual disponible, URL de descarga y hash SHA-256
- Releases — instaladores `Setup.exe` adjuntos a cada release

## Formato de version.json

```json
{
  "version": "X.Y.Z",
  "download_url": "https://github.com/taxi24bcn/agente-voz-updates/releases/download/vX.Y.Z/AgenteVozTaxi24H-X.Y.Z-Setup.exe",
  "release_notes": "Descripción de los cambios.",
  "sha256": "hash_sha256_del_instalador_en_minusculas"
}
```

## Cómo publicar una nueva versión

1. Generar el instalador con `build_release.bat`
2. Calcular SHA-256: `(Get-FileHash "Setup.exe" -Algorithm SHA256).Hash.ToLower()`
3. Crear release en GitHub con el `Setup.exe` adjunto
4. Actualizar `version.json` con la nueva versión, URL y SHA-256
