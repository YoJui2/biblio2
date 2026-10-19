# Inventario de biblioteca

Panel administrativo para explorar y mantener el catálogo bibliográfico. La interfaz se sirve desde FastAPI; el catálogo y sus ejemplares se guardan en MySQL. El tema elegido continúa guardándose en el navegador. Esta primera versión está pensada para una sola persona en la PC local y no incluye autenticación.

## Levantar el sistema en Windows

Requisitos: Docker Desktop con Docker Compose habilitado. Desde la raíz del proyecto, en PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up --build
```

Reemplazá en `.env` las contraseñas de ejemplo por valores locales propios antes de iniciar los contenedores. Para simplificar la URL que usa el contenedor de la API, usá contraseñas alfanuméricas en esta configuración inicial. No compartas ni subas `.env`: está excluido por `.gitignore` y `.dockerignore`.

Después abrí:

- Panel: <http://localhost:8000/>
- Documentación interactiva de FastAPI: <http://localhost:8000/docs>
- Estado de API y MySQL: <http://localhost:8000/api/health>

El contenedor de la API aplica las migraciones de Alembic antes de iniciar FastAPI. La base persiste en el volumen Docker `mysql_data` cuando se detienen los contenedores. **No ejecutes `docker compose down -v` salvo que quieras borrar la base local completa.**

La interfaz debe abrirse desde `http://localhost:8000/`; abrir `index.html` directamente como archivo ya no proporciona la conexión a la API.
MySQL solo es accesible desde la red interna de Docker; no se publica un puerto en Windows.

| Método | Ruta | Uso |
| --- | --- | --- |
| `GET` | `/api/health` | Comprueba la conexión a MySQL. |
| `GET` | `/api/books` | Lista los registros y sus números de inventario individuales. |
| `POST` | `/api/books` | Crea un registro y sus ejemplares. |
| `POST` | `/api/books/bulk` | Crea varios registros a la vez de forma atómica (un escaneo con varias fichas). Con `?on_conflict=skip` omite los registros cuyos números ya existen y devuelve `skipped` en lugar de rechazar todo el lote. |
| `PUT` | `/api/books/{id}` | Actualiza un registro y su rango de ejemplares. |
| `DELETE` | `/api/books/{id}` | Mueve el registro y sus ejemplares a la papelera. |
| `DELETE` | `/api/books` | Mueve **todos** los registros a la papelera. |
| `GET` | `/api/trash` | Lista los registros de la papelera. |
| `POST` | `/api/trash/{id}/restore` | Restaura un registro al catálogo. |
| `DELETE` | `/api/trash/{id}` | Borra definitivamente un registro de la papelera. |
| `DELETE` | `/api/trash` | Vacía la papelera (borrado definitivo). |
| `POST` | `/api/import?mode=replace` | Valida y **reemplaza** el catálogo desde un CSV; requiere confirmación (`confirm_replace`) si la base ya tiene registros. |
| `POST` | `/api/import?mode=merge` | Valida y **agrega** desde un CSV solo los registros nuevos, ignorando los números de inventario que ya existen. |
| `GET` | `/api/ocr/status` | Informa el motor OCR activo (DeepSeek o TrOCR), si está disponible y qué formatos admite. |
| `POST` | `/api/ocr/scan` | Lee una imagen escaneada y devuelve una o más propuestas de registro; no persiste nada. |
| `GET` | `/api/chat/status` | Informa el modelo de chat activo y si hay una API key configurada. |
| `POST` | `/api/chat` | Responde una consulta sobre el inventario con DeepSeek usando solo los datos del catálogo. |

La interfaz abre en tema oscuro. El botón de sol/luna permite cambiar al tema claro; la selección se guarda aparte de los datos del catálogo.

## Lectura de imágenes escaneadas (OCR)

Para digitalizar fichas escaneadas sin transcribirlas a mano, el panel usa por defecto un **modelo de visión de DeepSeek** (`deepseek-flash`) a través de su API compatible con OpenAI. El modelo transcribe la imagen completa y el resultado se interpreta como propuestas de registro. Como respaldo local se mantiene [TrOCR](https://huggingface.co/microsoft/trocr-base-handwritten) (`microsoft/trocr-base-handwritten`), que lee **escritura manuscrita e impresa** segmentando la imagen en renglones.

1. Elegí **Leer imagen (OCR)** en la barra del panel, o **Cargar imagen** dentro del formulario de registro, y seleccioná una foto o escaneo (PNG, JPG, TIFF, BMP, WEBP, GIF, y HEIC/AVIF para fotos de celular; hasta 20 MB).
2. La API reencoda la imagen a JPEG y la envía al modelo de visión de DeepSeek con una consigna que devuelve **una línea por libro**, con los campos `Nº de inventario | Autor | Título | Encuadernación | Pie de imprenta`.
3. El texto reconocido se divide en una propuesta por cada fila.
4. Según cuántas fichas haya:
   - **Una sola ficha:** se abre el formulario **Agregar registro** con los campos reconocidos y la imagen de referencia.
    - **Varias fichas (multiregistro):** se abre el panel **Registros detectados**, con la imagen y una fila editable por cada libro; corregís lo que haga falta y guardás todo junto con `POST /api/books/bulk`.
5. Revisá y corregí los campos, y guardá. **El OCR nunca escribe en la base por su cuenta**: solo propone valores.

Si una ficha tiene varios números de inventario (un rango como `62-72` o una lista como `62, 65, 78`), el formulario los carga **todos**; el campo acepta números sueltos separados por comas y rangos. Un mismo registro bibliográfico puede tener muchos ejemplares.

La respuesta de `/api/ocr/scan` incluye `records` (lista de propuestas), `engine` (`deepseek` o `trocr`), `confidence`, `raw_text` y `warnings` (por ejemplo, si no reconoció números de inventario, si no encontró título/autor o si la confianza es baja).

Mientras el servidor procesa una imagen, el panel muestra un **indicador de progreso** junto a la barra de acciones (con su leyenda «Escaneando la imagen…») y un aviso al iniciar la lectura, para que quede claro que el OCR puede tardar unos segundos.

Configuración en `.env`:

| Variable | Uso |
| --- | --- |
| `OCR_ENGINE` | Motor activo: `deepseek` o `trocr`. Si se deja vacío, usa DeepSeek cuando hay API key y TrOCR como respaldo. |
| `DEEPSEEK_API_KEY` | Clave de la API de DeepSeek. **Pegá tu clave acá.** |
| `DEEPSEEK_BASE_URL` | URL base de la API; por defecto `https://api.deepseek.com`. |
| `DEEPSEEK_MODEL` | Modelo de visión del OCR; por defecto `deepseek-flash`. |
| `DEEPSEEK_CHAT_MODEL` | Modelo del ChatBot si `CHATBOT_*` está en blanco; por defecto `deepseek-chat`. |
| `CHATBOT_API_KEY` | Clave del proveedor del ChatBot. Para Gemini, una clave `AIza…`. |
| `CHATBOT_BASE_URL` | URL base OpenAI-compatible del ChatBot; por defecto la de Gemini (`https://generativelanguage.googleapis.com/v1beta/openai/`). |
| `CHATBOT_MODEL` | Modelo de chat; por defecto `gemini-2.5-flash`. |
| `LIBRARY_CONTEXT_DIR` | Carpeta con la base de conocimiento institucional; por defecto `./Entrevistas_Contexto`. |
| `TROCR_MODEL` | Modelo local de respaldo en Hugging Face; por defecto `microsoft/trocr-base-handwritten`. |

La primera lectura con TrOCR puede tardar más porque carga el modelo en memoria. El reconocimiento es una **propuesta**: los rangos repetidos, ilegibles o ambiguos deben corregirse antes de guardar, igual que con la importación por CSV.

### Formatos y errores

- Formatos admitidos: PNG, JPG, TIFF, BMP, WEBP, GIF y, si `pillow-heif` está instalado, HEIC/HEIF y AVIF (fotos de celular). Consultá la lista en `GET /api/ocr/status` (campo `formats`).
- Formato no reconocido o imagen ilegible: `422` con un mensaje concreto en el formulario.
- Archivo que no es imagen: `400`. Imagen mayor a 20 MB: `413`.
- Modelo o dependencias ausentes en el servidor: `503` (`GET /api/ocr/status` devuelve `available: false` y el detalle); el panel avisa que hay que recompilar la API (`docker compose build --no-cache api && docker compose up -d`).
- Si la API no responde, el panel muestra "No se pudo conectar con el servidor" en lugar del error crudo del navegador, y recuerda abrir el panel desde `http://localhost:8000/`.

## ChatBot del inventario

En el menú principal, debajo de **Inventario**, está **ChatBot**: un asistente que responde preguntas sobre el catálogo y sobre el funcionamiento interno de la biblioteca en lenguaje natural. Antes de cada consulta, la API arma un **resumen** del inventario (registros, ejemplares y rango de inventario) y un listado compacto de las fichas (`INV-000062 | «Título» | Autor | Pie de imprenta | Encuadernación`). Ese contexto más la **base de conocimiento institucional** se envían a un proveedor OpenAI-compatible —por defecto **Google Gemini** con `gemini-2.5-flash`— y el asistente responde **solo con esos datos**.

- **Formato:** el asistente responde en Markdown con un tono humano; el panel lo **renderiza** (negritas, viñetas y **tablas**), así que para listar varios libros muestra una tabla con `Nº inv.` y `Título`.
- **Inventario:** responde por título, autor, pie de imprenta o número de inventario; también puede contar registros y ejemplares.
- **Institucional:** responde sobre préstamos, asociación, sanciones, horarios, computadoras y criterios internos, a partir de las entrevistas de la carpeta [`Entrevistas_Contexto/`](./Entrevistas_Contexto/).
- Si el libro **no está en el catálogo**, lo dice y sugiere revisar el título o el autor. Si la pregunta **no está en ninguna fuente** o es confusa, pide que la reformulen en lugar de adivinar.
- No inventa disponibilidad ni préstamos: del ejemplar solo se conoce la encuadernación u observaciones. Si las normas internas y el inventario se contradicen sobre disponibilidad, prioriza el inventario.
- La conversación recentísima se envía como historial para dar contexto, y se mantiene **en el navegador**: al recargar o pulsar **Limpiar chat** se reinicia.
- Si falta la API key (`CHATBOT_API_KEY` o `DEEPSEEK_API_KEY`), `GET /api/chat/status` devuelve `available: false` y el panel lo avisa.
- Por defecto el ChatBot usa **Google Gemini** (`gemini-2.5-flash`): pegá tu clave `AIza…` en `CHATBOT_API_KEY`. El OCR sigue usando DeepSeek.
- **Respaldo automático:** si el proveedor principal se queda **sin tokens o saldo** (cuota agotada, `429`, `402`, `403` o fallo de red), el ChatBot reintenta en el acto con **DeepSeek** usando `DEEPSEEK_API_KEY`; el campo `model` de la respuesta indica qué proveedor contestó. `GET /api/chat/status` informa el respaldo en `fallback_model`.
- El ChatBot puede usar **cualquier proveedor compatible con OpenAI** cambiando `CHATBOT_API_KEY`, `CHATBOT_BASE_URL` y `CHATBOT_MODEL`. Si dejás `CHATBOT_*` en blanco, usa DeepSeek con `deepseek-chat`.

### Base de conocimiento institucional

Todo archivo `.md`, `.markdown` o `.txt` que esté en la carpeta `Entrevistas_Contexto/` se concatena y se envía como contexto estable (lo que además aprovecha el caché de prompt del proveedor). Para sumar una nueva entrevista o normativa, dejá el archivo en esa carpeta (por ejemplo `EntrevistaV2.md`): se toma automáticamente al reiniciar la API, sin cambios de código. El directorio se puede cambiar con la variable `LIBRARY_CONTEXT_DIR`.

`GET /api/chat/status` informa `context_files` con la cantidad de archivos cargados y `fallback_model` con el respaldo DeepSeek (si está configurado). `POST /api/chat` recibe `{ "message": "...", "history": [{ "role": "user|assistant", "content": "..." }] }` y devuelve `{ "reply": "...", "model": "gemini-2.5-flash" }`; si el principal se queda sin tokens, `model` será `deepseek-chat`. El asistente **no modifica** la base: solo lee el catálogo y el contexto.

> Nota: la ficha institucional V1 menciona que el catálogo no está digitalizado. En este sistema, la disponibilidad se responde con el inventario en vivo, que es la fuente prioritaria.

## Formato del CSV

El importador reconoce la fila de encabezados aunque antes haya filas de título, resumen o metadatos. Espera un CSV separado por comas, con campos entre comillas cuando contienen comas, y estas columnas:

| Columna | Uso en el panel |
| --- | --- |
| `N° Inv.` | Número único o rango de inventario, por ejemplo `62` o `62-71`. |
| `Autor` | Autor o autores tal como aparecen en el registro. |
| `Título` | Título bibliográfico. |
| `Pie de Imprenta` | Lugar, editorial y año, como texto libre. |
| `Encuadernación / Observaciones` | Encuadernación y notas heredadas del catálogo. No representa un estado de préstamo. |

El CSV actual tiene **195 registros bibliográficos** y **751 números de inventario**.

### Normalización de números

El panel conserva cada número de ejemplar como entero y lo representa con el prefijo `INV-` y seis dígitos:

| Valor en el CSV | Números asignados | Cantidad |
| --- | --- | ---: |
| `62` | `INV-000062` | 1 |
| `62-71` | `INV-000062` a `INV-000071` | 10 |
| `62–71` | Igual que el rango anterior | 10 |

Los extremos del rango son inclusivos. Un número no puede pertenecer a más de un registro. El panel conserva los números individuales, aunque en la tabla compacta visualmente los rangos consecutivos; la columna «Ejemplares» indica cuántos números individuales contiene.

Al exportar, el panel escribe los rangos con prefijos normalizados, por ejemplo `INV-000062-INV-000071`. La exportación incluye el filtro de búsqueda activo; borralo antes de exportar el catálogo completo.

## Importación y reemplazos

1. Elegí **Importar CSV** y seleccioná el archivo.
2. Aparece un cuadro **en pantalla** (no el diálogo del navegador) para elegir qué hacer:
   - **Reemplazar todo** (`mode=replace`): borra el catálogo actual e importa el CSV completo.
   - **Ignorar duplicados** (`mode=merge`): conserva lo existente y agrega solo los registros cuyos números de inventario **no** están ya en el catálogo; las filas con números repetidos se omiten y se informa cuántas.
   - **Cancelar**: no cambia nada.
3. FastAPI busca la fila de encabezados y valida los registros y sus números.
4. Si la validación falla, MySQL conserva el catálogo actual. La carga se ejecuta en una transacción; cualquier error revierte todo.
5. En `replace`, la API exige un campo explícito de confirmación si MySQL ya tiene registros; en `merge` no hace falta porque nunca borra datos.

Las confirmaciones de acciones destructivas (eliminar, vaciar catálogo o papelera) también usan un cuadro **en pantalla**, no el `confirm` nativo del navegador.

Agregar y editar valida los campos obligatorios y evita asignar números ya utilizados por otro registro **activo** (los que están en la papelera no cuentan). Al editar un rango, los números anteriores de ese registro se sustituyen por los nuevos; confirmá que el rango sea el correcto antes de guardar. Eliminar un registro lo mueve a la papelera (ver abajo); el borrado definitivo elimina en cascada sus ejemplares.

## Papelera y borrado

Eliminar un registro (el menú de cada fila o el botón **Vaciar catálogo**) ya no lo borra de inmediato: lo **mueve a la papelera**. El botón **Papelera** de la barra del panel abre la lista de registros eliminados, donde podés **restaurarlos** al catálogo o **borrarlos definitivamente**. El botón **Vaciar papelera** purga todo lo que quedó allí.

- Los registros de la papelera no aparecen en el catálogo ni cuentan para los totales.
- Mientras un registro está en la papelera, sus números de inventario se pueden **reutilizar** en un registro nuevo.
- Al **restaurar**, si alguno de sus números ya fue asignado a un registro activo, la API responde `409` y no restaura.
- El **borrado definitivo** (purga individual o vaciar papelera) no se puede deshacer.

### Vaciar el catálogo para pruebas

Al testear el panel suele hacer falta empezar de cero. El botón **Vaciar catálogo** mueve todo a la papelera en una sola operación; después se puede restaurar o purgar desde la **Papelera**. Por API:

```powershell
curl.exe -X DELETE http://localhost:8000/api/books        # mover todo a la papelera
curl.exe -X DELETE http://localhost:8000/api/trash        # borrar la papelera definitivamente
```

La respuesta informa qué se movió, por ejemplo `{ "deleted_records": 195, "deleted_copies": 751 }`. Si preferís resetear también la estructura y el volumen de MySQL:

```powershell
docker compose down -v
docker compose up --build
```

**No uses `down -v` si querés conservar datos**: elimina el volumen `mysql_data` completo.

## Actividad reciente

Debajo del perfil del bibliotecario, al pie de la barra lateral, el botón **Actividad reciente** abre un **popup** con los últimos movimientos (agregar, editar, importar CSV, escanear OCR, mover a la papelera, restaurar, borrar definitivo, etc.). Cada línea trae un punto de color según el tipo de evento —verde (ok), ámbar (advertencia), rojo (error) o azul (información)—, el texto formateado y la hora.

- Se guarda en el navegador (`localStorage`, clave `biblioteca-actividad`) y conserva hasta 60 entradas; el botón **Limpiar** vacía la lista.
- Es solo informativo y local: no reemplaza un registro de auditoría en MySQL ni incluye usuario, fila de origen ni descarga de informe (ver **próximos pasos**).

## Escenarios y manejo actual

| Escenario | Comportamiento actual | Qué revisar |
| --- | --- | --- |
| El CSV trae filas de título y estadísticas antes del encabezado | Se ignoran para localizar la tabla; se importan las filas bajo los encabezados reconocidos. | Si cambian los nombres de columnas o el delimitador, el formato tendrá que adaptarse. |
| Un registro tiene `62-71` | Se carga como diez números individuales, asociados al mismo registro. | El panel no crea diez fichas bibliográficas duplicadas. |
| Dos filas asignan el mismo número, incluso si una lo incluye dentro de un rango | Se rechaza la importación completa y se informa el número repetido. | Corregí el conflicto en el Excel/CSV y volvé a importar. |
| Un rango es descendente (`71-62`), empieza en cero, no es numérico o tiene más de 10.000 números | Se rechaza la importación completa. | Corregí el valor; los rangos deben ser ascendentes, mayores que cero y acotados. |
| Faltan número, título o autor | Se informa el error de fila y no se reemplazan los datos actuales. | Completá el registro en el archivo fuente. |
| El archivo está mal formado o tiene comillas sin cerrar | Se rechaza la importación. | Revisá el CSV exportado por la planilla y volvé a guardarlo en formato CSV. |
| Se confirma una importación mientras ya hay registros cargados | Se reemplaza el catálogo completo de MySQL en una transacción; no se mezclan filas ni se hace una actualización por título. | Exportá una copia antes si necesitás conservar el estado anterior. |
| Se cancela la confirmación | No se cambia el catálogo. | No hace falta restaurar datos. |
| Se edita el rango de un registro | Se reemplaza su conjunto de números; la validación impide conflictos con otros registros. | Una edición incorrecta podría liberar números que estaban asignados a ese registro. |
| La imagen escaneada contiene varias fichas (multiregistro) | El OCR devuelve una propuesta por fila y el panel abre **Registros detectados**; al confirmar se guardan juntas con `POST /api/books/bulk`. | Corregí cada fila; si hay números repetidos **entre filas**, se rechaza el lote. |
| Una o más filas del escaneo usan números ya asignados en el catálogo | Las filas en conflicto se **resaltan** y el panel pregunta qué hacer: **Omitir duplicados y guardar** (`on_conflict=skip`, guarda solo las filas nuevas e informa cuántas se omitieron) o **Cancelar y corregir**. | Corregí los números y volvé a guardar, o elegí omitir si querés cargar solo lo nuevo. |
| Se escanea una imagen con un formato distinto al esperado | Si no hay filas con separadores `\|`, el intérprete cae al modo de un solo registro. | Revisá la propuesta; si vino mal, cargá los campos a mano o mejorá la toma. |
| Se elimina un registro | Se mueve a la papelera; deja de contar en el catálogo. | Se puede restaurar o borrar definitivamente desde **Papelera**. |
| Se exporta con búsqueda o filtro activo | Solo se exportan las filas visibles. | Quitá filtros para obtener el inventario completo. |
| Se abre el panel en otro navegador o dispositivo | La colección aparece desde MySQL mientras se pueda conectar a la PC/servidor que aloja la API. | La base de esta configuración solo publica los puertos en `127.0.0.1`; no es accesible desde otros dispositivos de la red. |
| Se editan datos desde dos pestañas abiertas | Cada operación se valida contra MySQL; los números repetidos se rechazan. No hay versionado de registros, así que dos ediciones simultáneas del mismo registro pueden pisarse. | Es una configuración de desarrollo para una sola persona, no de operación concurrente. |
| El navegador borra los datos del sitio | El catálogo no se borra porque está en MySQL; solo se pierden la preferencia de tema y la **actividad reciente**. | Conservá backups de MySQL/exportaciones CSV para proteger la colección. |
| MySQL o FastAPI no está disponible | El panel informa la falta de conexión y no puede leer ni guardar. | Verificá `docker compose ps` y `docker compose logs api db`. |
| Se detienen los contenedores | Los datos se mantienen en el volumen `mysql_data`. | No uses `docker compose down -v` si querés conservarlos. |

## Ambigüedades del inventario fuente

El CSV agrupa varios números bajo un mismo registro bibliográfico. Esto modela un título con varios ejemplares numerados, pero no contiene datos individuales de cada copia (condición, ubicación, préstamos o fecha de alta). Por eso:

- `Encuadernación / Observaciones` se mantiene como texto y no se interpreta automáticamente como disponibilidad, baja o préstamo.
- Un rango como `3452-3461` significa diez ejemplares asignados al mismo registro, aunque la descripción mencione varios títulos u obras. El panel no puede deducir qué número corresponde a cada título por separado.
- La normalización agrega `INV-` y ceros a la izquierda para identificación y búsqueda; no renumera ni cambia el valor numérico.
- Si el rango fuente estuviera equivocado, corregirlo en el Excel/CSV antes de reimportar evita que la interfaz consolide una asignación incorrecta.

## Modelo de datos y próximos pasos

1. **Modelo separado:** ya se separa `BibliographicRecord` de `InventoryCopy`. Un registro bibliográfico guarda autor, título, pie de imprenta, observaciones generales y `deleted_at` (papelera); cada ejemplar guarda un número de inventario único entre los registros activos.
2. **Importación no destructiva:** la primera API implementa reemplazo total explícito y atómico. Como mejora, ofrecer una vista previa con conteo de altas, cambios, bajas y conflictos antes de confirmar, además de actualización por número de inventario.
3. **Conflictos de identidad:** usar el número de inventario como identificador único del ejemplar. No fusionar registros por título o autor automáticamente: esos campos pueden repetirse o variar en su escritura.
4. **Trazabilidad:** guardar archivo, fecha, usuario, fila de origen, número original y resultado de cada importación; permitir descargar un informe de filas omitidas o conflictivas.
5. **Transacciones y concurrencia:** validar y aplicar una importación como operación atómica; usar transacciones y control de versión para que dos administradores no se pisen.
6. **Respaldo y permisos:** definir autenticación, roles, exportaciones de respaldo y políticas de recuperación antes de exponer la API a una red o usar el sistema como inventario oficial. Esta versión no incluye login.
7. **OCR de imágenes escaneadas:** implementado como propuesta (`/api/ocr/scan`); muestra la imagen y los campos reconocidos para corregir rangos repetidos, ilegibles o ambiguos antes de persistirlos.

## Archivos de la interfaz

- [`index.html`](./index.html): estructura y formulario.
- [`styles.css`](./styles.css): estilos y temas claro/oscuro.
- [`app.js`](./app.js): operaciones CRUD, validación, CSV, OCR, ChatBot y llamadas a la API.
- [`backend/app/`](./backend/app/): API FastAPI, modelos, validación, importación, OCR y ChatBot.
- [`backend/migrations/`](./backend/migrations/): migraciones versionadas con Alembic.
- [`backend/tests/`](./backend/tests/): pruebas de importación, API y migraciones.
- [`Entrevistas_Contexto/`](./Entrevistas_Contexto/): base de conocimiento institucional (entrevistas y normativas) que alimenta al ChatBot.
- [`backend/requirements.txt`](./backend/requirements.txt): dependencias de la API, instaladas durante la construcción de la imagen Docker.
- [`.env.example`](./.env.example): plantilla de variables; copiá a `.env` y reemplazá las contraseñas locales.
- [`docker-compose.yml`](./docker-compose.yml): MySQL y API para ejecutarse con Docker Compose.
