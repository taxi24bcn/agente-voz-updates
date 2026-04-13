/**
 * Webhook de centralización de sesiones — Agente Voz Taxi24H
 *
 * Configuración en PropertiesService (Project Properties → Script Properties):
 *   WEBHOOK_TOKEN         → token secreto compartido con los PCs
 *   SESIONES_ROOT_FOLDER_ID → ID de la carpeta "AgenteVoz/sesiones/" en Drive
 *   SHEET_ID              → ID del Google Sheet de sesiones
 *
 * Versión soportada: schema_version 2
 *
 * Despliegue:
 *   Extensions → Apps Script → Deploy → New deployment → Web App
 *   Execute as: Me (tu cuenta)
 *   Who has access: Anyone (anónimo, auth por token en body)
 */

// ── Constantes ────────────────────────────────────────────────────────────────

var SUPPORTED_SCHEMA_VERSION = 2;

var SHEET_COLUMNS = [
  "session_id",
  "timestamp",
  "pc_saved_at",
  "app_version",
  "pc_name",
  "cliente",
  "telefono",
  "recogida_raw",
  "recogida_final",
  "destino",
  "fecha_servicio",
  "hora_servicio",
  "tipo_servicio",
  "geo_status",
  "geo_municipio",
  "needs_geo_review",
  "geo_review_reasons",
  "needs_quality_review",
  "quality_review_reasons",
  "json_file_id",
  "txt_file_id",
  "upload_status",
  // diagnóstico
  "geo_failure_reason",
  "google_query_used",
  "google_result_count",
  "chosen_candidate",
  "chosen_place_id",
  "was_retry_used",
  "cache_hit",
  "operator_locked"
];


// ── Entry point ───────────────────────────────────────────────────────────────

function doPost(e) {
  // 1. Parsear body
  var body;
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonResponse({ status: "invalid_payload", reason: "empty_body" });
    }
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonResponse({ status: "invalid_payload", reason: "json_parse_error" });
  }

  // 2. Validar token ANTES del lock
  var props = PropertiesService.getScriptProperties();
  var expectedToken = props.getProperty("WEBHOOK_TOKEN");
  if (!expectedToken || body.token !== expectedToken) {
    return jsonResponse({ status: "forbidden" });
  }

  // 3. Validar payload mínimo
  var session = body.session;
  if (!session || !session.session_id || session.schema_version === undefined) {
    return jsonResponse({ status: "invalid_payload", reason: "missing_required_fields" });
  }
  if (session.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    return jsonResponse({
      status: "invalid_payload",
      reason: "unsupported_schema_version",
      expected: SUPPORTED_SCHEMA_VERSION,
      received: session.schema_version
    });
  }

  var sessionId = session.session_id;
  var txtContent = body.txt || "";

  // 4. Adquirir lock
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(10000);
  } catch (err) {
    return jsonResponse({ status: "error", reason: "lock_timeout" });
  }

  try {
    var sheetId = props.getProperty("SHEET_ID");
    var rootFolderId = props.getProperty("SESIONES_ROOT_FOLDER_ID");
    var sheet = SpreadsheetApp.openById(sheetId).getSheets()[0];

    // 5. Comprobar idempotencia por session_id en Sheet
    var existingRow = findRowBySessionId(sheet, sessionId);
    if (existingRow > 0) {
      var existingData = sheet.getRange(existingRow, 1, 1, SHEET_COLUMNS.length).getValues()[0];
      var jsonColIdx = SHEET_COLUMNS.indexOf("json_file_id");
      var txtColIdx  = SHEET_COLUMNS.indexOf("txt_file_id");
      var jsonFileId = existingData[jsonColIdx];
      var txtFileId  = existingData[txtColIdx];

      // Recovery: si la fila existe pero faltan archivos en Drive
      if (!jsonFileId || !txtFileId) {
        var recovered = recoverMissingFiles(
          rootFolderId, sessionId, session, txtContent,
          jsonFileId, txtFileId
        );
        // Actualizar fila con file_ids recuperados
        if (recovered.json_file_id) {
          sheet.getRange(existingRow, jsonColIdx + 1).setValue(recovered.json_file_id);
        }
        if (recovered.txt_file_id) {
          sheet.getRange(existingRow, txtColIdx + 1).setValue(recovered.txt_file_id);
        }
        var uploadColIdx = SHEET_COLUMNS.indexOf("upload_status");
        sheet.getRange(existingRow, uploadColIdx + 1).setValue("recovered");
        return jsonResponse({
          status: "recovered",
          session_id: sessionId,
          json_file_id: recovered.json_file_id || jsonFileId,
          txt_file_id:  recovered.txt_file_id  || txtFileId
        });
      }

      return jsonResponse({
        status: "already_exists",
        session_id: sessionId,
        json_file_id: jsonFileId,
        txt_file_id: txtFileId
      });
    }

    // 6. Localizar / crear carpeta YYYY/MM/DD
    var dayFolder = getOrCreateDayFolder(rootFolderId, sessionId);

    // 7. Crear archivos (reusar si ya existen por nombre determinista)
    var jsonFileName = sessionId + ".json";
    var txtFileName  = sessionId + ".txt";

    var jsonFileId = getOrCreateFile(dayFolder, jsonFileName, JSON.stringify(session, null, 2), "application/json");
    var txtFileId  = getOrCreateFile(dayFolder, txtFileName, txtContent, "text/plain");

    // 8. Añadir fila al Sheet
    var row = buildSheetRow(session, sessionId, jsonFileId, txtFileId);
    sheet.appendRow(row);

    return jsonResponse({
      status: "ok",
      session_id: sessionId,
      json_file_id: jsonFileId,
      txt_file_id: txtFileId
    });

  } catch (err) {
    Logger.log("doPost error: " + err.toString());
    return jsonResponse({ status: "error", reason: err.toString() });
  } finally {
    lock.releaseLock();
  }
}


// ── Helpers: Sheet ────────────────────────────────────────────────────────────

function findRowBySessionId(sheet, sessionId) {
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (data[i][0] === sessionId) {
      return i + 1; // 1-indexed
    }
  }
  return -1;
}

function buildSheetRow(session, sessionId, jsonFileId, txtFileId) {
  var geo = session.geo || {};
  var diag = session.geo_diagnostics || {};
  var fields = session.fields_final || {};

  // google_query_used: retry si was_retry_used=true y pickup_query_retry no es null
  var googleQueryUsed = diag.pickup_query_sent_to_google || null;
  if (diag.was_retry_used && diag.pickup_query_retry) {
    googleQueryUsed = diag.pickup_query_retry;
  }

  var row = [
    sessionId,                                               // session_id
    session.timestamp || "",                                 // timestamp
    session.saved_at  || "",                                 // pc_saved_at
    session.app_version || "",                               // app_version
    session.pc_name || "",                                   // pc_name
    fields.cliente || "",                                    // cliente
    maskPhone(fields.telefono || ""),                        // telefono (enmascarado)
    geo.recogida_raw  || "",                                 // recogida_raw
    geo.recogida_final || "",                                // recogida_final
    fields.destino || "",                                    // destino
    fields.fecha || "",                                      // fecha_servicio
    fields.hora  || "",                                      // hora_servicio
    fields.tipo_servicio || "",                              // tipo_servicio
    geo.status || "",                                        // geo_status
    geo.municipio || "",                                     // geo_municipio
    session.needs_geo_review ? "TRUE" : "FALSE",             // needs_geo_review
    (session.geo_review_reasons || []).join(", "),           // geo_review_reasons
    session.needs_quality_review ? "TRUE" : "FALSE",         // needs_quality_review
    (session.quality_review_reasons || []).join(", "),       // quality_review_reasons
    jsonFileId,                                              // json_file_id
    txtFileId,                                               // txt_file_id
    "ok",                                                    // upload_status
    // diagnóstico
    diag.decision_reason || "",                              // geo_failure_reason
    googleQueryUsed || "",                                   // google_query_used
    diag.google_result_count !== undefined ? diag.google_result_count : "", // google_result_count
    diag.accepted_formatted_address || "",                   // chosen_candidate
    diag.accepted_place_id || "",                            // chosen_place_id
    diag.was_retry_used ? "TRUE" : "FALSE",                  // was_retry_used
    diag.cache_hit ? "TRUE" : "FALSE",                       // cache_hit
    diag.operator_locked ? "TRUE" : "FALSE"                  // operator_locked
  ];
  return row;
}


// ── Helpers: Drive ────────────────────────────────────────────────────────────

function getOrCreateDayFolder(rootFolderId, sessionId) {
  // sessionId format: YYYYMMDD_HHMMSS_PC_uuid8
  var dateStr = sessionId.substring(0, 8); // "20260413"
  var year  = dateStr.substring(0, 4);
  var month = dateStr.substring(4, 6);
  var day   = dateStr.substring(6, 8);

  var root = DriveApp.getFolderById(rootFolderId);
  var yearFolder  = getOrCreateSubfolder(root, year);
  var monthFolder = getOrCreateSubfolder(yearFolder, month);
  var dayFolder   = getOrCreateSubfolder(monthFolder, day);
  return dayFolder;
}

function getOrCreateSubfolder(parent, name) {
  var it = parent.getFoldersByName(name);
  if (it.hasNext()) return it.next();
  return parent.createFolder(name);
}

function getOrCreateFile(folder, name, content, mimeType) {
  var it = folder.getFilesByName(name);
  if (it.hasNext()) {
    return it.next().getId();
  }
  var file = folder.createFile(name, content, mimeType);
  return file.getId();
}

function recoverMissingFiles(rootFolderId, sessionId, session, txtContent, existingJsonId, existingTxtId) {
  var dayFolder = getOrCreateDayFolder(rootFolderId, sessionId);
  var jsonFileId = existingJsonId;
  var txtFileId  = existingTxtId;

  if (!jsonFileId) {
    jsonFileId = getOrCreateFile(
      dayFolder,
      sessionId + ".json",
      JSON.stringify(session, null, 2),
      "application/json"
    );
  }
  if (!txtFileId) {
    txtFileId = getOrCreateFile(
      dayFolder,
      sessionId + ".txt",
      txtContent,
      "text/plain"
    );
  }
  return { json_file_id: jsonFileId, txt_file_id: txtFileId };
}


// ── Helpers: privacidad ───────────────────────────────────────────────────────

function maskPhone(phone) {
  var digits = (phone || "").replace(/\D/g, "");
  if (digits.length >= 6) {
    return digits.substring(0, 3) +
           "*".repeat(digits.length - 5) +
           digits.substring(digits.length - 2);
  }
  return "***";
}


// ── Helpers: respuesta HTTP ───────────────────────────────────────────────────

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


// ── Mantenimiento: cabecera del Sheet ─────────────────────────────────────────

/**
 * Ejecutar manualmente UNA VEZ tras crear el Sheet para añadir la cabecera.
 * Menu: Run → setupSheetHeader
 */
function setupSheetHeader() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = props.getProperty("SHEET_ID");
  var sheet = SpreadsheetApp.openById(sheetId).getSheets()[0];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(SHEET_COLUMNS);
    sheet.setFrozenRows(1);
    // Formato de cabecera
    sheet.getRange(1, 1, 1, SHEET_COLUMNS.length)
      .setBackground("#4A4A4A")
      .setFontColor("#FFFFFF")
      .setFontWeight("bold");
  }
}
