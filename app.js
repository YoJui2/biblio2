"use strict";

const INVENTORY_PREFIX = "INV-";
const INVENTORY_WIDTH = 6;

const elements = {
  list: document.querySelector("#book-list"),
  emptyState: document.querySelector("#empty-state"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyDescription: document.querySelector("#empty-description"),
  search: document.querySelector("#search-input"),
  ocrStatus: document.querySelector("#ocr-status"),
  ocrStatusText: document.querySelector("#ocr-status-text"),
  dialog: document.querySelector("#book-dialog"),
  form: document.querySelector("#book-form"),
  dialogTitle: document.querySelector("#dialog-title"),
  saveButton: document.querySelector("#save-button"),
  formError: document.querySelector("#form-error"),
  toast: document.querySelector("#toast"),
  csvInput: document.querySelector("#csv-input"),
  ocrInput: document.querySelector("#ocr-input"),
  ocrDialogInput: document.querySelector("#ocr-dialog-input"),
  ocrPreview: document.querySelector("#ocr-preview"),
  ocrImage: document.querySelector("#ocr-image"),
  ocrNote: document.querySelector("#ocr-note"),
  ocrAlert: document.querySelector("#ocr-alert"),
  ocrAlertMessage: document.querySelector("#ocr-alert-message"),
  themeToggle: document.querySelector("#theme-toggle"),
  trashDialog: document.querySelector("#trash-dialog"),
  trashList: document.querySelector("#trash-list"),
  trashEmpty: document.querySelector("#trash-empty"),
  trashCount: document.querySelector("#trash-count"),
  emptyTrashButton: document.querySelector("#empty-trash-button"),
  scanDialog: document.querySelector("#scan-dialog"),
  scanList: document.querySelector("#scan-list"),
  scanError: document.querySelector("#scan-error"),
  scanSaveButton: document.querySelector("#scan-save-button"),
  scanAlert: document.querySelector("#scan-alert"),
  scanAlertMessage: document.querySelector("#scan-alert-message"),
  scanPreview: document.querySelector("#scan-preview"),
  scanImage: document.querySelector("#scan-image"),
  scanNote: document.querySelector("#scan-note"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmEyebrow: document.querySelector("#confirm-eyebrow"),
  confirmTitle: document.querySelector("#confirm-title"),
  confirmMessage: document.querySelector("#confirm-message"),
  confirmActions: document.querySelector("#confirm-actions"),
  inventoryView: document.querySelector("#inventory-view"),
  chatPanel: document.querySelector("#chatbot"),
  chatLog: document.querySelector("#chat-log"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  chatSendButton: document.querySelector("#chat-send-button"),
  chatClearButton: document.querySelector("#chat-clear-button"),
  chatStatus: document.querySelector("#chat-status"),
  chatModelPill: document.querySelector("#chat-model-pill"),
  chatSuggestions: document.querySelector("#chat-suggestions"),
  breadcrumbCurrent: document.querySelector("#breadcrumb-current"),
  activityList: document.querySelector("#activity-list"),
  activityClear: document.querySelector("#activity-clear"),
  activityButton: document.querySelector("#activity-button"),
  activityDialog: document.querySelector("#activity-dialog"),
  activityCount: document.querySelector("#activity-count"),
};

let books = [];
let trash = [];
let editingId = null;
let toastTimeout;
let ocrImageUrl = null;
let scanImageUrl = null;
let scanRecords = [];
let chatHistory = [];
let chatBusy = false;
let chatStarted = false;
let activityEntries = [];
const ACTIVITY_KEY = "biblioteca-actividad";
const ACTIVITY_LIMIT = 60;

const CHAT_WELCOME = "Hola, soy el asistente de la biblioteca. Preguntame por un libro (título, autor o un número como INV-000062) o por el funcionamiento de la biblioteca: préstamos, cómo asociarte, horarios y computadoras. Si no está en el catálogo o no lo sé, te lo digo.";

function applyTheme(theme) {
  const selectedTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = selectedTheme;
  const switchTo = selectedTheme === "dark" ? "claro" : "oscuro";
  elements.themeToggle.setAttribute("aria-label", `Cambiar a tema ${switchTo}`);
  elements.themeToggle.title = `Cambiar a tema ${switchTo}`;
  document.querySelector("#theme-toggle-label").textContent = `Tema ${switchTo}`;
  document.querySelector('meta[name="theme-color"]').content = selectedTheme === "dark" ? "#171b19" : "#f6f7f5";
}

function loadTheme() {
  try {
    applyTheme(localStorage.getItem("biblioteca-tema") || "dark");
  } catch (error) {
    console.error("No se pudo leer la preferencia de tema.", error);
    applyTheme("dark");
  }
}

function toggleTheme() {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(nextTheme);
  try {
    localStorage.setItem("biblioteca-tema", nextTheme);
  } catch (error) {
    console.error("No se pudo guardar la preferencia de tema.", error);
    showToast("El tema cambió, pero no se pudo guardar la preferencia.");
  }
}

function fromApiRecord(record) {
  return {
    id: String(record.id),
    title: record.title,
    author: record.author,
    imprint: record.imprint,
    binding: record.binding,
    inventorySource: record.inventory_source,
    inventoryNumbers: record.inventory_numbers,
    deletedAt: record.deleted_at,
  };
}

function httpError(message, status, detail) {
  const error = new Error(message);
  error.status = status;
  if (detail) error.detail = detail;
  return error;
}

async function apiRequest(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
    if (location.protocol === "file:") {
      throw httpError("El panel se abrió como archivo. Abrilo desde http://localhost:8000/", 0);
    }
    throw httpError(
      "No se pudo conectar con el servidor. Verificá que la API esté en ejecución.",
      0,
    );
  }
  const contentType = response.headers.get("content-type") || "";
  let body = null;
  try {
    body = contentType.includes("application/json")
      ? await response.json()
      : { detail: (await response.text()).slice(0, 500) };
  } catch (cause) {
    body = null;
  }
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join(" ")
      : detail || `La API respondió con el estado ${response.status}.`;
    throw httpError(message, response.status, detail);
  }
  return body;
}

async function refreshBooks() {
  try {
    const records = await apiRequest("/api/books");
    books = records.map(fromApiRecord);
    renderBooks();
    document.querySelector("#storage-label").textContent = "Conectado a MySQL";
    document.querySelector(".storage-dot").classList.add("connected");
    return true;
  } catch (error) {
    console.error("No se pudo cargar el catálogo desde la API.", error);
    document.querySelector("#storage-label").textContent = "Sin conexión al servidor";
    document.querySelector(".storage-dot").classList.remove("connected");
    showToast(`No se pudo conectar con FastAPI: ${error.message}`);
    return false;
  }
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function renderInline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
}

function splitTableRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function renderMarkdownTable(header, rows) {
  const head = header.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="chat-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderMarkdown(text) {
  const lines = escapeHTML(String(text ?? "")).replace(/\r\n?/g, "\n").split("\n");
  const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
  const isTableSeparator = (line) => /-/.test(line) && /^[\s|:-]+$/.test(line);
  const isBullet = (line) => /^\s*[-*]\s+\S/.test(line);
  const isOrdered = (line) => /^\s*\d+[.)]\s+\S/.test(line);
  const html = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitTableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      html.push(renderMarkdownTable(header, rows));
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 5);
      html.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }
    if (isBullet(line)) {
      const items = [];
      while (i < lines.length && isBullet(lines[i])) {
        items.push(`<li>${renderInline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`);
        i += 1;
      }
      html.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (isOrdered(line)) {
      const items = [];
      while (i < lines.length && isOrdered(lines[i])) {
        items.push(`<li>${renderInline(lines[i].replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
        i += 1;
      }
      html.push(`<ol>${items.join("")}</ol>`);
      continue;
    }
    html.push(`<p>${renderInline(line)}</p>`);
    i += 1;
  }
  return html.join("");
}

function formatInventoryNumber(number) {
  return `${INVENTORY_PREFIX}${String(number).padStart(INVENTORY_WIDTH, "0")}`;
}

function parseInventoryRange(value) {
  const text = String(value ?? "").trim();
  if (!text) throw new Error("Usá un número o rango, por ejemplo 62 o 62-71.");
  const tokenPattern = /(?:INV\s*[-:]?\s*)?(\d+)(?:\s*[-–—]\s*(?:INV\s*[-:]?\s*)?(\d+))?/gi;
  const numbers = [];
  let lastIndex = 0;
  let match;
  while ((match = tokenPattern.exec(text)) !== null) {
    const between = text.slice(lastIndex, match.index);
    if (between && !/^[\s,;/]+$/.test(between)) {
      throw new Error("Usá números o rangos separados por comas, por ejemplo 62, 65 o 62-71.");
    }
    lastIndex = tokenPattern.lastIndex;
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) {
      throw new Error("El rango debe ser ascendente y usar números mayores que cero.");
    }
    if (end - start >= 10000) throw new Error("El rango no puede superar los 10.000 ejemplares.");
    for (let number = start; number <= end; number += 1) numbers.push(number);
  }
  const tail = text.slice(lastIndex);
  if (tail && !/^[\s,;/]*$/.test(tail)) {
    throw new Error("Usá números o rangos separados por comas, por ejemplo 62, 65 o 62-71.");
  }
  if (!numbers.length) throw new Error("Usá un número o rango, por ejemplo 62 o 62-71.");
  return [...new Set(numbers)].sort((a, b) => a - b);
}

function inventoryText(numbers) {
  if (!numbers.length) return "Sin número";
  const sorted = [...numbers].sort((a, b) => a - b);
  const ranges = [];
  let start = sorted[0];
  let end = start;
  for (const number of sorted.slice(1)) {
    if (number === end) continue;
    if (number === end + 1) {
      end = number;
      continue;
    }
    ranges.push(start === end ? formatInventoryNumber(start) : `${formatInventoryNumber(start)}–${formatInventoryNumber(end)}`);
    start = number;
    end = number;
  }
  ranges.push(start === end ? formatInventoryNumber(start) : `${formatInventoryNumber(start)}–${formatInventoryNumber(end)}`);
  return ranges.join(", ");
}

function getFilteredBooks() {
  const query = elements.search.value.trim().toLocaleLowerCase("es");
  return books.filter((book) => {
    const searchable = [
      book.title, book.author, book.imprint, book.binding,
      ...book.inventoryNumbers.map(formatInventoryNumber),
      book.inventorySource,
    ].join(" ").toLocaleLowerCase("es");
    return !query || searchable.includes(query);
  });
}

function renderStats() {
  const allNumbers = books.flatMap((book) => book.inventoryNumbers);
  document.querySelector("#stat-titles").textContent = books.length.toLocaleString("es");
  document.querySelector("#stat-copies").textContent = allNumbers.length.toLocaleString("es");
  document.querySelector("#stat-range").textContent = allNumbers.length
    ? `${formatInventoryNumber(Math.min(...allNumbers))} – ${formatInventoryNumber(Math.max(...allNumbers))}`
    : "—";
  document.querySelector("#nav-count").textContent = books.length.toLocaleString("es");
  const clearButton = document.querySelector("#clear-all-button");
  if (clearButton) clearButton.disabled = books.length === 0;
}

function renderBooks() {
  renderStats();
  const filtered = getFilteredBooks();
  document.querySelector("#results-count").textContent = filtered.length.toLocaleString("es");
  document.querySelector("#footer-summary").textContent = `Mostrando ${filtered.length.toLocaleString("es")} de ${books.length.toLocaleString("es")} registros`;
  elements.emptyState.hidden = filtered.length > 0;
  elements.list.hidden = filtered.length === 0;
  if (!filtered.length) {
    const isFiltering = books.length > 0;
    elements.emptyTitle.textContent = isFiltering ? "No encontramos resultados" : "Todavía no hay registros";
    elements.emptyDescription.textContent = isFiltering
      ? "Probá con otra búsqueda."
      : "Importá tu CSV para cargar el catálogo y expandir los rangos de inventario.";
    document.querySelector("#empty-add-button").textContent = isFiltering ? "Agregar registro" : "Agregar primer registro";
    document.querySelector("#empty-add-button").hidden = isFiltering;
  }
  elements.list.innerHTML = filtered.map((book, index) => {
    const title = escapeHTML(book.title);
    const ids = inventoryText(book.inventoryNumbers);
    return `<tr>
      <td class="inventory-number-cell" title="${escapeHTML(ids)}">${escapeHTML(ids)}</td>
      <td><div class="book-cell"><span class="book-cover cover-${index % 6}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 4.5h11a3 3 0 0 1 3 3v12H8a3 3 0 0 1-3-3v-12Z"/><path d="M5 16.5a3 3 0 0 1 3-3h11"/></svg></span><span class="book-copy"><span class="book-title" title="${title}">${title}</span><span class="book-author">${escapeHTML(book.author)}</span></span></div></td>
      <td class="imprint-cell" title="${escapeHTML(book.imprint)}">${escapeHTML(book.imprint || "—")}</td>
      <td><span class="unit-count">${book.inventoryNumbers.length}</span></td>
      <td class="binding-cell" title="${escapeHTML(book.binding)}">${escapeHTML(book.binding || "—")}</td>
      <td class="row-actions"><button class="icon-button row-menu-toggle" type="button" aria-label="Acciones para ${title}" aria-expanded="false" data-id="${escapeHTML(book.id)}">···</button></td>
    </tr>`;
  }).join("");
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => elements.toast.classList.remove("visible"), 3500);
}

function loadActivity() {
  try {
    const raw = localStorage.getItem(ACTIVITY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    activityEntries = Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    activityEntries = [];
  }
}

function saveActivity() {
  try {
    localStorage.setItem(ACTIVITY_KEY, JSON.stringify(activityEntries));
  } catch (error) {
    console.error("No se pudo guardar la actividad reciente.", error);
  }
}

function activityTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const sameDay = date.toDateString() === new Date().toDateString();
  return new Intl.DateTimeFormat(
    "es-AR",
    sameDay
      ? { hour: "2-digit", minute: "2-digit" }
      : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" },
  ).format(date);
}

function renderActivity() {
  if (elements.activityCount) {
    elements.activityCount.textContent = activityEntries.length.toLocaleString("es");
    elements.activityCount.hidden = activityEntries.length === 0;
  }
  if (!elements.activityList) return;
  if (!activityEntries.length) {
    elements.activityList.innerHTML = '<li class="activity-empty">Sin movimientos todavía.</li>';
    return;
  }
  elements.activityList.innerHTML = activityEntries.map((entry) => {
    const kind = ["ok", "warn", "error", "info"].includes(entry.kind) ? entry.kind : "info";
    return `<li class="activity-item ${kind}"><span class="activity-dot" aria-hidden="true"></span><span class="activity-body"><span class="activity-text">${escapeHTML(entry.message)}</span><span class="activity-time">${escapeHTML(activityTime(entry.at))}</span></span></li>`;
  }).join("");
}

function logActivity(message, kind = "info") {
  activityEntries.unshift({ at: new Date().toISOString(), kind, message: String(message) });
  if (activityEntries.length > ACTIVITY_LIMIT) activityEntries.length = ACTIVITY_LIMIT;
  saveActivity();
  renderActivity();
}

function clearActivity() {
  activityEntries = [];
  saveActivity();
  renderActivity();
}

function openChoiceDialog({ title, message, eyebrow = "CONFIRMACIÓN", options }) {
  const dialog = elements.confirmDialog;
  if (!dialog) return Promise.resolve(null);
  elements.confirmEyebrow.textContent = eyebrow;
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmActions.innerHTML = "";
  return new Promise((resolve) => {
    let settled = false;
    const onClose = () => {
      if (settled) return;
      settled = true;
      dialog.removeEventListener("close", onClose);
      dialog.removeEventListener("cancel", onCancel);
      const selected = options.find(
        (option) => String(option.value) === dialog.returnValue,
      );
      resolve(selected ? selected.value : null);
    };
    const onCancel = (event) => {
      event.preventDefault();
      dialog.returnValue = "";
      dialog.close();
    };
    options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button ${option.className || "button-cancel"}`;
      button.textContent = option.label;
      button.addEventListener("click", () => {
        dialog.returnValue = String(option.value);
        dialog.close();
      });
      elements.confirmActions.append(button);
    });
    dialog.returnValue = "";
    dialog.addEventListener("close", onClose);
    dialog.addEventListener("cancel", onCancel);
    dialog.showModal();
  });
}

function confirmAction(message, config = {}) {
  return openChoiceDialog({
    title: config.title || "Confirmar acción",
    message,
    eyebrow: config.eyebrow,
    options: [
      { value: false, label: config.cancelLabel || "Cancelar", className: "button-cancel" },
      {
        value: true,
        label: config.confirmLabel || "Confirmar",
        className: config.tone === "danger" ? "button-danger" : "button-primary",
      },
    ],
  }).then((value) => value === true);
}

function resetOcrPreview() {
  if (ocrImageUrl) {
    URL.revokeObjectURL(ocrImageUrl);
    ocrImageUrl = null;
  }
  elements.ocrPreview.hidden = true;
  elements.ocrImage.removeAttribute("src");
  elements.ocrNote.textContent = "";
}

function hideOcrAlert() {
  elements.ocrAlert.hidden = true;
  elements.ocrAlertMessage.textContent = "";
}

function showOcrAlert(message, kind = "error") {
  elements.ocrAlertMessage.textContent = message;
  elements.ocrAlert.classList.toggle("ocr-alert-error", kind === "error");
  elements.ocrAlert.classList.toggle("ocr-alert-ok", kind === "ok");
  elements.ocrAlert.hidden = false;
  elements.ocrAlert.scrollIntoView({ block: "nearest" });
}

function openDialog(book = null) {
  editingId = book ? book.id : null;
  resetOcrPreview();
  hideOcrAlert();
  elements.form.reset();
  elements.formError.hidden = true;
  elements.dialogTitle.textContent = book ? "Editar registro" : "Agregar registro";
  elements.saveButton.textContent = book ? "Guardar cambios" : "Agregar registro";
  if (book) {
    elements.form.elements.inventoryRange.value = compressNumbers(book.inventoryNumbers);
    for (const name of ["title", "author", "imprint", "binding"]) {
      elements.form.elements[name].value = book[name] || "";
    }
  }
  elements.dialog.showModal();
  elements.form.elements.inventoryRange.focus();
}

function fillFormFromOcr(result) {
  const range = result.inventory_numbers.length
    ? compressNumbers(result.inventory_numbers)
    : "";
  elements.form.elements.inventoryRange.value = range;
  elements.form.elements.title.value = result.title || "";
  elements.form.elements.author.value = result.author || "";
  elements.form.elements.imprint.value = result.imprint || "";
  elements.form.elements.binding.value = result.binding || "";
}

function showOcrSuggestion(result, imageUrl) {
  const records = Array.isArray(result.records) ? result.records : [];
  if (records.length > 1) {
    openScanRecords(result, imageUrl, records);
    return;
  }
  const single = records[0] || result;
  if (elements.dialog.open) {
    resetOcrPreview();
    elements.formError.hidden = true;
  } else {
    openDialog();
  }
  fillFormFromOcr(single);
  ocrImageUrl = imageUrl;
  elements.ocrImage.src = imageUrl;
  elements.ocrPreview.hidden = false;
  const numbers = single.inventory_numbers || [];
  const summary = numbers.length
    ? `Reconocí ${numbers.length} número(s) de inventario.`
    : "No reconocí números de inventario; completalos a mano.";
  const motor = result.engine ? ` Motor: ${result.engine}.` : "";
  elements.ocrNote.textContent = `${summary} Confianza de lectura: ${result.confidence}%.${motor}`;
  const warnings = result.warnings || [];
  if (warnings.length) {
    showOcrAlert(`Lectura completada, revisá lo siguiente: ${warnings.join(" ")}`, "warn");
  } else {
    hideOcrAlert();
  }
  elements.form.elements.title.focus();
}

function resetScanPreview() {
  if (scanImageUrl) {
    URL.revokeObjectURL(scanImageUrl);
    scanImageUrl = null;
  }
  if (elements.scanPreview) elements.scanPreview.hidden = true;
  if (elements.scanImage) elements.scanImage.removeAttribute("src");
  if (elements.scanNote) elements.scanNote.textContent = "";
}

function hideScanAlert() {
  if (!elements.scanAlert) return;
  elements.scanAlert.hidden = true;
  elements.scanAlertMessage.textContent = "";
}

function showScanAlert(message, kind = "warn") {
  if (!elements.scanAlert) return;
  elements.scanAlertMessage.textContent = message;
  elements.scanAlert.classList.toggle("ocr-alert-error", kind === "error");
  elements.scanAlert.classList.toggle("ocr-alert-warn", kind === "warn");
  elements.scanAlert.classList.toggle("ocr-alert-ok", kind === "ok");
  elements.scanAlert.hidden = false;
}

function scanRowTemplate(record, index) {
  const field = (name, label, wide = false) => `<label class="scan-cell${wide ? " scan-cell-wide" : ""}"><span>${label}</span><input data-field="${name}" value="${escapeHTML(record[name])}" /></label>`;
  return `<div class="scan-row" data-index="${index}">
    ${field("inventory", "N° inv.")}
    ${field("author", "Autor")}
    ${field("title", "Título", true)}
    ${field("binding", "Encuadernación")}
    ${field("imprint", "Pie de imprenta", true)}
    <button class="icon-button scan-remove" type="button" data-remove="${index}" aria-label="Quitar fila ${index + 1}">×</button>
  </div>`;
}

function renderScanRecords() {
  elements.scanList.innerHTML = scanRecords.map(scanRowTemplate).join("");
}

function openScanRecords(result, imageUrl, records) {
  if (elements.dialog.open) elements.dialog.close();
  scanRecords = records.map((record) => ({
    inventory: record.inventory_source || compressNumbers(record.inventory_numbers || []),
    author: record.author || "",
    title: record.title || "",
    binding: record.binding || "",
    imprint: record.imprint || "",
  }));
  resetScanPreview();
  hideScanAlert();
  elements.scanError.hidden = true;
  scanImageUrl = imageUrl;
  elements.scanImage.src = imageUrl;
  elements.scanPreview.hidden = false;
  const motor = result.engine ? ` Motor: ${result.engine}.` : "";
  const imageCount = Number(result.image_count) || 1;
  const origin = imageCount > 1
    ? ` a partir de ${imageCount} imágenes (vista previa de la primera)`
    : "";
  elements.scanNote.textContent = `Reconocí ${scanRecords.length} registros${origin}. Confianza de lectura: ${result.confidence}%.${motor}`;
  const warnings = result.warnings || [];
  const incomplete = records.filter((record) => (record.warnings || []).length > 0).length;
  if (warnings.length) {
    showScanAlert(`Revisá antes de guardar: ${warnings.join(" ")}`);
  } else if (incomplete) {
    showScanAlert(`${incomplete} fila(s) tienen datos incompletos; podés completarlos o guardarlos igual con confirmación.`);
  } else {
    hideScanAlert();
  }
  renderScanRecords();
  elements.scanDialog.showModal();
  elements.scanList.querySelector("input")?.focus();
}

async function saveScanRecords() {
  elements.scanError.hidden = true;
  const rows = [...elements.scanList.querySelectorAll(".scan-row")];
  if (!rows.length) {
    elements.scanError.textContent = "No quedan filas para guardar.";
    elements.scanError.hidden = false;
    return;
  }
  const payload = [];
  const problems = [];
  const incomplete = [];
  rows.forEach((row, index) => {
    const value = (field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    const inventory = value("inventory");
    const title = value("title");
    const author = value("author");
    let numbers = [];
    if (inventory) {
      try {
        numbers = parseInventoryRange(inventory);
      } catch (error) {
        problems.push(`Fila ${index + 1}: ${error.message}`);
        return;
      }
    }
    const missing = [];
    if (!inventory) missing.push("N° inv.");
    if (!title) missing.push("título");
    if (!author) missing.push("autor");
    if (missing.length) {
      incomplete.push(`Fila ${index + 1}: ${missing.join(", ")}.`);
    }
    payload.push({
      title,
      author,
      imprint: value("imprint"),
      binding: value("binding"),
      inventory_numbers: numbers,
      inventory_source: compressNumbers(numbers),
    });
  });
  if (problems.length) {
    elements.scanError.innerHTML = problems.map((message) => escapeHTML(message)).join("<br />");
    elements.scanError.hidden = false;
    return;
  }
  if (incomplete.length) {
    const preview = incomplete.slice(0, 4).join(" ");
    const extra = incomplete.length > 4 ? ` y ${incomplete.length - 4} fila(s) más.` : "";
    const confirmed = await confirmAction(
      `${incomplete.length} fila(s) tienen datos faltantes. ${preview}${extra} ¿Querés guardarlas igual?`,
      {
        title: "Datos incompletos",
        eyebrow: "REVISAR",
        confirmLabel: "Guardar igual",
        tone: "danger",
      },
    );
    if (!confirmed) return;
  }
  const seen = new Set();
  const duplicates = new Set();
  payload.forEach((item) => item.inventory_numbers.forEach((number) => {
    if (seen.has(number)) duplicates.add(number);
    seen.add(number);
  }));
  if (duplicates.size) {
    const formatted = [...duplicates].map(formatInventoryNumber).join(", ");
    elements.scanError.textContent = `Números de inventario repetidos entre las filas: ${formatted}.`;
    elements.scanError.hidden = false;
    return;
  }
  const catalogNumbers = new Set(books.flatMap((book) => book.inventoryNumbers));
  const conflictingRows = [];
  payload.forEach((item, index) => {
    if (item.inventory_numbers.some((number) => catalogNumbers.has(number))) {
      conflictingRows.push(index);
    }
  });
  rows.forEach((row) => row.classList.remove("scan-row-conflict"));
  if (conflictingRows.length) {
    conflictingRows.forEach((index) => rows[index].classList.add("scan-row-conflict"));
    const repeated = [...new Set(conflictingRows.flatMap(
      (index) => payload[index].inventory_numbers.filter((number) => catalogNumbers.has(number)),
    ))].map(formatInventoryNumber).join(", ");
    const choice = await openChoiceDialog({
      title: "Números ya asignados",
      eyebrow: "DUPLICADOS",
      message: `${conflictingRows.length} fila(s) usan números que ya están en el catálogo (${repeated}). Elegí «Omitir duplicados» para guardar solo las filas nuevas o «Cancelar» para corregirlas.`,
      options: [
        { value: null, label: "Cancelar y corregir", className: "button-cancel" },
        { value: "skip", label: "Omitir duplicados y guardar", className: "button-primary" },
      ],
    });
    if (choice !== "skip") return;
  }
  elements.scanSaveButton.disabled = true;
  try {
    const result = await apiRequest("/api/books/bulk?on_conflict=skip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    elements.scanDialog.close();
    const loaded = await refreshBooks();
    if (!loaded) return;
    const skippedNote = result.skipped ? ` Se omitieron ${result.skipped} fila(s) por números repetidos.` : "";
    showToast(`Se guardaron ${result.records} registros y ${result.copies} números de inventario.${skippedNote}`);
    logActivity(`Escaneo guardado: ${result.records} registro(s) y ${result.copies} número(s)${result.skipped ? `, ${result.skipped} duplicado(s) omitidos` : ""}.`, result.skipped ? "warn" : "ok");
  } catch (error) {
    elements.scanError.textContent = error.message;
    elements.scanError.hidden = false;
    logActivity(`No se pudo guardar el escaneo: ${error.message}`, "error");
  } finally {
    elements.scanSaveButton.disabled = false;
  }
}

function compressNumbers(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b);
  if (!sorted.length) return "";
  if (sorted.length === 1) return String(sorted[0]);
  if (sorted.every((number, index) => index === 0 || number === sorted[index - 1] + 1)) {
    return `${sorted[0]}-${sorted[sorted.length - 1]}`;
  }
  return sorted.map(String).join(", ");
}

async function handleSubmit(event) {
  event.preventDefault();
  const data = new FormData(elements.form);
  const inventoryRange = String(data.get("inventoryRange")).trim();
  const title = String(data.get("title")).trim();
  const author = String(data.get("author")).trim();
  let inventoryNumbers = [];
  if (inventoryRange) {
    try {
      inventoryNumbers = parseInventoryRange(inventoryRange);
    } catch (error) {
      elements.formError.textContent = error.message;
      elements.formError.hidden = false;
      return;
    }
  }
  const missing = [];
  if (!inventoryRange) missing.push("número de inventario");
  if (!title) missing.push("título");
  if (!author) missing.push("autor");
  if (missing.length) {
    const confirmed = await confirmAction(
      `Faltan estos datos: ${missing.join(", ")}. El registro se guardará incompleto. ¿Querés continuar?`,
      {
        title: "Datos incompletos",
        eyebrow: "REVISAR",
        confirmLabel: "Guardar igual",
        tone: "danger",
      },
    );
    if (!confirmed) return;
  }
  const updated = {
    title,
    author,
    imprint: String(data.get("imprint")).trim(),
    binding: String(data.get("binding")).trim(),
    inventory_numbers: inventoryNumbers,
    inventory_source: compressNumbers(inventoryNumbers),
  };
  elements.saveButton.disabled = true;
  try {
    const path = editingId ? `/api/books/${encodeURIComponent(editingId)}` : "/api/books";
    const method = editingId ? "PUT" : "POST";
    await apiRequest(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updated),
    });
    elements.dialog.close();
    const loaded = await refreshBooks();
    if (!loaded) return;
    showToast(editingId ? "Se guardaron los cambios del registro." : "El registro se agregó al catálogo.");
    logActivity(editingId ? `Registro actualizado: «${title}».` : `Registro agregado: «${title}».`, "ok");
  } catch (error) {
    elements.formError.textContent = error.message;
    elements.formError.hidden = false;
    logActivity(`No se pudo guardar el registro «${title}»: ${error.message}`, "error");
  } finally {
    elements.saveButton.disabled = false;
  }
}

async function deleteBook(id) {
  const book = books.find((item) => item.id === id);
  if (!book) return;
  const confirmed = await confirmAction(
    `¿Mover «${book.title}» y sus ${book.inventoryNumbers.length} número(s) de inventario a la papelera?`,
    { title: "Mover a la papelera", eyebrow: "ELIMINAR", confirmLabel: "Mover a papelera", tone: "danger" },
  );
  if (!confirmed) return;
  try {
    await apiRequest(`/api/books/${encodeURIComponent(id)}`, { method: "DELETE" });
    const loaded = await refreshBooks();
    if (!loaded) return;
    await refreshTrashCount();
    showToast("El registro se movió a la papelera.");
    logActivity(`Registro movido a la papelera: «${book.title}».`, "warn");
  } catch (error) {
    showToast(`No se pudo eliminar el registro: ${error.message}`);
    logActivity(`No se pudo enviar a la papelera «${book.title}»: ${error.message}`, "error");
  }
}

async function clearCatalog() {
  if (!books.length) {
    showToast("El catálogo ya está vacío.");
    return;
  }
  const copies = books.reduce((total, book) => total + book.inventoryNumbers.length, 0);
  const first = await confirmAction(
    `¿Mover los ${books.length} registros y sus ${copies} números de inventario a la papelera?`,
    { title: "Vaciar catálogo", eyebrow: "VACIAR", confirmLabel: "Continuar", tone: "danger" },
  );
  if (!first) return;
  const second = await confirmAction(
    "Confirmá otra vez: se moverá todo el catálogo a la papelera. Podés restaurarlo desde ahí.",
    { title: "¿Seguro?", eyebrow: "CONFIRMAR", confirmLabel: "Sí, vaciar", tone: "danger" },
  );
  if (!second) return;
  try {
    const result = await apiRequest("/api/books", { method: "DELETE" });
    elements.search.value = "";
    const loaded = await refreshBooks();
    if (!loaded) return;
    await refreshTrashCount();
    showToast(`Se movieron ${result.deleted_records} registros a la papelera.`);
    logActivity(`Catálogo vaciado: ${result.deleted_records} registro(s) a la papelera.`, "warn");
  } catch (error) {
    showToast(`No se pudo vaciar el catálogo: ${error.message}`);
    logActivity(`No se pudo vaciar el catálogo: ${error.message}`, "error");
  }
}

function updateTrashCount() {
  if (!elements.trashCount) return;
  elements.trashCount.textContent = trash.length.toLocaleString("es");
  elements.trashCount.hidden = trash.length === 0;
}

async function loadTrash() {
  const records = await apiRequest("/api/trash");
  trash = records.map(fromApiRecord);
  updateTrashCount();
  return trash;
}

async function refreshTrashCount() {
  try {
    return await loadTrash();
  } catch (error) {
    trash = [];
    updateTrashCount();
    return trash;
  }
}

function renderTrash() {
  const hasItems = trash.length > 0;
  elements.trashEmpty.hidden = hasItems;
  elements.trashList.hidden = !hasItems;
  elements.emptyTrashButton.disabled = !hasItems;
  updateTrashCount();
  elements.trashList.innerHTML = trash.map((record) => {
    const ids = inventoryText(record.inventoryNumbers);
    let when = "";
    if (record.deletedAt) {
      const parsed = new Date(`${record.deletedAt}Z`);
      when = Number.isNaN(parsed.getTime()) ? record.deletedAt : parsed.toLocaleString("es-AR");
    }
    return `<li class="trash-item" data-id="${escapeHTML(record.id)}">
      <div class="trash-info">
        <span class="trash-title" title="${escapeHTML(record.title)}">${escapeHTML(record.title)}</span>
        <span class="trash-meta">${escapeHTML(record.author)} · ${escapeHTML(ids)}</span>
        <span class="trash-when">Eliminado el ${escapeHTML(when)}</span>
      </div>
      <div class="trash-actions">
        <button class="button button-export" type="button" data-action="restore">Restaurar</button>
        <button class="button button-danger" type="button" data-action="purge">Borrar</button>
      </div>
    </li>`;
  }).join("");
}

async function openTrash() {
  elements.trashDialog.showModal();
  elements.trashList.hidden = false;
  elements.trashEmpty.hidden = true;
  elements.trashList.innerHTML = '<li class="trash-loading">Cargando papelera…</li>';
  try {
    await loadTrash();
    renderTrash();
  } catch (error) {
    elements.trashList.innerHTML = "";
    elements.trashList.hidden = true;
    elements.trashEmpty.hidden = true;
    showToast(`No se pudo abrir la papelera: ${error.message}`);
  }
}

async function restoreRecord(id) {
  try {
    await apiRequest(`/api/trash/${encodeURIComponent(id)}/restore`, { method: "POST" });
    await refreshTrashCount();
    renderTrash();
    const loaded = await refreshBooks();
    if (!loaded) return;
    showToast("El registro se restauró al catálogo.");
    logActivity("Registro restaurado desde la papelera.", "ok");
  } catch (error) {
    showToast(`No se pudo restaurar: ${error.message}`);
    logActivity(`No se pudo restaurar desde la papelera: ${error.message}`, "error");
  }
}

async function purgeRecord(id) {
  const record = trash.find((item) => item.id === id);
  if (!record) return;
  const confirmed = await confirmAction(
    `¿Borrar definitivamente «${record.title}»? No se puede deshacer.`,
    { title: "Borrar definitivamente", eyebrow: "PAPELERA", confirmLabel: "Borrar", tone: "danger" },
  );
  if (!confirmed) return;
  try {
    await apiRequest(`/api/trash/${encodeURIComponent(id)}`, { method: "DELETE" });
    await refreshTrashCount();
    renderTrash();
    showToast("El registro se borró definitivamente.");
    logActivity(`Registro borrado definitivamente: «${record.title}».`, "error");
  } catch (error) {
    showToast(`No se pudo borrar: ${error.message}`);
    logActivity(`No se pudo borrar «${record.title}»: ${error.message}`, "error");
  }
}

async function emptyTrash() {
  if (!trash.length) return;
  const confirmed = await confirmAction(
    `¿Vaciar la papelera? Se borrarán ${trash.length} registro(s) definitivamente.`,
    { title: "Vaciar papelera", eyebrow: "PAPELERA", confirmLabel: "Borrar todo", tone: "danger" },
  );
  if (!confirmed) return;
  try {
    const result = await apiRequest("/api/trash", { method: "DELETE" });
    await refreshTrashCount();
    renderTrash();
    showToast(`Se borraron ${result.deleted_records} registros de la papelera.`);
    logActivity(`Papelera vaciada: ${result.deleted_records} registro(s) eliminados.`, "error");
  } catch (error) {
    showToast(`No se pudo vaciar la papelera: ${error.message}`);
    logActivity(`No se pudo vaciar la papelera: ${error.message}`, "error");
  }
}

async function handleCsvFile(file) {
  try {
    const choice = await openChoiceDialog({
      title: "Importar catálogo",
      eyebrow: "IMPORTAR CSV",
      message: books.length
        ? `El catálogo actual tiene ${books.length} registro(s). ¿Querés reemplazarlo por el CSV o agregar solo los registros nuevos e ignorar los números de inventario que ya existen?`
        : "Elegí cómo cargar el CSV: reemplazar el catálogo actual o agregar solo los registros nuevos e ignorar los números que ya existan.",
      options: [
        { value: null, label: "Cancelar", className: "button-cancel" },
        { value: "merge", label: "Ignorar duplicados", className: "button-export" },
        { value: "replace", label: "Reemplazar todo", className: "button-danger" },
      ],
    });
    if (!choice) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("confirm_replace", String(choice === "replace"));
    const result = await apiRequest(`/api/import?mode=${choice}`, { method: "POST", body: formData });
    elements.search.value = "";
    const loaded = await refreshBooks();
    if (!loaded) return;
    const ignored = result.skipped ? ` Se ignoraron ${result.skipped} registro(s) duplicado(s).` : "";
    const action = choice === "replace" ? "Reemplazados" : "Agregados";
    showToast(`${action} ${result.records} registro(s) y ${result.copies} ejemplar(es) numerado(s).${ignored}`);
    logActivity(`CSV ${choice === "replace" ? "reemplazado" : "combinado"}: ${result.records} registro(s)${result.skipped ? `, ${result.skipped} duplicado(s) ignorados` : ""}.`, result.skipped ? "warn" : "ok");
  } catch (error) {
    console.error("No se pudo importar el CSV a MySQL.", error);
    showToast(`No se pudo importar el CSV: ${error.message}`);
    logActivity(`No se pudo importar el CSV: ${error.message}`, "error");
  } finally {
    elements.csvInput.value = "";
  }
}

const OCR_ACCEPTED = /\.(png|jpe?g|tiff?|bmp|webp|gif|heic|heif|avif)$/i;
const OCR_MAX_BYTES = 20 * 1024 * 1024;
let ocrStatusCache;
let ocrBusy = false;

function setOcrBusy(busy, message) {
  const hint = document.querySelector("#ocr-loader-hint");
  if (hint) {
    hint.textContent = message || "Leé una o varias fichas escaneadas y se completan los campos.";
  }
  document
    .querySelectorAll('label.button[for="ocr-input"], label.button[for="ocr-dialog-input"]')
    .forEach((element) => element.classList.toggle("busy", busy));
  if (elements.ocrStatus) {
    elements.ocrStatus.hidden = !busy;
    if (busy && elements.ocrStatusText) {
      elements.ocrStatusText.textContent = message || "Escaneando la imagen…";
    }
  }
}

function validateImageFile(file) {
  const looksImage = (file.type || "").startsWith("image/") || OCR_ACCEPTED.test(file.name || "");
  if (!looksImage) {
    throw httpError("El archivo no parece una imagen. Formatos admitidos: PNG, JPG, TIFF, BMP, WEBP, GIF o HEIC.", 400);
  }
  if (file.size > OCR_MAX_BYTES) {
    throw httpError("La imagen supera los 20 MB. Reducila o escaneá a menor resolución.", 413);
  }
}

function describeOcrError(error) {
  if (error.status === 0) return error.message;
  if (error.status === 400) return error.message;
  if (error.status === 413) return error.message;
  if (error.status === 503) return `El lector OCR no está disponible: ${error.message}`;
  if (error.status === 422) return `No se pudo leer la imagen. ${error.message}`;
  if (error.status === 404) return "El servidor no tiene el módulo OCR. Recompilá la API con docker compose up --build.";
  return `No se pudo leer la imagen: ${error.message}`;
}

function reportOcrError(error) {
  console.error("No se pudo leer la imagen escaneada.", error);
  const message = describeOcrError(error);
  if (!elements.dialog.open) openDialog();
  elements.formError.hidden = true;
  showOcrAlert(message, "error");
  showToast(message);
  logActivity(`Error de OCR: ${message}`, "error");
}

async function getOcrStatus() {
  if (ocrStatusCache) return ocrStatusCache;
  try {
    ocrStatusCache = await apiRequest("/api/ocr/status");
  } catch (error) {
    ocrStatusCache = null;
  }
  return ocrStatusCache;
}

function combineOcrResults(successes) {
  const records = [];
  const warnings = [];
  const engines = new Set();
  let confidenceTotal = 0;
  successes.forEach((success) => {
    const result = success.result || {};
    if (result.engine) engines.add(result.engine);
    confidenceTotal += Number(result.confidence) || 0;
    (result.warnings || []).forEach((warning) => {
      if (!warnings.includes(warning)) warnings.push(warning);
    });
    (success.records || []).forEach((record) => records.push(record));
  });
  return {
    engine: [...engines].join("+") || "OCR",
    confidence: successes.length ? Math.round(confidenceTotal / successes.length) : 0,
    records,
    warnings,
    image_count: successes.length,
  };
}

async function handleOcrFiles(fileList) {
  const files = Array.from(fileList || []).filter(Boolean);
  if (!files.length || ocrBusy) return;
  ocrBusy = true;

  if (elements.dialog.open) elements.formError.hidden = true;
  hideOcrAlert();
  hideScanAlert();
  resetOcrPreview();
  resetScanPreview();

  const createdUrls = [];
  const successes = [];
  const failures = [];

  try {
    const status = await getOcrStatus();
    if (status && status.available === false) {
      throw httpError(
        `El lector ${status.engine || "OCR"} no está disponible en el servidor. ${status.detail || "Recompilá la API: docker compose up --build."}`,
        503,
      );
    }

    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const progress = `Escaneando imagen ${index + 1} de ${files.length}…`;
      setOcrBusy(true, progress);
      showToast(`${progress} puede tardar unos segundos.`);
      try {
        validateImageFile(file);
        const formData = new FormData();
        formData.append("file", file);
        const result = await apiRequest("/api/ocr/scan", { method: "POST", body: formData });
        const records = Array.isArray(result.records) && result.records.length
          ? result.records
          : [result];
        const imageUrl = URL.createObjectURL(file);
        createdUrls.push(imageUrl);
        successes.push({ result, records, imageUrl });
      } catch (error) {
        failures.push({ file, error });
      }
    }

    if (!successes.length) {
      const first = failures[0];
      const message = failures.length > 1
        ? `${failures.length} imágenes no se pudieron leer. ${first.error.message}`
        : first.error.message;
      throw httpError(message, first.error.status || 422);
    }

    const combined = combineOcrResults(successes);
    const failedNames = failures.map((failure) => failure.file.name).filter(Boolean).join(", ");
    if (failures.length) {
      const detail = failedNames
        ? `${failures.length} imagen(es) no se pudieron leer: ${failedNames}.`
        : `${failures.length} imagen(es) no se pudieron leer.`;
      combined.warnings.push(detail);
    }

    if (!combined.records.length) {
      throw httpError("No se reconocieron registros en las imágenes seleccionadas.", 422);
    }

    const preferred = combined.records.length === 1
      ? successes.find((success) => success.records.length === 1) || successes[0]
      : successes[0];

    createdUrls.forEach((imageUrl) => {
      if (imageUrl !== preferred.imageUrl) URL.revokeObjectURL(imageUrl);
    });

    if (combined.records.length === 1) {
      const single = combined.records[0];
      showOcrSuggestion(
        { ...combined, records: [single] },
        preferred.imageUrl,
      );
    } else {
      openScanRecords(combined, preferred.imageUrl, combined.records);
    }

    logActivity(
      `Escaneo OCR: ${successes.length} imagen(es), ${combined.records.length} registro(s) reconocido(s).`,
      failures.length ? "warn" : "info",
    );
    if (failedNames) {
      showToast(`Se leyeron ${successes.length} imagen(es). No se pudieron leer: ${failedNames}.`);
    }
  } catch (error) {
    createdUrls.forEach((imageUrl) => URL.revokeObjectURL(imageUrl));
    reportOcrError(error);
  } finally {
    ocrBusy = false;
    setOcrBusy(false);
    elements.ocrInput.value = "";
    elements.ocrDialogInput.value = "";
  }
}

function exportCsv() {
  const headers = ["N° Inv.", "Autor", "Título", "Pie de Imprenta", "Encuadernación / Observaciones"];
  const quote = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [
    headers.map(quote).join(","),
    ...getFilteredBooks().map((book) => [
      inventoryText(book.inventoryNumbers).replaceAll("–", "-"),
      book.author,
      book.title,
      book.imprint,
      book.binding,
    ].map(quote).join(",")),
  ];
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `inventario-biblioteca-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  showToast("Se exportó la vista actual en el formato de tu catálogo.");
}

function appendChatMessage(role, text) {
  const wrapper = document.createElement("div");
  wrapper.className = `chat-message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  const label = document.createElement("span");
  label.className = "chat-role";
  label.textContent = role === "user" ? "Vos" : role === "error" ? "Error" : "ChatBot";
  const content = document.createElement("div");
  content.className = "chat-content";
  if (role === "assistant") {
    content.innerHTML = renderMarkdown(text);
  } else {
    content.textContent = text;
  }
  bubble.append(label, content);
  wrapper.append(bubble);
  elements.chatLog.append(wrapper);
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  return wrapper;
}

function resetChat() {
  chatHistory = [];
  chatBusy = false;
  elements.chatLog.innerHTML = "";
  appendChatMessage("assistant", CHAT_WELCOME);
}

async function loadChatStatus() {
  try {
    const status = await apiRequest("/api/chat/status");
    if (status.model) elements.chatModelPill.textContent = status.model;
    const files = status.context_files
      ? ` · ${status.context_files} archivo(s) de normas internas`
      : "";
    elements.chatStatus.classList.toggle("error", status.available === false);
    elements.chatStatus.textContent = status.available
      ? `Modelo: ${status.model}${files}`
      : status.detail || "El asistente no está disponible.";
  } catch (error) {
    elements.chatStatus.classList.add("error");
    elements.chatStatus.textContent = "No se pudo consultar el estado del asistente.";
  }
}

async function sendChatQuestion(question) {
  const text = String(question ?? "").trim();
  if (!text || chatBusy) return;
  chatBusy = true;
  elements.chatSendButton.disabled = true;
  elements.chatStatus.classList.remove("error");
  elements.chatStatus.textContent = "Consultando el inventario…";
  appendChatMessage("user", text);
  const pending = appendChatMessage("assistant", "Pensando…");
  pending.querySelector(".chat-bubble").classList.add("chat-typing");
  try {
    const result = await apiRequest("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: chatHistory.slice(-8) }),
    });
    pending.remove();
    appendChatMessage("assistant", result.reply);
    chatHistory.push({ role: "user", content: text });
    chatHistory.push({ role: "assistant", content: result.reply });
    chatHistory = chatHistory.slice(-8);
    elements.chatStatus.classList.remove("error");
    elements.chatStatus.textContent = `Modelo: ${result.model}`;
  } catch (error) {
    pending.remove();
    appendChatMessage("error", error.message);
    elements.chatStatus.classList.add("error");
    elements.chatStatus.textContent = error.status === 0
      ? "Sin conexión con el servidor."
      : "El asistente no pudo responder.";
  } finally {
    chatBusy = false;
    elements.chatSendButton.disabled = false;
    elements.chatInput.focus();
  }
}

function setView(view) {
  const target = view === "chatbot" ? "chatbot" : "inventory";
  if (elements.inventoryView) elements.inventoryView.hidden = target !== "inventory";
  if (elements.chatPanel) elements.chatPanel.hidden = target !== "chatbot";
  document.querySelectorAll(".nav-link[data-view]").forEach((link) => {
    const active = link.dataset.view === target;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (elements.breadcrumbCurrent) {
    elements.breadcrumbCurrent.textContent = target === "chatbot" ? "ChatBot" : "Inventario";
  }
  if (target === "chatbot") {
    if (!chatStarted) {
      chatStarted = true;
      resetChat();
      loadChatStatus();
    }
    elements.chatInput.focus();
  } else {
    document.querySelectorAll(".row-menu").forEach((menu) => menu.remove());
  }
}

document.querySelector("#add-book-button").addEventListener("click", () => openDialog());
document.querySelector("#empty-add-button").addEventListener("click", () => openDialog());
document.querySelector("#cancel-button").addEventListener("click", () => elements.dialog.close());
document.querySelector(".close-dialog").addEventListener("click", () => elements.dialog.close());
elements.form.addEventListener("submit", handleSubmit);
elements.search.addEventListener("input", renderBooks);
document.querySelector("#export-button").addEventListener("click", exportCsv);
document.querySelector("#clear-all-button")?.addEventListener("click", clearCatalog);
document.querySelector("#trash-button")?.addEventListener("click", openTrash);
document.querySelector(".close-trash")?.addEventListener("click", () => elements.trashDialog.close());
document.querySelector("#close-trash-button")?.addEventListener("click", () => elements.trashDialog.close());
elements.emptyTrashButton?.addEventListener("click", emptyTrash);
elements.trashList?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.closest(".trash-item")?.dataset.id;
  if (!id) return;
  if (button.dataset.action === "restore") restoreRecord(id);
  if (button.dataset.action === "purge") purgeRecord(id);
});
document.querySelector("#import-button").addEventListener("click", () => elements.csvInput.click());
elements.themeToggle.addEventListener("click", toggleTheme);
elements.dialog.addEventListener("close", resetOcrPreview);
document.querySelector(".close-scan")?.addEventListener("click", () => elements.scanDialog.close());
document.querySelector("#scan-cancel-button")?.addEventListener("click", () => elements.scanDialog.close());
elements.scanSaveButton?.addEventListener("click", saveScanRecords);
elements.scanDialog?.addEventListener("close", resetScanPreview);
elements.scanList?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  button.closest(".scan-row")?.remove();
  if (!elements.scanList.querySelector(".scan-row")) {
    elements.scanError.textContent = "No quedan filas para guardar. Cerrá el panel para continuar.";
    elements.scanError.hidden = false;
  }
});
elements.csvInput.addEventListener("change", () => {
  const [file] = elements.csvInput.files;
  if (file) handleCsvFile(file);
});
elements.ocrInput.addEventListener("change", () => {
  const files = [...elements.ocrInput.files];
  if (files.length) handleOcrFiles(files);
});
elements.ocrDialogInput.addEventListener("change", () => {
  const files = [...elements.ocrDialogInput.files];
  if (files.length) handleOcrFiles(files);
  elements.ocrDialogInput.value = "";
});

elements.list.addEventListener("click", (event) => {
  const toggle = event.target.closest(".row-menu-toggle");
  if (!toggle) return;
  const book = books.find((item) => item.id === toggle.dataset.id);
  if (!book) return;
  const existing = toggle.parentElement.querySelector(".row-menu");
  if (existing) {
    existing.remove();
    toggle.setAttribute("aria-expanded", "false");
    return;
  }
  document.querySelectorAll(".row-menu").forEach((menu) => menu.remove());
  const menu = document.createElement("div");
  menu.className = "row-menu";
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.textContent = "Editar registro";
  editButton.addEventListener("click", () => openDialog(book));
  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "delete-action";
  deleteButton.textContent = "Eliminar";
  deleteButton.addEventListener("click", () => deleteBook(book.id));
  menu.append(editButton, deleteButton);
  toggle.parentElement.append(menu);
  const wrap = toggle.closest(".table-wrap");
  if (wrap) {
    const rect = toggle.getBoundingClientRect();
    if (rect.bottom > wrap.getBoundingClientRect().bottom - 130) {
      menu.classList.add("row-menu-up");
    }
  }
  toggle.setAttribute("aria-expanded", "true");
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".row-actions")) {
    document.querySelectorAll(".row-menu").forEach((menu) => menu.remove());
    document.querySelectorAll(".row-menu-toggle[aria-expanded='true']")
      .forEach((button) => button.setAttribute("aria-expanded", "false"));
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    setView("inventory");
    elements.search.focus();
  }
});

document.querySelectorAll(".nav-link[data-view]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    setView(link.dataset.view);
  });
});

elements.chatForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = elements.chatInput.value;
  elements.chatInput.value = "";
  sendChatQuestion(text);
});
elements.chatClearButton?.addEventListener("click", () => resetChat());
elements.chatSuggestions?.addEventListener("click", (event) => {
  const chip = event.target.closest(".chat-chip");
  if (!chip) return;
  sendChatQuestion(chip.dataset.question);
});

document.querySelector("#today").textContent = new Intl.DateTimeFormat("es-AR", {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
}).format(new Date());

loadActivity();
renderActivity();
elements.activityClear?.addEventListener("click", clearActivity);
elements.activityButton?.addEventListener("click", () => {
  renderActivity();
  elements.activityDialog?.showModal();
});
document.querySelector(".close-activity")?.addEventListener("click", () => elements.activityDialog?.close());
document.querySelector("#close-activity-button")?.addEventListener("click", () => elements.activityDialog?.close());

loadTheme();
renderBooks();
refreshBooks();
refreshTrashCount();
