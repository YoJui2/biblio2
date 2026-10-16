"use strict";

const STORAGE_KEY = "biblioteca-inventario-v2";
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

let books = loadBooks();
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

function loadBooks() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === null) return [];
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed) || parsed.some((book) => (
      !book || typeof book.id !== "string" || typeof book.title !== "string"
      || !Array.isArray(book.inventoryNumbers)
      || book.inventoryNumbers.some((number) => !Number.isSafeInteger(number) || number < 1)
    ))) {
      throw new Error("El formato del inventario guardado no es válido.");
    }
    return parsed;
  } catch (error) {
    console.error("No se pudo leer el inventario guardado.", error);
    showToast("No se pudo cargar el inventario guardado.");
    return [];
  }
}

function saveBooks(nextBooks) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(nextBooks));
    books = nextBooks;
    return true;
  } catch (error) {
    console.error("No se pudo guardar el inventario.", error);
    showToast("No se pudieron guardar los cambios. Revisá el espacio disponible.");
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

function handleSubmit(event) {
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
  const idsInUse = new Set(books.filter((book) => book.id !== editingId).flatMap((book) => book.inventoryNumbers));
  const duplicate = inventoryNumbers.find((number) => idsInUse.has(number));
  if (duplicate !== undefined) {
    elements.formError.textContent = `${formatInventoryNumber(duplicate)} ya está asignado a otro registro.`;
    elements.formError.hidden = false;
    return;
  }
  const updated = {
    id: editingId || makeId(),
    section,
    title,
    author,
    imprint: String(data.get("imprint")).trim(),
    binding: String(data.get("binding")).trim(),
    inventoryNumbers,
    inventorySource: compressNumbers(inventoryNumbers),
  };
  const nextBooks = editingId
    ? books.map((book) => book.id === editingId ? updated : book)
    : [updated, ...books];
  if (!saveBooks(nextBooks)) return;
  elements.dialog.close();
  renderBooks();
  showToast(editingId ? "Se guardaron los cambios del registro." : "El registro se agregó al catálogo.");
}

function makeId() {
  return globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function deleteBook(id) {
  const book = books.find((item) => item.id === id);
  if (!book) return;
  if (!window.confirm(`¿Querés eliminar "${book.title}" y sus ${book.inventoryNumbers.length} números de inventario? Esta acción no se puede deshacer.`)) return;
  if (!saveBooks(books.filter((item) => item.id !== id))) return;
  renderBooks();
  showToast("El registro y sus números se eliminaron del inventario.");
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  const source = text.replace(/^\uFEFF/, "");
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
    } else if (character === '"' && field.length === 0) {
      quoted = true;
    } else if (character === ",") {
      row.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      if (character === "\r" && source[index + 1] === "\n") index += 1;
      row.push(field);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  if (quoted) throw new Error("El CSV termina con un campo entre comillas sin cerrar.");
  if (field.length || row.length) {
    row.push(field);
    if (row.some((value) => value.trim())) rows.push(row);
  }
  return rows;
}

function normalizeHeader(header) {
  return header.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es").replace(/[^a-z0-9]/g, "");
}

function importCsvText(text) {
  const rows = parseCsv(text);
  const headerIndex = rows.findIndex((row) => {
    const headers = row.map(normalizeHeader);
    return headers.includes("seccion") && headers.includes("autor")
      && headers.includes("titulo") && headers.includes("piedeimprenta")
      && headers.some((header) => header.includes("inv"));
  });
  if (headerIndex < 0) throw new Error("No encontré los encabezados Sección, N° Inv., Autor, Título y Pie de Imprenta.");
  const headers = rows[headerIndex].map(normalizeHeader);
  const column = (predicate) => headers.findIndex(predicate);
  const indexes = {
    section: column((header) => header === "seccion"),
    inventory: column((header) => header.includes("inv")),
    author: column((header) => header === "autor"),
    title: column((header) => header === "titulo"),
    imprint: column((header) => header.includes("imprenta")),
    binding: column((header) => header.includes("encuadernacion") || header.includes("observaciones")),
  };
  const imported = [];
  const seenNumbers = new Map();
  const errors = [];
  for (let index = headerIndex + 1; index < rows.length; index += 1) {
    const row = rows[index];
    if (!row.some((value) => value.trim())) continue;
    const record = Object.fromEntries(Object.entries(indexes).map(([key, position]) => [key, (row[position] || "").trim()]));
    if (!record.inventory && !record.title && !record.author) continue;
    if (!record.section || !record.inventory || !record.title || !record.author) {
      errors.push(`fila ${index + 1}: falta sección, número de inventario, título o autor`);
      continue;
    }
    let inventoryNumbers;
    try {
      inventoryNumbers = parseInventoryRange(record.inventory);
    } catch (error) {
      errors.push(`fila ${index + 1} (${record.inventory}): ${error.message}`);
      continue;
    }
    const duplicate = inventoryNumbers.find((number) => seenNumbers.has(number));
    if (duplicate !== undefined) {
      errors.push(`fila ${index + 1}: ${formatInventoryNumber(duplicate)} se repite (también en fila ${seenNumbers.get(duplicate)})`);
      continue;
    }
    inventoryNumbers.forEach((number) => seenNumbers.set(number, index + 1));
    imported.push({
      id: makeId(),
      section: record.section,
      inventorySource: record.inventory,
      inventoryNumbers,
      author: record.author,
      title: record.title,
      imprint: record.imprint,
      binding: record.binding,
    });
  }
  if (errors.length) throw new Error(`No se importó el archivo: ${errors.slice(0, 5).join("; ")}${errors.length > 5 ? `; y ${errors.length - 5} errores más` : ""}.`);
  if (!imported.length) throw new Error("El archivo no contiene registros para importar.");
  return imported;
}

async function handleCsvFile(file) {
  try {
    const text = await file.text();
    const imported = importCsvText(text);
    if (books.length && !window.confirm(`El archivo contiene ${imported.length} registros y reemplazará los ${books.length} registros actuales. ¿Continuar?`)) return;
    if (!saveBooks(imported)) return;
    elements.search.value = "";
    elements.categoryFilter.value = "";
    renderBooks();
    const totalCopies = imported.reduce((total, book) => total + book.inventoryNumbers.length, 0);
    showToast(`Importados ${imported.length} registros y ${totalCopies} ejemplares numerados.`);
  } catch (error) {
    console.error("No se pudo importar el CSV.", error);
    showToast(error.message || "No se pudo leer el archivo CSV.");
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
