# Configuración centralización en la nube — Agente Voz Taxi24H

Esta guía explica cómo conectar los PCs de operador a Google Drive y Google Sheets para centralizar y auditar las sesiones de geocodificación.

---

## Arquitectura

```
PC operador → Guardar TXT
    ├─→ SESSIONS_DIR local: TXT + JSON (backup permanente)
    ├─→ PENDING_DIR local: .pending.json si falla la subida
    └─→ HTTP POST → Google Apps Script
              ├─→ Drive compartido: JSON + TXT en carpeta YYYY/MM/DD
              └─→ Google Sheet: fila resumen + diagnóstico geo
```

---

## Paso 1 — Crear el Google Sheet

1. Ir a [sheets.google.com](https://sheets.google.com) → **Nuevo**
2. Renombrar la hoja como `Sesiones Taxi24H`
3. Anotar el **ID del Sheet** (parte de la URL entre `/d/` y `/edit`):
   ```
   https://docs.google.com/spreadsheets/d/  →  1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms  ← este ID
   ```

La cabecera se creará automáticamente con `setupSheetHeader()` (paso 4).

---

## Paso 2 — Crear la carpeta en Drive

1. Ir a [drive.google.com](https://drive.google.com)
2. Crear la carpeta `AgenteVoz/sesiones/` (puede ser en Unidad Compartida o en Mi Unidad)
3. Abrir la carpeta → URL del navegador → anotar el **ID de la carpeta**:
   ```
   https://drive.google.com/drive/folders/  →  1a2b3c4d5e6f7g8h9i0j  ← este ID
   ```

---

## Paso 3 — Crear el Apps Script

1. Ir a [script.google.com](https://script.google.com) → **Nuevo proyecto**
2. Renombrar como `AgenteVozWebhook`
3. Borrar el contenido del editor y pegar el contenido de [google_apps_script.js](google_apps_script.js)
4. **Guardar** (Ctrl+S)

### Configurar Script Properties

En el editor de Apps Script:
1. Menú **Proyecto** → **Propiedades del proyecto** → pestaña **Propiedades de script**
2. Añadir las tres propiedades:

| Nombre | Valor |
|---|---|
| `WEBHOOK_TOKEN` | Un token secreto seguro (ver generación abajo) |
| `SHEET_ID` | ID del Sheet del paso 1 |
| `SESIONES_ROOT_FOLDER_ID` | ID de la carpeta del paso 2 |

**Generar un token seguro** (ejecutar en PowerShell):
```powershell
[System.Web.Security.Membership]::GeneratePassword(32, 4)
# O más sencillo:
-join ((65..90 + 97..122 + 48..57) | Get-Random -Count 40 | % {[char]$_})
```

Ejemplo de token válido: `tk_a7f3b2e9d1c845f6b2a09e3d7c1f8b4e2d6a9f3`

### Inicializar la cabecera del Sheet

1. En el editor de Apps Script, seleccionar la función `setupSheetHeader` en el desplegable de funciones
2. Hacer clic en **Ejecutar**
3. Autorizar los permisos cuando se pida (acceso a Sheets y Drive)
4. Verificar que la primera fila del Sheet tiene las columnas en negativo sobre fondo oscuro

---

## Paso 4 — Desplegar como Web App

1. En el editor de Apps Script → **Implementar** → **Nueva implementación**
2. Tipo: **Web App**
3. Configuración:
   - **Ejecutar como:** `Yo (tu@cuenta.com)`
   - **Quién tiene acceso:** `Cualquier usuario` (la auth real es por token en el body)
4. Hacer clic en **Implementar**
5. **Copiar la URL** que aparece:
   ```
   https://script.google.com/macros/s/AKfycbxxxxxxxxxxxxxxxx/exec
   ```

> **Importante:** cada vez que modifiques el script debes crear una **nueva implementación** (no usar "Administrar implementaciones" → Editar). La URL cambia con cada nueva versión.

---

## Paso 5 — Configurar cada PC

En el archivo `.env` de cada PC operador (ubicado en `%LOCALAPPDATA%\Taxi24H\AgenteVoz\config\.env`):

```env
CLOUD_WEBHOOK_URL=https://script.google.com/macros/s/AKfycbxxxxxxxxxxxxxxxx/exec
CLOUD_WEBHOOK_TOKEN=tk_a7f3b2e9d1c845f6b2a09e3d7c1f8b4e2d6a9f3
```

- La URL es la del paso 4
- El token es el mismo que configuraste en Script Properties
- Si ambas variables están vacías → la app solo guarda localmente, sin errores

Para editar el `.env` directamente:
1. Pulsar `Windows + R` → escribir `%LOCALAPPDATA%\Taxi24H\AgenteVoz\config` → Enter
2. Abrir `.env` con el Bloc de notas
3. Añadir las dos líneas al final y guardar
4. Reiniciar la aplicación

---

## Verificación

### Test 1 — Subida básica
1. Realizar o simular una llamada con la app
2. Pulsar **Guardar TXT**
3. Verificar en Google Sheet → debe aparecer una nueva fila
4. Verificar en Drive → carpeta `YYYY/MM/DD` → debe tener `session_id.json` y `session_id.txt`

### Test 2 — Idempotencia
1. Guardar la misma sesion dos veces (no es posible desde la UI, pero sí reintentando un .pending)
2. Verificar que el Sheet no tiene filas duplicadas con el mismo `session_id`

### Test 3 — Token inválido
1. Poner un token incorrecto en `.env`
2. Guardar una sesion → el JSON local debe tener `upload_status: "auth_failed"`

### Test 4 — Sin conexión
1. Desconectar red y guardar una sesion
2. Verificar que aparece un archivo `.pending.json` en `%LOCALAPPDATA%\Taxi24H\AgenteVoz\logs\pending\`
3. Reconectar y reiniciar la app → la sesion debe subirse automáticamente

### Test 5 — Solo local (sin configuración cloud)
1. Dejar `CLOUD_WEBHOOK_URL` vacío en `.env`
2. Guardar una sesion → `upload_status: "local_only"` en el JSON
3. No debe aparecer ningún `.pending.json`

---

## Columnas del Google Sheet

| Columna | Descripción |
|---|---|
| `session_id` | ID único `YYYYMMDD_HHMMSS_PC_uuid8` |
| `timestamp` | Hora de la sesion |
| `pc_name` | Nombre del PC operador |
| `cliente` | Nombre del cliente |
| `telefono` | Teléfono enmascarado (primeros 3 + últimos 2) |
| `recogida_raw` | Texto original de recogida (antes de geo) |
| `recogida_final` | Dirección final mostrada en UI |
| `geo_status` | Estado geocodificación: `validated`, `partial_match`, etc. |
| `needs_geo_review` | TRUE si la dirección necesita revisión |
| `geo_failure_reason` | Motivo del fallo geo (del catálogo cerrado) |
| `google_query_used` | Query que se envió a Google (o retry si se usó) |
| `google_result_count` | Número de candidatos devueltos por Google |
| `chosen_candidate` | Dirección del candidato aceptado (null si ninguno) |
| `was_retry_used` | TRUE si se usó query de reintento |
| `cache_hit` | TRUE si el resultado vino de caché |
| `operator_locked` | TRUE si el operador bloqueó el campo de recogida |

---

## Retención y limpieza

Los archivos JSON y TXT en Drive se guardan indefinidamente. Si deseas aplicar retención de 12 meses, puedes crear un trigger mensual en Apps Script:

```javascript
function deleteOldSessions() {
  var cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - 12);
  // Iterar sobre carpetas YYYY y borrar las de hace más de 12 meses
  // (implementar según tu estructura de carpetas)
}
```
