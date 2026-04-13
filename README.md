# Agente Voz Taxi24H

Asistente de voz para operadores de taxi. Escucha las llamadas en tiempo real y extrae automáticamente los datos del servicio: recogida, destino, nombre del cliente y número de teléfono.

---

## Descarga

Descarga el instalador desde la sección [Releases](https://github.com/taxi24bcn/agente-voz-updates/releases/latest) → archivo `AgenteVozTaxi24H-X.X.X-Setup.exe`

---

## Requisitos previos

Antes de instalar el agente necesitas estos dos programas:

### 1. VB-CABLE — cable de audio virtual

Captura el audio de las llamadas telefónicas.

**Descarga oficial:** [vb-audio.com/Cable](https://vb-audio.com/Cable/)

1. Descargar e instalar VB-CABLE
2. Reiniciar el PC tras la instalación
3. Verificar que aparecen "CABLE Input" y "CABLE Output" en los dispositivos de audio de Windows

### 2. MicroSIP — softphone SIP

Softphone con el que se gestionan las llamadas.

**Descarga oficial:** [microsip.org/downloads](https://www.microsip.org/downloads)

1. Descargar e instalar MicroSIP
2. Añadir la cuenta SIP (servidor, usuario, contraseña) — datos del proveedor de telefonía
3. En MicroSIP → Herramientas → Ajustes, configurar audio:

| Campo | Valor |
|---|---|
| **Dispositivo de llamada** | Predeterminado |
| **Altavoz** | **CABLE Input (VB-Audio Virtual Cable)** |
| **Micrófono** | Micrófono físico del PC (Realtek, Conexant, etc. — cualquiera que NO sea CABLE) |

### 3. Configurar audio de Windows — para que el operador oiga las llamadas

Al poner el altavoz de MicroSIP en CABLE Input, el audio de la llamada va al cable virtual y el operador no lo oye directamente. Hay que activar la repetición en Windows:

1. Pulsar `Windows + R`, escribir `mmsys.cpl` y pulsar Enter
2. Ir a la pestaña **Grabacion**
3. Click derecho en **CABLE Output** → **Propiedades**
4. Ir a la pestaña **Escuchar**
5. Marcar **"Escuchar este dispositivo"**
6. En el desplegable **"Reproducir en este dispositivo"** → seleccionar los **altavoces fisicos** del PC (Realtek, Conexant, etc.)
7. Pulsar **Aceptar**

Esto hace que el audio de la llamada llegue a la app (para transcribir) Y a los altavoces (para que el operador oiga) al mismo tiempo.

---

## Instalación

1. Ejecutar `AgenteVozTaxi24H-X.X.X-Setup.exe` como administrador
2. Seguir el asistente
3. Al finalizar, la app se abre automáticamente

---

## Configuración inicial (primer arranque)

Al abrir la app por primera vez aparece la pantalla de configuración:

| Campo | Qué poner |
|---|---|
| **OpenAI API Key** | Clave de la cuenta OpenAI (`sk-...`) |
| **Google Maps API Key** | Clave de Google Maps Geocoding API (opcional) |
| **Micrófono operador** | Seleccionar el micrófono físico del operador |

Las claves se guardan localmente en `%LOCALAPPDATA%\Taxi24H\AgenteVoz\config\.env` y nunca se sincronizan.

---

## Requisitos del sistema

- Windows 10 / 11 (64 bits)
- 4 GB RAM mínimo
- Conexión a internet (STT y geocodificación)
- Cuenta OpenAI con acceso a `gpt-4o-transcribe` y `gpt-4o-mini`

---

## Versiones

| Versión | Fecha | Novedades |
|---|---|---|
| 2.3.0 | 2026-04-13 | Centralización sesiones en Drive + Sheets + trazabilidad geo completa |
| 2.2.0 | 2026-04-13 | Auto-detección micrófono + guía audio Windows en README |
| 2.1.7 | 2026-04-13 | Fix selector de micrófono en configuración inicial |
| 2.1.6 | 2026-04-12 | Fix geocodificación intersecciones |
| 2.1.5 | 2026-04-12 | Mejora geocodificación municipio + catalán |
| 2.1.4 | 2026-04-12 | Config dialog en app, instalador 46 MB |
| 2.1.3 | 2026-04-11 | Baseline estable |

Ver historial completo en [CHANGELOG.md](CHANGELOG.md)

---

## Para desarrolladores — publicar una nueva versión

1. Generar el instalador con `build_release.bat`
2. Calcular SHA-256: `(Get-FileHash "AgenteVozTaxi24H-X.X.X-Setup.exe" -Algorithm SHA256).Hash.ToLower()`
3. Crear release en GitHub con el `Setup.exe` adjunto
4. Actualizar `version.json` con la nueva versión, URL y SHA-256

### Formato de version.json

```json
{
  "version": "X.Y.Z",
  "download_url": "https://github.com/taxi24bcn/agente-voz-updates/releases/download/vX.Y.Z/AgenteVozTaxi24H-X.Y.Z-Setup.exe",
  "release_notes": "Descripción de los cambios.",
  "sha256": "hash_sha256_del_instalador_en_minusculas"
}
```
