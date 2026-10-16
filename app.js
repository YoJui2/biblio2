"use strict";

const INVENTORY_PREFIX = "INV-";
const INVENTORY_WIDTH = 6;

const elements = {
  list: document.querySelector("#book-list"),
  emptyState: document.querySelector("#empty-state"),
  emptyTitle: document.querySelector("#empty-title"),
  emptyDescription: document.querySelector("#empty-description"),
  search: document.querySelector("#search-input"),
  categoryFilter: document.querySelector("#category-filter"),
  dialog: document.querySelector("#book-dialog"),
  form: document.querySelector("#book-form"),
  dialogTitle: document.querySelector("#dialog-title"),
  saveButton: document.querySelector("#save-button"),
  formError: document.querySelector("#form-error"),
  toast: document.querySelector("#toast"),
  csvInput: document.querySelector("#csv-input"),
  themeToggle: document.querySelector("#theme-toggle"),
};

let books = [];
let editingId = null;
let toastTimeout;

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
    section: record.section,
    title: record.title,
    author: record.author,
    imprint: record.imprint,
    binding: record.binding,
    inventorySource: record.inventory_source,
    inventoryNumbers: record.inventory_numbers,
  };
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join(" ")
      : detail || `La API respondió con el estado ${response.status}.`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
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

function formatInventoryNumber(number) {
  return `${INVENTORY_PREFIX}${String(number).padStart(INVENTORY_WIDTH, "0")}`;
}

function parseInventoryRange(value) {
  const match = String(value).trim().match(/^(?:INV\s*[-:]?\s*)?(\d+)(?:\s*[-–—]\s*(?:INV\s*[-:]?\s*)?(\d+))?$/i);
  if (!match) throw new Error("Usá un número o rango, por ejemplo 62 o 62-71.");
  const start = Number(match[1]);
  const end = match[2] ? Number(match[2]) : start;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) {
    throw new Error("El rango debe ser ascendente y usar números mayores que cero.");
  }
  if (end - start >= 10000) throw new Error("El rango no puede superar los 10.000 ejemplares.");
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
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
  const section = elements.categoryFilter.value;
  return books.filter((book) => {
    const searchable = [
      book.title, book.author, book.section, book.imprint, book.binding,
      ...book.inventoryNumbers.map(formatInventoryNumber),
      book.inventorySource,
    ].join(" ").toLocaleLowerCase("es");
    return (!query || searchable.includes(query)) && (!section || book.section === section);
  });
}

function renderCategories() {
  const current = elements.categoryFilter.value;
  const categories = [...new Set(books.map((book) => book.section).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "es"));
  elements.categoryFilter.innerHTML = '<option value="">Todas las secciones</option>'
    + categories.map((section) => `<option value="${escapeHTML(section)}">${escapeHTML(section)}</option>`).join("");
  if (categories.includes(current)) elements.categoryFilter.value = current;
  document.querySelector("#category-options").innerHTML = categories
    .map((section) => `<option value="${escapeHTML(section)}"></option>`).join("");
}

function renderStats() {
  const allNumbers = books.flatMap((book) => book.inventoryNumbers);
  const sections = new Set(books.map((book) => book.section).filter(Boolean));
  document.querySelector("#stat-titles").textContent = books.length.toLocaleString("es");
  document.querySelector("#stat-copies").textContent = allNumbers.length.toLocaleString("es");
  document.querySelector("#stat-sections").textContent = sections.size.toLocaleString("es");
  document.querySelector("#stat-range").textContent = allNumbers.length
    ? `${formatInventoryNumber(Math.min(...allNumbers))} – ${formatInventoryNumber(Math.max(...allNumbers))}`
    : "—";
  document.querySelector("#nav-count").textContent = books.length.toLocaleString("es");
}

function renderBooks() {
  renderCategories();
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
      ? "Probá con otra búsqueda o cambiá el filtro de sección."
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
      <td><span class="category-tag" title="${escapeHTML(book.section)}">${escapeHTML(book.section)}</span></td>
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

function openDialog(book = null) {
  editingId = book ? book.id : null;
  elements.form.reset();
  elements.formError.hidden = true;
  elements.dialogTitle.textContent = book ? "Editar registro" : "Agregar registro";
  elements.saveButton.textContent = book ? "Guardar cambios" : "Agregar registro";
  if (book) {
    elements.form.elements.inventoryRange.value = compressNumbers(book.inventoryNumbers);
    for (const name of ["section", "title", "author", "imprint", "binding"]) {
      elements.form.elements[name].value = book[name] || "";
    }
  }
  elements.dialog.showModal();
  elements.form.elements.inventoryRange.focus();
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
  const section = String(data.get("section")).trim();
  const title = String(data.get("title")).trim();
  const author = String(data.get("author")).trim();
  let inventoryNumbers;
  try {
    inventoryNumbers = parseInventoryRange(inventoryRange);
  } catch (error) {
    elements.formError.textContent = error.message;
    elements.formError.hidden = false;
    return;
  }
  if (!section || !title || !author) {
    elements.formError.textContent = "Completá el número de inventario, la sección, el título y el autor.";
    elements.formError.hidden = false;
    return;
  }
  const updated = {
    section,
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
  } catch (error) {
    elements.formError.textContent = error.message;
    elements.formError.hidden = false;
  } finally {
    elements.saveButton.disabled = false;
  }
}

async function deleteBook(id) {
  const book = books.find((item) => item.id === id);
  if (!book) return;
  if (!window.confirm(`¿Querés eliminar "${book.title}" y sus ${book.inventoryNumbers.length} números de inventario? Esta acción no se puede deshacer.`)) return;
  try {
    await apiRequest(`/api/books/${encodeURIComponent(id)}`, { method: "DELETE" });
    const loaded = await refreshBooks();
    if (!loaded) return;
    showToast("El registro y sus números se eliminaron del inventario.");
  } catch (error) {
    showToast(`No se pudo eliminar el registro: ${error.message}`);
  }
}

async function handleCsvFile(file) {
  let confirmed = false;
  try {
    if (books.length) {
      if (!window.confirm(`La importación reemplazará los ${books.length} registros actuales. ¿Continuar?`)) return;
      confirmed = true;
    }
    const upload = async (confirmReplace) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("confirm_replace", String(confirmReplace));
      return apiRequest("/api/import?mode=replace", {
        method: "POST",
        body: formData,
      });
    };
    let result;
    try {
      result = await upload(confirmed);
    } catch (error) {
      if (error.status !== 409 || !error.message.startsWith("MySQL ya contiene ") || confirmed
        || !window.confirm(`${error.message}\n\n¿Reemplazar el catálogo de MySQL?`)) throw error;
      result = await upload(true);
    }
    elements.search.value = "";
    elements.categoryFilter.value = "";
    const loaded = await refreshBooks();
    if (!loaded) return;
    showToast(`Importados ${result.records} registros y ${result.copies} ejemplares numerados.`);
  } catch (error) {
    console.error("No se pudo importar el CSV a MySQL.", error);
    showToast(`No se pudo importar el CSV: ${error.message}`);
  } finally {
    elements.csvInput.value = "";
  }
}

function exportCsv() {
  const headers = ["Sección", "N° Inv.", "Autor", "Título", "Pie de Imprenta", "Encuadernación / Observaciones"];
  const quote = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [
    headers.map(quote).join(","),
    ...getFilteredBooks().map((book) => [
      book.section,
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

document.querySelector("#add-book-button").addEventListener("click", () => openDialog());
document.querySelector("#empty-add-button").addEventListener("click", () => openDialog());
document.querySelector("#cancel-button").addEventListener("click", () => elements.dialog.close());
document.querySelector(".close-dialog").addEventListener("click", () => elements.dialog.close());
elements.form.addEventListener("submit", handleSubmit);
elements.search.addEventListener("input", renderBooks);
elements.categoryFilter.addEventListener("change", renderBooks);
document.querySelector("#export-button").addEventListener("click", exportCsv);
document.querySelector("#import-button").addEventListener("click", () => elements.csvInput.click());
elements.themeToggle.addEventListener("click", toggleTheme);
elements.csvInput.addEventListener("change", () => {
  const [file] = elements.csvInput.files;
  if (file) handleCsvFile(file);
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
    elements.search.focus();
  }
});

document.querySelector("#today").textContent = new Intl.DateTimeFormat("es-AR", {
  weekday: "long", day: "numeric", month: "long", year: "numeric",
}).format(new Date());

loadTheme();
renderBooks();
refreshBooks();
